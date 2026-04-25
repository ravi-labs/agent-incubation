"""
Tests for foundry.deploy.lambda_handler — make_handler and _FoundryLambdaHandler.

Covers:
  make_handler():
    - raises ValueError immediately if no gateway provided
    - returns handler object with .handler() method
    - agent is not initialised at make_handler() time (lazy cold-start)

  _FoundryLambdaHandler.handler():
    - cold start initialises agent on first invocation
    - warm invocation reuses existing agent (no re-init)
    - direct event kwargs passed through to agent.execute()
    - EventBridge event detail unpacked correctly
    - SQS Records body unpacked correctly
    - scheduled EventBridge (aws.events) passes empty kwargs
    - PermissionError returns 403 statusCode
    - unexpected Exception returns 500 statusCode
    - successful invocation returns 200 statusCode with result

  Event normalisation (_normalise_event):
    - direct dict passes through unchanged
    - EventBridge detail extracted
    - SQS Records body parsed and passed
    - scheduled aws.events returns empty dict
"""

import json
import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

from arc.runtime.deploy.lambda_handler import make_handler, _FoundryLambdaHandler


# ─── Minimal concrete agent ────────────────────────────────────────────────────

def _make_concrete_agent_class(execute_return=None):
    """Return a BaseAgent subclass that doesn't need real Tollgate wiring."""
    from arc.core import BaseAgent

    class FakeAgent(BaseAgent):
        async def execute(self, **kwargs):
            if execute_return is not None:
                return execute_return
            return {"processed": True, **kwargs}

    return FakeAgent


# ─── make_handler ─────────────────────────────────────────────────────────────

class TestMakeHandler:

    def test_raises_value_error_without_gateway(self, tmp_path, monkeypatch):
        """make_handler without gateway= must raise on cold-start."""
        AgentClass = _make_concrete_agent_class()
        handler_obj = make_handler(AgentClass)

        # Patch out everything up to the gateway check so we isolate just that guard
        from arc.core.manifest import AgentManifest
        from arc.core.lifecycle import LifecycleStage
        from arc.core.manifest import AgentStatus
        from arc.core.effects import FinancialEffect

        dummy_manifest = AgentManifest(
            agent_id="test", version="0.1.0", owner="t", description="t",
            lifecycle_stage=LifecycleStage.BUILD,
            allowed_effects=[FinancialEffect.PARTICIPANT_DATA_READ],
            data_access=[], policy_path="p.yaml", success_metrics=["m"],
        )

        with patch("arc.runtime.deploy.lambda_handler._FoundryLambdaHandler._load_secrets"):
            with patch("arc.core.manifest.AgentManifest.from_yaml", return_value=dummy_manifest):
                with patch("tollgate.YamlPolicyEvaluator"):
                    with patch("tollgate.AutoApprover"):
                        with patch("tollgate.JsonlAuditSink"):
                            with patch("tollgate.tower.ControlTower"):
                                with pytest.raises(ValueError, match="No gateway provided"):
                                    handler_obj._init_agent()

    def test_returns_object_with_handler_method(self):
        AgentClass = _make_concrete_agent_class()
        mock_gw = MagicMock()
        handler_obj = make_handler(AgentClass, gateway=mock_gw)
        assert hasattr(handler_obj, "handler")
        assert callable(handler_obj.handler)

    def test_agent_not_initialised_at_make_handler_time(self):
        AgentClass = _make_concrete_agent_class()
        mock_gw = MagicMock()
        handler_obj = make_handler(AgentClass, gateway=mock_gw)
        # _agent should be None until first invocation
        assert handler_obj._agent is None


# ─── Event normalisation ──────────────────────────────────────────────────────

class TestNormaliseEvent:

    def test_direct_dict_passes_through(self):
        event = {"participant_id": "p-001", "run_type": "batch"}
        result = _FoundryLambdaHandler._normalise_event(event)
        assert result == event

    def test_eventbridge_detail_extracted(self):
        event = {
            "source": "my.app",
            "detail-type": "AgentTrigger",
            "detail": {"fund_id": "f-42"},
        }
        result = _FoundryLambdaHandler._normalise_event(event)
        assert result == {"fund_id": "f-42"}

    def test_scheduled_aws_events_returns_empty(self):
        event = {"source": "aws.events", "detail-type": "Scheduled Event"}
        result = _FoundryLambdaHandler._normalise_event(event)
        assert result == {}

    def test_sqs_records_body_parsed(self):
        body = json.dumps({"plan_id": "p-999"})
        event = {"Records": [{"body": body}]}
        result = _FoundryLambdaHandler._normalise_event(event)
        assert result == {"plan_id": "p-999"}

    def test_sqs_foundry_event_wrapped(self):
        body = json.dumps({"foundry_event": "review_complete", "review_id": "r-1"})
        event = {"Records": [{"body": body}]}
        result = _FoundryLambdaHandler._normalise_event(event)
        # foundry_event present → returns wrapped as {"event": {...}}
        assert "event" in result

    def test_eventbridge_non_dict_detail_wrapped(self):
        event = {"detail": "just a string"}
        result = _FoundryLambdaHandler._normalise_event(event)
        assert result == {"detail": "just a string"}


