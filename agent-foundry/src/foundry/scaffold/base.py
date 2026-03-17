"""
BaseAgent — abstract base class for all agents incubated in Foundry.

Every agent built on Foundry inherits from BaseAgent. This guarantees:
  - All tool calls pass through the ControlTower (policy enforcement)
  - All effects are declared in the manifest before they are invoked
  - All decisions are logged to the audit trail
  - Sandbox and production environments are strictly separated

Usage:
    class RetirementTrajectoryAgent(BaseAgent):
        async def execute(self, **kwargs) -> dict:
            # 1. Fetch data via Gateway
            data = await self.gateway.fetch("participant.data", {...})

            # 2. Run computation (ALLOW by default)
            score = await self.run_effect(
                effect=FinancialEffect.RISK_SCORE_COMPUTE,
                tool="scorer", action="compute",
                params={"participant_id": data["id"]},
                intent_action="score_trajectory",
                intent_reason="Identify at-risk participants for intervention",
            )

            # 3. Draft intervention (ALLOW — internal)
            draft = await self.run_effect(
                effect=FinancialEffect.INTERVENTION_DRAFT,
                tool="generator", action="draft",
                params={"score": score, "participant_id": data["id"]},
                intent_action="draft_intervention",
                intent_reason="Generate personalized intervention message",
            )

            # 4. Send (ASK by default — may require human approval)
            await self.run_effect(
                effect=FinancialEffect.PARTICIPANT_COMMUNICATION_SEND,
                tool="email_gateway", action="send",
                params={"participant_id": data["id"], "content": draft},
                intent_action="send_intervention",
                intent_reason="Deliver personalized retirement intervention",
            )
"""

import logging
from abc import ABC, abstractmethod
from typing import Any

from foundry.gateway.base import GatewayConnector
from foundry.observability.tracker import OutcomeTracker
from foundry.policy.builder import EffectRequestBuilder
from foundry.policy.effects import FinancialEffect
from foundry.scaffold.manifest import AgentManifest
from foundry.tollgate.tower import ControlTower
from foundry.tollgate.types import AgentContext

logger = logging.getLogger(__name__)


class BaseAgent(ABC):
    """
    Abstract base class for all Foundry-incubated agents.

    Subclasses must implement `execute()`. All tool calls must go
    through `run_effect()` to ensure policy enforcement and audit logging.
    """

    def __init__(
        self,
        manifest: AgentManifest,
        tower: ControlTower,
        gateway: GatewayConnector,
        tracker: OutcomeTracker | None = None,
    ):
        """
        Args:
            manifest: The agent's declared manifest (scope, effects, stage).
            tower:    Configured Tollgate ControlTower (policy + audit).
            gateway:  Data access connector (reads data via declared permissions).
            tracker:  Optional outcome tracker for ROI measurement.
        """
        self.manifest = manifest
        self.tower = tower
        self.gateway = gateway
        self.tracker = tracker
        self._builder = EffectRequestBuilder(manifest_version=manifest.manifest_version)
        self._agent_ctx = AgentContext(
            agent_id=manifest.agent_id,
            version=manifest.version,
            owner=manifest.owner,
            metadata={
                "environment": manifest.environment,
                "lifecycle_stage": manifest.lifecycle_stage.value,
                "tags": manifest.tags,
            },
        )

    @abstractmethod
    async def execute(self, **kwargs: Any) -> Any:
        """
        Main agent logic. Implement this in your agent subclass.
        All tool calls must go through self.run_effect().
        """
        ...

    async def run_effect(
        self,
        effect: FinancialEffect,
        tool: str,
        action: str,
        params: dict[str, Any],
        intent_action: str,
        intent_reason: str,
        confidence: float | None = None,
        metadata: dict[str, Any] | None = None,
        exec_fn: Any = None,
    ) -> Any:
        """
        Execute a tool call through the ControlTower policy engine.

        This is the only permitted way for agents to take actions.
        The effect must be declared in the agent's manifest.

        Args:
            effect:        The FinancialEffect being invoked.
            tool:          Tool name (e.g., "email_gateway").
            action:        Specific action (e.g., "send").
            params:        Tool parameters.
            intent_action: Short intent descriptor (e.g., "send_intervention").
            intent_reason: Human-readable reason for the action.
            confidence:    Optional confidence score (0.0 – 1.0).
            metadata:      Extra metadata for policy `when:` conditions.
            exec_fn:       Async callable to execute. If None, returns params.

        Raises:
            PermissionError: If the effect is not in this agent's manifest.
            TollgateDenied:  If the policy engine denies the request.
            TollgateDeferred: If the request is queued for human approval.
        """
        # Kill switch: suspended agents are completely blocked
        if not self.manifest.is_active:
            raise PermissionError(
                f"Agent '{self.manifest.agent_id}' is {self.manifest.status.value}. "
                f"Update the manifest status to 'active' to resume operations."
            )

        # Guard: effect must be declared in manifest
        if not self.manifest.allows_effect(effect):
            raise PermissionError(
                f"Agent '{self.manifest.agent_id}' attempted undeclared effect "
                f"'{effect.value}'. Add it to allowed_effects in the manifest."
            )

        # Guard: no production actions from sandbox agents
        if self.manifest.is_sandbox and effect.value.startswith("agent.promote"):
            raise PermissionError(
                "Sandbox agents cannot trigger agent.promote. "
                "Promotion is handled by the lifecycle manager."
            )

        tool_request = self._builder.build(
            effect=effect,
            tool=tool,
            action=action,
            params=params,
            metadata=metadata,
        )

        intent = self._builder.intent(
            action=intent_action,
            reason=intent_reason,
            confidence=confidence,
        )

        # Default exec: return params (useful for draft/log effects)
        if exec_fn is None:
            async def exec_fn():
                return params

        result = await self.tower.execute_async(
            agent_ctx=self._agent_ctx,
            intent=intent,
            tool_request=tool_request,
            exec_async=exec_fn,
        )

        logger.debug(
            "effect=%s tool=%s action=%s agent=%s env=%s",
            effect.value, tool, action,
            self.manifest.agent_id, self.manifest.environment,
        )

        return result

    async def log_outcome(self, event_type: str, data: dict[str, Any]) -> None:
        """Record an outcome event for ROI tracking."""
        if self.tracker:
            await self.tracker.record(
                agent_id=self.manifest.agent_id,
                event_type=event_type,
                data=data,
            )
