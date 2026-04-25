"""
Tests for arc.core.agent.BaseAgent — native after migration module 4.
Foundry has equivalent coverage via the shim
(agent-foundry/tests/test_base_agent.py).

Validates that:
  - Kill switch blocks execution for suspended/deprecated agents
  - Undeclared effects raise PermissionError
  - sandbox agents cannot trigger agent.promote
  - run_effect passes through to exec_fn correctly
  - log_outcome records to tracker
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from arc.core.agent import BaseAgent
from arc.core.effects import FinancialEffect
from arc.core.manifest import AgentManifest, AgentStatus

from arc.core.lifecycle import LifecycleStage


# ─── Fixtures ─────────────────────────────────────────────────────────────────

def make_manifest(
    allowed_effects=None,
    environment="sandbox",
    status=AgentStatus.ACTIVE,
) -> AgentManifest:
    return AgentManifest(
        agent_id="test-agent",
        version="0.1.0",
        owner="test-team",
        description="Test agent",
        lifecycle_stage=LifecycleStage.BUILD,
        allowed_effects=allowed_effects or [
            FinancialEffect.PARTICIPANT_DATA_READ,
            FinancialEffect.RISK_SCORE_COMPUTE,
            FinancialEffect.AUDIT_LOG_WRITE,
        ],
        data_access=["participant.data"],
        policy_path="tests/policy.yaml",
        success_metrics=["Metric one"],
        environment=environment,
        status=status,
    )


def make_mock_tower():
    tower = MagicMock()
    tower.execute_async = AsyncMock(return_value={"result": "ok"})
    return tower


def make_mock_gateway():
    gateway = MagicMock()
    gateway.fetch = AsyncMock(return_value=MagicMock(data={}))
    return gateway


class ConcreteAgent(BaseAgent):
    """Minimal concrete agent for testing BaseAgent."""
    async def execute(self, **kwargs):
        return {"executed": True}


# ─── Kill Switch ──────────────────────────────────────────────────────────────

class TestKillSwitch:
    @pytest.mark.asyncio
    async def test_suspended_agent_raises_on_run_effect(self):
        manifest = make_manifest(status=AgentStatus.SUSPENDED)
        agent = ConcreteAgent(
            manifest=manifest,
            tower=make_mock_tower(),
            gateway=make_mock_gateway(),
        )
        with pytest.raises(PermissionError, match="suspended"):
            await agent.run_effect(
                effect=FinancialEffect.PARTICIPANT_DATA_READ,
                tool="gateway", action="read", params={},
                intent_action="read", intent_reason="test",
            )

    @pytest.mark.asyncio
    async def test_deprecated_agent_raises_on_run_effect(self):
        manifest = make_manifest(status=AgentStatus.DEPRECATED)
        agent = ConcreteAgent(
            manifest=manifest,
            tower=make_mock_tower(),
            gateway=make_mock_gateway(),
        )
        with pytest.raises(PermissionError, match="deprecated"):
            await agent.run_effect(
                effect=FinancialEffect.PARTICIPANT_DATA_READ,
                tool="gateway", action="read", params={},
                intent_action="read", intent_reason="test",
            )

    @pytest.mark.asyncio
    async def test_active_agent_can_run_declared_effect(self):
        manifest = make_manifest(status=AgentStatus.ACTIVE)
        tower = make_mock_tower()
        agent = ConcreteAgent(
            manifest=manifest, tower=tower, gateway=make_mock_gateway(),
        )
        result = await agent.run_effect(
            effect=FinancialEffect.PARTICIPANT_DATA_READ,
            tool="gateway", action="read", params={},
            intent_action="read", intent_reason="test",
        )
        assert tower.execute_async.called


# ─── Effect Permission Enforcement ───────────────────────────────────────────

class TestEffectPermissions:
    @pytest.mark.asyncio
    async def test_undeclared_effect_raises_permission_error(self):
        manifest = make_manifest(allowed_effects=[FinancialEffect.PARTICIPANT_DATA_READ])
        agent = ConcreteAgent(
            manifest=manifest,
            tower=make_mock_tower(),
            gateway=make_mock_gateway(),
        )
        with pytest.raises(PermissionError, match="undeclared effect"):
            await agent.run_effect(
                effect=FinancialEffect.PARTICIPANT_COMMUNICATION_SEND,  # Not in allowed_effects
                tool="email", action="send", params={},
                intent_action="send", intent_reason="test",
            )

    @pytest.mark.asyncio
    async def test_declared_effect_does_not_raise(self):
        effects = [FinancialEffect.PARTICIPANT_DATA_READ, FinancialEffect.RISK_SCORE_COMPUTE]
        manifest = make_manifest(allowed_effects=effects)
        tower = make_mock_tower()
        agent = ConcreteAgent(
            manifest=manifest, tower=tower, gateway=make_mock_gateway(),
        )
        # Should not raise
        await agent.run_effect(
            effect=FinancialEffect.RISK_SCORE_COMPUTE,
            tool="scorer", action="compute", params={},
            intent_action="compute", intent_reason="test",
        )
        assert tower.execute_async.called

    @pytest.mark.asyncio
    async def test_agent_promote_blocked_in_sandbox(self):
        manifest = make_manifest(
            allowed_effects=[FinancialEffect.AGENT_PROMOTE],
            environment="sandbox",
        )
        agent = ConcreteAgent(
            manifest=manifest, tower=make_mock_tower(), gateway=make_mock_gateway(),
        )
        with pytest.raises(PermissionError, match="Sandbox agents cannot trigger agent.promote"):
            await agent.run_effect(
                effect=FinancialEffect.AGENT_PROMOTE,
                tool="lifecycle", action="promote", params={},
                intent_action="promote", intent_reason="test",
            )


# ─── exec_fn Passthrough ─────────────────────────────────────────────────────

class TestExecFnPassthrough:
    @pytest.mark.asyncio
    async def test_exec_fn_result_returned(self):
        """When exec_fn is provided, its result should be returned."""
        manifest = make_manifest()
        tower = MagicMock()
        expected_result = {"score": 0.92, "at_risk": True}
        tower.execute_async = AsyncMock(return_value=expected_result)
        agent = ConcreteAgent(
            manifest=manifest, tower=tower, gateway=make_mock_gateway(),
        )
        result = await agent.run_effect(
            effect=FinancialEffect.RISK_SCORE_COMPUTE,
            tool="scorer", action="compute", params={"participant_id": "p-001"},
            intent_action="compute", intent_reason="test",
            exec_fn=lambda: expected_result,
        )
        assert result == expected_result

    @pytest.mark.asyncio
    async def test_default_exec_fn_returns_params(self):
        """When no exec_fn is provided, params are returned as default."""
        manifest = make_manifest()
        params = {"participant_id": "p-001", "query": "balance"}
        tower = MagicMock()
        tower.execute_async = AsyncMock(return_value=params)
        agent = ConcreteAgent(
            manifest=manifest, tower=tower, gateway=make_mock_gateway(),
        )
        result = await agent.run_effect(
            effect=FinancialEffect.PARTICIPANT_DATA_READ,
            tool="gateway", action="read", params=params,
            intent_action="read", intent_reason="test",
            exec_fn=None,
        )
        assert result == params


# ─── Tower Integration ────────────────────────────────────────────────────────

class TestTowerIntegration:
    @pytest.mark.asyncio
    async def test_tower_execute_async_is_called(self):
        manifest = make_manifest()
        tower = make_mock_tower()
        agent = ConcreteAgent(
            manifest=manifest, tower=tower, gateway=make_mock_gateway(),
        )
        await agent.run_effect(
            effect=FinancialEffect.PARTICIPANT_DATA_READ,
            tool="gateway", action="read", params={},
            intent_action="read", intent_reason="test",
        )
        assert tower.execute_async.call_count == 1

    @pytest.mark.asyncio
    async def test_agent_context_carries_manifest_metadata(self):
        manifest = make_manifest()
        agent = ConcreteAgent(
            manifest=manifest, tower=make_mock_tower(), gateway=make_mock_gateway(),
        )
        ctx = agent._agent_ctx
        assert ctx.agent_id == "test-agent"
        assert ctx.version == "0.1.0"
        assert ctx.owner == "test-team"


# ─── Outcome Tracking ────────────────────────────────────────────────────────

class TestOutcomeTracking:
    @pytest.mark.asyncio
    async def test_log_outcome_records_event(self):
        from arc.core.observability import OutcomeTracker
        tracker = OutcomeTracker()
        manifest = make_manifest()
        agent = ConcreteAgent(
            manifest=manifest,
            tower=make_mock_tower(),
            gateway=make_mock_gateway(),
            tracker=tracker,
        )
        await agent.log_outcome("intervention_sent", {"participant_id": "p-001"})
        events = tracker.events(event_type="intervention_sent")
        assert len(events) == 1
        assert events[0].data["participant_id"] == "p-001"

    @pytest.mark.asyncio
    async def test_log_outcome_no_tracker_is_safe(self):
        """Calling log_outcome with no tracker should not raise."""
        manifest = make_manifest()
        agent = ConcreteAgent(
            manifest=manifest,
            tower=make_mock_tower(),
            gateway=make_mock_gateway(),
            tracker=None,
        )
        # Should not raise even with no tracker
        await agent.log_outcome("test_event", {"key": "value"})
