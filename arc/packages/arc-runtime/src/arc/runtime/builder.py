"""
RuntimeBuilder — production-mode agent factory.

Reads TICKET_TARGET env var and instantiates the correct connector
(PegaCaseConnector or ServiceNowConnector). Wires all Arc components
into a production-ready agent instance.

Contrast with HarnessBuilder (foundry.harness) — same fluent interface,
real connectors instead of fixture mocks.

Usage:
    from arc.runtime.builder import RuntimeBuilder
    from arc.runtime.config import RuntimeConfig
    from arc.agents.email_triage.agent import EmailTriageAgent

    config = RuntimeConfig.from_env()
    agent = (
        RuntimeBuilder(
            config=config,
            manifest=Path("manifest.yaml"),
            policy=Path("policy.yaml"),
        )
        .with_orchestrator(LangGraphOrchestrator(graph=my_graph))
        .build(EmailTriageAgent)
    )
    await agent.execute()

Swap from harness to production:
    Replace HarnessBuilder with RuntimeBuilder and supply real
    RuntimeConfig. No changes to the agent class.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Type, TypeVar

logger = logging.getLogger(__name__)

AgentT = TypeVar("AgentT")


class RuntimeBuilder:
    """
    Fluent builder for production-mode Arc agent instances.

    Reads TICKET_TARGET=pega|servicenow to select the active ITSM connector.
    All other connectors (Outlook, Pega Knowledge) are instantiated when
    their config is present in RuntimeConfig.
    """

    def __init__(
        self,
        config: Any,               # RuntimeConfig
        manifest: str | Path,
        policy: str | Path,
    ):
        """
        Args:
            config:   RuntimeConfig instance (from RuntimeConfig.from_env()).
            manifest: Path to the agent's manifest.yaml.
            policy:   Path to the agent's policy.yaml.
        """
        self._config        = config
        self._manifest_path = Path(manifest)
        self._policy_path   = Path(policy)
        self._orchestrator  = None
        self._extra_kwargs: dict = {}

    # ── Fluent configuration ──────────────────────────────────────────────────

    def with_orchestrator(self, orchestrator: Any) -> "RuntimeBuilder":
        """
        Inject an orchestrator (LangGraphOrchestrator, AgentCoreOrchestrator, etc.).

        The orchestrator is injected into the agent as self.orchestrator.
        If not set, the agent runs in direct (non-orchestrated) mode.
        """
        self._orchestrator = orchestrator
        return self

    def with_kwargs(self, **kwargs) -> "RuntimeBuilder":
        """Pass extra keyword arguments to the agent constructor."""
        self._extra_kwargs.update(kwargs)
        return self

    # ── Build ─────────────────────────────────────────────────────────────────

    def build(self, agent_cls: Type[AgentT]) -> AgentT:
        """
        Instantiate the agent class wired for production mode.

        Reads TICKET_TARGET env var (default "pega") to select the
        active ITSM connector. Validates that required configs are present
        before building — fails fast with a clear error.

        Args:
            agent_cls: The BaseAgent subclass to instantiate.

        Returns:
            An instance of agent_cls ready to run in production.
        """
        from arc.core import load_manifest
        from tollgate import ControlTower, YamlPolicyEvaluator, JsonlAuditSink
        from arc.core.gateway import MultiGateway, MockGatewayConnector

        ticket_target = os.getenv("TICKET_TARGET", "pega").lower()
        logger.info("RuntimeBuilder: TICKET_TARGET=%s", ticket_target)

        # ── Build ITSM ticket connector ───────────────────────────────────────
        ticket_connector = self._build_ticket_connector(ticket_target)

        # ── Build Outlook connector (optional) ────────────────────────────────
        outlook_connector = self._build_outlook_connector()

        # ── Build Pega Knowledge connector (optional) ─────────────────────────
        kb_connector = self._build_kb_connector()

        # ── Assemble MultiGateway ─────────────────────────────────────────────
        gateway = self._build_gateway(ticket_connector, outlook_connector, kb_connector)

        # ── Build audit sink ──────────────────────────────────────────────────
        audit = self._build_audit_sink()

        # ── Build approver ────────────────────────────────────────────────────
        approver = self._build_approver()

        # ── Load manifest + policy ────────────────────────────────────────────
        manifest = load_manifest(self._manifest_path)
        policy   = YamlPolicyEvaluator(self._policy_path)
        tower    = ControlTower(policy=policy, approver=approver, audit=audit)

        # ── Instantiate agent ──────────────────────────────────────────────────
        kwargs = dict(self._extra_kwargs)
        if self._orchestrator is not None:
            kwargs["orchestrator"] = self._orchestrator

        agent = agent_cls(
            manifest=manifest,
            tower=tower,
            gateway=gateway,
            **kwargs,
        )

        if self._orchestrator is not None:
            agent.orchestrator = self._orchestrator  # type: ignore[attr-defined]

        logger.info(
            "RuntimeBuilder: built %s (ticket_target=%s, orchestrator=%s)",
            agent_cls.__name__,
            ticket_target,
            type(self._orchestrator).__name__ if self._orchestrator else "None",
        )
        return agent

    # ── Internal builders ─────────────────────────────────────────────────────

    def _build_ticket_connector(self, ticket_target: str) -> Any:
        """Build the active ITSM ticket connector based on TICKET_TARGET."""
        if ticket_target == "pega":
            if self._config.pega_case is None:
                raise EnvironmentError(
                    "RuntimeBuilder: TICKET_TARGET=pega but PegaCaseConfig is not set. "
                    "Ensure PEGA_BASE_URL, PEGA_CLIENT_ID, PEGA_CLIENT_SECRET are set."
                )
            from arc.connectors.pega_case import PegaCaseConnector
            logger.info("RuntimeBuilder: using PegaCaseConnector")
            return PegaCaseConnector(self._config.pega_case)

        elif ticket_target == "servicenow":
            if self._config.servicenow is None:
                raise EnvironmentError(
                    "RuntimeBuilder: TICKET_TARGET=servicenow but ServiceNowConfig is not set. "
                    "Ensure SNOW_INSTANCE_URL, SNOW_CLIENT_ID, SNOW_CLIENT_SECRET are set."
                )
            from arc.connectors.servicenow import ServiceNowConnector
            logger.info("RuntimeBuilder: using ServiceNowConnector")
            return ServiceNowConnector(self._config.servicenow)

        else:
            raise ValueError(
                f"RuntimeBuilder: unknown TICKET_TARGET={ticket_target!r}. "
                "Valid values: 'pega', 'servicenow'."
            )

    def _build_outlook_connector(self) -> Any | None:
        """Build Outlook connector if config is present."""
        if self._config.outlook is None:
            logger.debug("RuntimeBuilder: Outlook not configured — skipping")
            return None
        from arc.connectors.outlook import OutlookConnector
        logger.info("RuntimeBuilder: using OutlookConnector")
        return OutlookConnector(self._config.outlook)

    def _build_kb_connector(self) -> Any | None:
        """Build Pega Knowledge connector if config is present."""
        if self._config.pega_knowledge is None:
            logger.debug("RuntimeBuilder: Pega Knowledge not configured — skipping")
            return None
        from arc.connectors.pega_knowledge import PegaKnowledgeConnector
        logger.info("RuntimeBuilder: using PegaKnowledgeConnector")
        return PegaKnowledgeConnector(self._config.pega_knowledge)

    def _build_gateway(
        self,
        ticket_connector: Any,
        outlook_connector: Any | None,
        kb_connector: Any | None,
    ) -> Any:
        """Assemble MultiGateway routing sources to connectors."""
        from arc.core.gateway import MultiGateway

        connectors: dict[str, Any] = {}

        # Route ticket.system → active ticket connector
        connectors["ticket.system"] = ticket_connector

        # Route email.inbox → Outlook connector (or mock if missing)
        if outlook_connector is not None:
            connectors["email.inbox"] = outlook_connector
            connectors["email.thread"] = outlook_connector
        else:
            logger.warning(
                "RuntimeBuilder: Outlook connector not available — "
                "email sources will return empty data"
            )

        # Route knowledge.buddy → KB connector (optional)
        if kb_connector is not None:
            connectors["knowledge.buddy"] = kb_connector

        return MultiGateway(connectors)

    def _build_audit_sink(self) -> Any:
        """Build audit sink based on config.audit_sink."""
        from tollgate import JsonlAuditSink

        sink_type = getattr(self._config, "audit_sink", "jsonl")
        audit_path = getattr(self._config, "audit_path", "arc_audit.jsonl")

        if sink_type == "jsonl":
            return JsonlAuditSink(path=audit_path)

        # TODO: CloudWatch and S3 sinks — fall back to JSONL for now
        logger.warning(
            "RuntimeBuilder: audit_sink=%r not yet implemented — using jsonl",
            sink_type,
        )
        return JsonlAuditSink(path=audit_path)

    def _build_approver(self) -> Any:
        """Build approver based on config.approver_mode."""
        from tollgate import AsyncQueueApprover, CliApprover

        mode = getattr(self._config, "approver_mode", "sqs")

        if mode == "cli":
            logger.info("RuntimeBuilder: using CliApprover (dev mode)")
            return CliApprover()

        if mode == "sqs":
            queue_url = getattr(self._config, "sqs_queue_url", "")
            if not queue_url:
                logger.warning(
                    "RuntimeBuilder: approver_mode=sqs but SQS_QUEUE_URL is empty — "
                    "falling back to CliApprover"
                )
                return CliApprover()
            return AsyncQueueApprover(queue_url=queue_url)

        # Fallback
        logger.warning(
            "RuntimeBuilder: unknown approver_mode=%r — using CliApprover",
            mode,
        )
        return CliApprover()