# ─── Handler invocation ───────────────────────────────────────────────────────

class TestHandlerInvocation:
    """Tests that patch _init_agent to avoid real manifest/tower wiring."""

    def _make_handler_with_patched_init(self, execute_return=None, execute_side_effect=None):
        AgentClass = _make_concrete_agent_class(execute_return)
        mock_gw = MagicMock()
        handler_obj = make_handler(AgentClass, gateway=mock_gw)

        # Patch _init_agent to set up a mock agent instead of real wiring
        mock_agent = MagicMock()
        mock_manifest = MagicMock()
        mock_manifest.agent_id = "test-agent"
        mock_agent.manifest = mock_manifest

        if execute_side_effect:
            mock_agent.execute = AsyncMock(side_effect=execute_side_effect)
        else:
            mock_agent.execute = AsyncMock(return_value=execute_return or {"ok": True})

        def fake_init():
            handler_obj._agent = mock_agent

        handler_obj._init_agent = fake_init
        return handler_obj, mock_agent

    def _make_context(self, request_id: str = "test-req-123"):
        ctx = MagicMock()
        ctx.aws_request_id = request_id
        return ctx

    def test_successful_invocation_returns_200(self):
        handler_obj, _ = self._make_handler_with_patched_init({"result": "done"})
        result = handler_obj.handler({"param": "value"}, self._make_context())
        assert result["statusCode"] == 200
        assert result["result"] == {"result": "done"}

    def test_result_includes_agent_id_and_request_id(self):
        handler_obj, _ = self._make_handler_with_patched_init()
        result = handler_obj.handler({}, self._make_context("req-abc"))
        assert result["agent"] == "test-agent"
        assert result["request_id"] == "req-abc"

    def test_cold_start_initialises_agent(self):
        handler_obj, mock_agent = self._make_handler_with_patched_init()
        assert handler_obj._agent is None
        handler_obj.handler({}, self._make_context())
        assert handler_obj._agent is mock_agent

    def test_warm_invocation_does_not_reinitialise(self):
        handler_obj, mock_agent = self._make_handler_with_patched_init()
        init_calls = []
        original_init = handler_obj._init_agent

        def counted_init():
            init_calls.append(1)
            original_init()

        handler_obj._init_agent = counted_init

        # First call (cold start)
        handler_obj.handler({}, self._make_context())
        # Second call (warm)
        handler_obj.handler({}, self._make_context())

        assert len(init_calls) == 1   # only one cold start

    def test_permission_error_returns_403(self):
        handler_obj, _ = self._make_handler_with_patched_init(
            execute_side_effect=PermissionError("effect denied")
        )
        result = handler_obj.handler({}, self._make_context())
        assert result["statusCode"] == 403
        assert result["error"] == "permission_denied"

    def test_unexpected_exception_returns_500(self):
        handler_obj, _ = self._make_handler_with_patched_init(
            execute_side_effect=RuntimeError("something exploded")
        )
        result = handler_obj.handler({}, self._make_context())
        assert result["statusCode"] == 500
        assert result["error"] == "RuntimeError"

    def test_event_kwargs_passed_to_execute(self):
        handler_obj, mock_agent = self._make_handler_with_patched_init()
        handler_obj.handler({"participant_id": "p-001", "run": True}, self._make_context())
        mock_agent.execute.assert_awaited_once_with(participant_id="p-001", run=True)

    def test_eventbridge_event_unpacked_before_execute(self):
        handler_obj, mock_agent = self._make_handler_with_patched_init()
        event = {"detail": {"fund_id": "f-42"}}
        handler_obj.handler(event, self._make_context())
        mock_agent.execute.assert_awaited_once_with(fund_id="f-42")

    def test_sqs_event_body_unpacked_before_execute(self):
        handler_obj, mock_agent = self._make_handler_with_patched_init()
        body = json.dumps({"plan_id": "p-999"})
        event = {"Records": [{"body": body}]}
        handler_obj.handler(event, self._make_context())
        mock_agent.execute.assert_awaited_once_with(plan_id="p-999")

    def test_bedrock_event_not_routed_to_normalise(self):
        """Events with actionGroup should not be treated as direct invocations."""
        handler_obj, mock_agent = self._make_handler_with_patched_init()
        # Bedrock events have actionGroup — these route to _invoke_bedrock, not execute directly.
        # We just confirm execute() is NOT called via the standard path.
        event = {
            "actionGroup": "MyGroup",
            "function": "execute",
            "parameters": [],
        }
        # Patch _invoke_bedrock to avoid full Bedrock parsing
        import asyncio

        async def fake_bedrock(event, ctx, loop):
            return {"messageVersion": "1.0", "response": {"httpStatusCode": 200}}

        handler_obj._invoke_bedrock = fake_bedrock
        handler_obj.handler(event, self._make_context())
        mock_agent.execute.assert_not_awaited()
