"""
HarnessBuilder — wires a complete harness environment in one call.

Replaces the scattered build_agent() patterns in individual examples
with a single, consistent builder that:
  1. Loads manifest + policy
  2. Wires SandboxApprover + ShadowAuditSink
  3. Connects FixtureLoader data as a MockGatewayConnector
  4. Instantiates the agent class
  5. Returns the agent ready to run

Usage:
    from arc.harness import HarnessBuilder
    from examples.email_triage.agent import EmailTriageAgent

    agent = (
        HarnessBuilder(
            manifest="examples/email_triage/manifest.yaml",
            policy="examples/email_triage/policy.yaml",
        )
        .with_fixtures("examples/email_triage/fixtures/emails.yaml")
        .build(EmailTriageAgent)
    )

    # Run the agent
    results = await agent.execute(run_id="poc-001")

    # Get the harness report
    report = agent.harness_report()
    report.print()

Production swap:
    Replace HarnessBuilder with RuntimeBuilder and supply real
    connector configs via RuntimeConfig. No changes to the agent class.
"""

from pathlib import Path
from typing import Any, Type, TypeVar

from arc.core import BaseAgent, LLMClient, LLMConfig, load_manifest, resolve_llm
from arc.core.observability import OutcomeTracker
from tollgate import ControlTower, YamlPolicyEvaluator

from .approver import SandboxApprover
from .fixtures import FixtureLoader
from .report import DecisionReport
from .shadow import ShadowAuditSink

AgentT = TypeVar("AgentT", bound=BaseAgent)


class HarnessBuilder:
    """
    Fluent builder for harness-mode agent instances.

    All components are wired for local/sandbox use:
    - Policy evaluated against YAML rules
    - All ASK decisions auto-approved by SandboxApprover
    - All decisions captured by ShadowAuditSink
    - Data served from FixtureLoader (MockGatewayConnector)
    """

    def __init__(
        self,
        manifest: str | Path,
        policy:   str | Path,
        *,
        llm_config: LLMConfig | None = None,
    ):
        """
        Args:
            manifest:   Path to the agent's manifest.yaml
            policy:     Path to the agent's policy.yaml
            llm_config: Optional platform-default LLM config. If set, the
                        builder constructs an LLMClient and injects it
                        unless the manifest overrides or ``with_llm()`` is
                        called explicitly. ``LLMConfig.from_env()`` reads
                        ``ARC_LLM_PROVIDER`` / ``ARC_LLM_MODEL`` / etc.
        """
        self._manifest_path = Path(manifest)
        self._policy_path   = Path(policy)
        self._fixtures      = FixtureLoader.empty()
        self._tracker_path: str | None = None
        self._orchestrator: Any = None
        self._extra_kwargs: dict = {}
        self._llm_config: LLMConfig | None = llm_config
        self._llm_explicit: LLMClient | None = None

    # ── Fluent configuration ──────────────────────────────────────────────

    def with_fixtures(self, path: str | Path) -> "HarnessBuilder":
        """Load fixture data from a YAML or JSON file."""
        self._fixtures = FixtureLoader(path)
        return self

    def with_fixture_data(self, source: str, data: Any) -> "HarnessBuilder":
        """Add a single source's fixture data inline."""
        self._fixtures.add(source, data)
        return self

    def with_fixture_dict(self, data: dict[str, Any]) -> "HarnessBuilder":
        """Load fixture data from an inline dict."""
        self._fixtures = FixtureLoader.from_dict(data)
        return self

    def with_tracker(self, path: str) -> "HarnessBuilder":
        """Enable outcome tracking and write to this JSONL path."""
        self._tracker_path = path
        return self

    def with_orchestrator(self, orchestrator: Any) -> "HarnessBuilder":
        """
        Inject an orchestrator (LangGraphOrchestrator, AgentCoreOrchestrator, etc.).

        The orchestrator is stored and injected into the agent as
        self.orchestrator after construction. Enables full LangGraph
        pipeline testing in harness mode using MockBedrockLLM.

        Args:
            orchestrator: Any object implementing OrchestratorProtocol.

        Returns:
            self (fluent interface)
        """
        self._orchestrator = orchestrator
        return self

    def with_kwargs(self, **kwargs) -> "HarnessBuilder":
        """Pass extra keyword arguments to the agent constructor."""
        self._extra_kwargs.update(kwargs)
        return self

    def with_llm(self, llm: LLMClient) -> "HarnessBuilder":
        """Inject a pre-built LLMClient (highest precedence).

        Overrides both the manifest's ``llm:`` block and the platform
        default supplied via ``llm_config=``. Use for tests + one-off
        scripts that want to wire a fake or specific client without
        touching the manifest.
        """
        self._llm_explicit = llm
        return self

    # ── Build ─────────────────────────────────────────────────────────────

    def build(self, agent_cls: Type[AgentT]) -> AgentT:
        """
        Instantiate the agent class wired for harness mode.

        The returned agent has a .harness_report() method injected
        that returns a DecisionReport after execute() completes.

        Args:
            agent_cls: The BaseAgent subclass to instantiate.

        Returns:
            An instance of agent_cls ready to run in harness mode.
        """
        manifest = load_manifest(self._manifest_path)
        approver = SandboxApprover()
        audit    = ShadowAuditSink()
        policy   = YamlPolicyEvaluator(self._policy_path)
        tower    = ControlTower(policy=policy, approver=approver, audit=audit)
        gateway  = self._fixtures.to_gateway()
        tracker  = OutcomeTracker(path=self._tracker_path) if self._tracker_path else None

        # Resolve LLMClient by precedence:
        #   explicit with_llm()  >  manifest.llm  >  platform llm_config  >  None
        llm = resolve_llm(
            explicit         = self._llm_explicit,
            manifest_config  = manifest.llm,
            platform_default = self._llm_config,
        )

        kwargs = dict(self._extra_kwargs)
        if self._orchestrator is not None:
            kwargs["orchestrator"] = self._orchestrator
        if llm is not None:
            kwargs.setdefault("llm", llm)

        agent = agent_cls(
            manifest=manifest,
            tower=tower,
            gateway=gateway,
            tracker=tracker,
            **kwargs,
        )

        # Inject orchestrator as attribute (also accessible directly)
        if self._orchestrator is not None:
            agent.orchestrator = self._orchestrator  # type: ignore[attr-defined]

        # Inject harness_report() as a bound method on the instance
        def harness_report() -> DecisionReport:
            return DecisionReport(
                audit=audit,
                approver=approver,
                agent_id=manifest.agent_id,
            )

        agent.harness_report = harness_report  # type: ignore[attr-defined]

        # Expose the shadow sink directly for programmatic access
        agent._harness_audit    = audit    # type: ignore[attr-defined]
        agent._harness_approver = approver # type: ignore[attr-defined]

        return agent

    # ── Convenience: build and run in one call ────────────────────────────

    async def run(
        self,
        agent_cls: Type[AgentT],
        **execute_kwargs,
    ) -> DecisionReport:
        """
        Build the agent, call execute(**execute_kwargs), and return
        the DecisionReport.

        Usage:
            report = await HarnessBuilder(...).with_fixtures(...).run(
                EmailTriageAgent,
                email_ids=["e-001", "e-002"],
            )
            report.print()
        """
        agent = self.build(agent_cls)
        await agent.execute(**execute_kwargs)
        return agent.harness_report()  # type: ignore[operator]
