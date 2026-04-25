"""
Tests for OrchestratorProtocol, OrchestratorResult, OrchestratorSuspended,
LangGraphOrchestrator, and AgentCoreOrchestrator.

All tests run without real AWS credentials or LangGraph runtime.
"""

import pytest
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

from arc.orchestrators.protocol import (
    OrchestratorProtocol,
    OrchestratorResult,
    OrchestratorSuspended,
)


# ── OrchestratorResult ────────────────────────────────────────────────────────

class TestOrchestratorResult:
    def test_basic_creation(self):
        result = OrchestratorResult(output={"ticket_id": "T-001"}, state={"priority": "P2"})
        assert result.output == {"ticket_id": "T-001"}
        assert result.state == {"priority": "P2"}
        assert result.run_id == ""
        assert result.metadata == {}

    def test_get_accessor(self):
        result = OrchestratorResult(
            output={"ticket_id": "T-001", "intent": "incident"},
            state={},
        )
        assert result.get("ticket_id") == "T-001"
        assert result.get("missing_key", "default") == "default"

    def test_dict_accessor(self):
        result = OrchestratorResult(
            output={"ticket_id": "T-001"},
            state={},
        )
        assert result["ticket_id"] == "T-001"
        with pytest.raises(KeyError):
            _ = result["nonexistent"]

    def test_with_run_id_and_metadata(self):
        result = OrchestratorResult(
            output={"status": "done"},
            state={"completed": True},
            run_id="run-abc-123",
            metadata={"framework": "langgraph", "resumed": False},
        )
        assert result.run_id == "run-abc-123"
        assert result.metadata["framework"] == "langgraph"

    def test_empty_output(self):
        result = OrchestratorResult(output={}, state={})
        assert result.get("anything") is None


# ── OrchestratorSuspended ─────────────────────────────────────────────────────

class TestOrchestratorSuspended:
    def test_basic_creation(self):
        exc = OrchestratorSuspended(
            thread_id="thread-xyz",
            pending_effect="ticket.create",
            reason="P1 ticket requires human approval",
        )
        assert exc.thread_id == "thread-xyz"
        assert exc.pending_effect == "ticket.create"
        assert exc.reason == "P1 ticket requires human approval"

    def test_is_exception(self):
        exc = OrchestratorSuspended("t1", "ticket.create", "reason")
        assert isinstance(exc, Exception)

    def test_str_contains_thread_id(self):
        exc = OrchestratorSuspended("thread-42", "email.reply.send", "needs approval")
        assert "thread-42" in str(exc)
        assert "email.reply.send" in str(exc)

    def test_can_raise_and_catch(self):
        with pytest.raises(OrchestratorSuspended) as exc_info:
            raise OrchestratorSuspended("t1", "ticket.create", "P1 needs review")
        assert exc_info.value.thread_id == "t1"


# ── OrchestratorProtocol ──────────────────────────────────────────────────────

class TestOrchestratorProtocol:
    def test_protocol_is_runtime_checkable(self):
        """OrchestratorProtocol is @runtime_checkable."""
        # Any class with run/stream/resume methods should satisfy protocol
        class MinimalOrchestrator:
            async def run(self, input, config=None):
                return OrchestratorResult(output={}, state={})

            async def stream(self, input, config=None):
                yield {}

            async def resume(self, thread_id, approval, config=None):
                return OrchestratorResult(output={}, state={})

        orch = MinimalOrchestrator()
        # With runtime_checkable, isinstance check works
        assert isinstance(orch, OrchestratorProtocol)

    def test_object_without_run_fails_protocol(self):
        class NotAnOrchestrator:
            pass

        assert not isinstance(NotAnOrchestrator(), OrchestratorProtocol)


# ── LangGraphOrchestrator ─────────────────────────────────────────────────────

class TestLangGraphOrchestrator:
    def test_implements_protocol(self):
        """LangGraphOrchestrator satisfies OrchestratorProtocol."""
        from arc.orchestrators.langgraph import LangGraphOrchestrator

        # Create a mock graph
        mock_graph = MagicMock()
        mock_graph.ainvoke = AsyncMock(return_value={"ticket_id": "T-001", "priority": "P3"})
        mock_graph.astream = AsyncMock()

        orch = LangGraphOrchestrator(graph=mock_graph)
        assert isinstance(orch, OrchestratorProtocol)

    @pytest.mark.asyncio
    async def test_run_returns_orchestrator_result(self):
        """run() wraps graph output in OrchestratorResult."""
        from arc.orchestrators.langgraph import LangGraphOrchestrator

        mock_graph = MagicMock()
        mock_graph.ainvoke = AsyncMock(return_value={
            "ticket_id": "T-001",
            "priority": "P2",
            "completed": True,
        })

        orch = LangGraphOrchestrator(graph=mock_graph)
        result = await orch.run({"email_id": "e-001", "email": {}})

        assert isinstance(result, OrchestratorResult)
        assert result.output.get("ticket_id") == "T-001"
        assert result.metadata.get("framework") == "langgraph"

    @pytest.mark.asyncio
    async def test_run_maps_interrupt_to_suspended(self):
        """GraphInterrupt should be mapped to OrchestratorSuspended."""
        from arc.orchestrators.langgraph import LangGraphOrchestrator

        # Simulate LangGraph GraphInterrupt
        class FakeGraphInterrupt(Exception):
            def __init__(self, value):
                self.value = value

        mock_graph = MagicMock()
        mock_graph.ainvoke = AsyncMock(
            side_effect=FakeGraphInterrupt({"effect": "ticket.create", "reason": "P1 needs review"})
        )

        orch = LangGraphOrchestrator(graph=mock_graph)
        with pytest.raises(OrchestratorSuspended) as exc_info:
            await orch.run({"email_id": "e-001", "email": {}})

        assert exc_info.value.pending_effect == "ticket.create"
        assert "P1" in exc_info.value.reason

    @pytest.mark.asyncio
    async def test_resume_passes_thread_id(self):
        """resume() sets thread_id in config and calls ainvoke."""
        from arc.orchestrators.langgraph import LangGraphOrchestrator

        mock_graph = MagicMock()
        mock_graph.ainvoke = AsyncMock(return_value={"ticket_id": "T-002", "completed": True})

        orch = LangGraphOrchestrator(graph=mock_graph)
        result = await orch.resume(
            thread_id="thread-42",
            approval={"approved": True, "approver": "alice"},
        )

        assert isinstance(result, OrchestratorResult)
        assert result.metadata.get("resumed") is True

        # Verify thread_id was passed to ainvoke
        call_config = mock_graph.ainvoke.call_args[1].get("config", {})
        assert call_config.get("configurable", {}).get("thread_id") == "thread-42"

    def test_build_config_assigns_thread_id(self):
        """_build_config always ensures a thread_id."""
        from arc.orchestrators.langgraph import LangGraphOrchestrator

        mock_graph = MagicMock()
        orch = LangGraphOrchestrator(graph=mock_graph)
        config = orch._build_config(None)
        assert "thread_id" in config.get("configurable", {})

    def test_build_config_uses_override(self):
        from arc.orchestrators.langgraph import LangGraphOrchestrator

        mock_graph = MagicMock()
        orch = LangGraphOrchestrator(graph=mock_graph)
        config = orch._build_config({"configurable": {"thread_id": "my-thread"}})
        assert config["configurable"]["thread_id"] == "my-thread"


# ── AgentCoreOrchestrator ─────────────────────────────────────────────────────

class TestAgentCoreOrchestrator:
    def test_implements_protocol(self):
        """AgentCoreOrchestrator satisfies OrchestratorProtocol."""
        from arc.orchestrators.agentcore import AgentCoreOrchestrator

        orch = AgentCoreOrchestrator(agent_id="test-agent-123")
        assert isinstance(orch, OrchestratorProtocol)

    def test_has_required_methods(self):
        from arc.orchestrators.agentcore import AgentCoreOrchestrator

        orch = AgentCoreOrchestrator(agent_id="test-agent-123")
        assert hasattr(orch, "run")
        assert hasattr(orch, "stream")
        assert hasattr(orch, "resume")

    @pytest.mark.asyncio
    async def test_run_without_aws_raises_gracefully(self):
        """Without real AWS, run() should raise a clear error."""
        from arc.orchestrators.agentcore import AgentCoreOrchestrator

        orch = AgentCoreOrchestrator(agent_id="test-agent-123")
        with pytest.raises(Exception) as exc_info:
            await orch.run({"email_id": "e-001"})
        # Should raise a boto3/botocore/AWS error — not AttributeError or TypeError
        err_type = type(exc_info.value).__name__
        assert err_type in (
            "ImportError", "NoCredentialsError", "BotoCoreError",
            "ClientError", "Exception", "RuntimeError",
            "ParamValidationError",  # raised when required params missing (no real AWS)
        )
