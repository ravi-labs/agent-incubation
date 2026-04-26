"""
Tests for arc.core.tools — AgentToolRegistry and @governed_tool decorator.

Covers:
  @governed_tool decorator:
    - metadata stored on wrapper function
    - decorated function is still callable
    - wraps preserves original function name/docstring

  AgentToolRegistry:
    - register() stores tool def
    - register() warns on overwrite
    - register_all() auto-discovers @governed_tool methods
    - register_all() ignores non-decorated methods
    - invoke() routes through agent.run_effect()
    - invoke() on unknown name raises KeyError
    - invoke() passes kwargs to exec_fn
    - list_tools() returns all tools
    - list_tools(tag=) filters by tag
    - get() returns tool def or None
    - names() lists registered names
    - len() returns count
    - repr() shows agent_id and count

  ToolRegistry alias:
    - ToolRegistry is same class as AgentToolRegistry
"""

import pytest
from unittest.mock import AsyncMock, MagicMock

from arc.core.effects import FinancialEffect
from arc.core.manifest import AgentManifest, AgentStatus
from arc.core.tools import AgentToolRegistry, GovernedToolDef, ToolRegistry, governed_tool

from arc.core.lifecycle import LifecycleStage


# ─── Fixtures ──────────────────────────────────────────────────────────────────

def make_manifest(allowed_effects=None) -> AgentManifest:
    return AgentManifest(
        agent_id="tool-test-agent",
        version="0.1.0",
        owner="test-team",
        description="Tool test agent",
        lifecycle_stage=LifecycleStage.BUILD,
        allowed_effects=allowed_effects or [
            FinancialEffect.RISK_SCORE_COMPUTE,
            FinancialEffect.PARTICIPANT_DATA_READ,
            FinancialEffect.PARTICIPANT_COMMUNICATION_SEND,
        ],
        data_access=["participant.data"],
        policy_path="tests/policy.yaml",
        success_metrics=["coverage"],
    )


def make_mock_agent(allowed_effects=None) -> MagicMock:
    agent = MagicMock()
    agent.manifest = make_manifest(allowed_effects)
    agent.run_effect = AsyncMock(return_value={"score": 0.72})
    return agent


# ─── @governed_tool decorator ─────────────────────────────────────────────────

class TestGovernedToolDecorator:

    def test_metadata_stored_on_wrapper(self):
        @governed_tool(
            effect=FinancialEffect.RISK_SCORE_COMPUTE,
            description="Compute score",
            intent_reason="Testing",
        )
        async def compute(self, x: int) -> float:
            return x * 0.1

        meta = getattr(compute, "__governed_tool__")
        assert meta["effect"] == FinancialEffect.RISK_SCORE_COMPUTE
        assert meta["description"] == "Compute score"
        assert meta["intent_reason"] == "Testing"

    def test_params_schema_stored(self):
        @governed_tool(
            effect=FinancialEffect.RISK_SCORE_COMPUTE,
            description="Test",
            params_schema={"x": "int", "y": "str"},
        )
        async def my_tool(self): ...

        meta = getattr(my_tool, "__governed_tool__")
        assert meta["params_schema"] == {"x": "int", "y": "str"}

    def test_tags_stored(self):
        @governed_tool(
            effect=FinancialEffect.PARTICIPANT_DATA_READ,
            description="Test",
            tags=["read", "participant"],
        )
        async def my_tool(self): ...

        meta = getattr(my_tool, "__governed_tool__")
        assert "read" in meta["tags"]
        assert "participant" in meta["tags"]

    def test_wraps_preserves_function_name(self):
        @governed_tool(effect=FinancialEffect.RISK_SCORE_COMPUTE, description="d")
        async def my_named_tool(self): ...

        assert my_named_tool.__name__ == "my_named_tool"

    @pytest.mark.asyncio
    async def test_decorated_function_is_still_callable(self):
        @governed_tool(effect=FinancialEffect.RISK_SCORE_COMPUTE, description="d")
        async def add_one(self, x: int) -> int:
            return x + 1

        result = await add_one(None, x=5)
        assert result == 6

    def test_default_intent_reason_set_when_omitted(self):
        @governed_tool(effect=FinancialEffect.RISK_SCORE_COMPUTE, description="d")
        async def my_tool(self): ...

        meta = getattr(my_tool, "__governed_tool__")
        assert "Tool invocation" in meta["intent_reason"]


# ─── AgentToolRegistry ────────────────────────────────────────────────────────

class TestAgentToolRegistry:

    def test_register_stores_tool_def(self):
        agent = make_mock_agent()
        registry = AgentToolRegistry(agent)

        async def my_fn(**kwargs): return {}
        registry.register(
            name="my_tool",
            fn=my_fn,
            effect=FinancialEffect.RISK_SCORE_COMPUTE,
            description="A test tool",
        )
        assert "my_tool" in registry.names()

    def test_register_overwrite_logs_warning(self, caplog):
        import logging
        agent = make_mock_agent()
        registry = AgentToolRegistry(agent)

        async def fn1(): ...
        async def fn2(): ...

        registry.register("tool", fn1, FinancialEffect.RISK_SCORE_COMPUTE, "First")
        with caplog.at_level(logging.WARNING):
            registry.register("tool", fn2, FinancialEffect.RISK_SCORE_COMPUTE, "Second")
        assert "overwriting" in caplog.text.lower()

    def test_register_all_discovers_decorated_methods(self):
        agent = make_mock_agent()
        registry = AgentToolRegistry(agent)

        class MyAgent:
            @governed_tool(effect=FinancialEffect.RISK_SCORE_COMPUTE, description="Score")
            async def compute_score(self, **kwargs): return 0.5

            @governed_tool(effect=FinancialEffect.PARTICIPANT_DATA_READ, description="Read")
            async def read_data(self, **kwargs): return {}

            async def not_a_tool(self): ...

        obj = MyAgent()
        count = registry.register_all(obj)
        assert count == 2
        assert "compute_score" in registry.names()
        assert "read_data" in registry.names()
        assert "not_a_tool" not in registry.names()

    def test_register_all_ignores_non_decorated_methods(self):
        agent = make_mock_agent()
        registry = AgentToolRegistry(agent)

        class Plain:
            def foo(self): ...
            async def bar(self): ...

        count = registry.register_all(Plain())
        assert count == 0

    @pytest.mark.asyncio
    async def test_invoke_calls_run_effect(self):
        agent = make_mock_agent()
        registry = AgentToolRegistry(agent)

        async def score_fn(**kwargs): return 0.88
        registry.register(
            "compute_score", score_fn,
            FinancialEffect.RISK_SCORE_COMPUTE, "Compute risk score",
        )
        await registry.invoke("compute_score", participant_id="p-001")
        agent.run_effect.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_invoke_passes_kwargs_to_exec_fn(self):
        agent = make_mock_agent()
        registry = AgentToolRegistry(agent)
        received = {}

        async def capture_fn(**kwargs):
            received.update(kwargs)
            return {}

        registry.register("capture", capture_fn, FinancialEffect.RISK_SCORE_COMPUTE, "Capture")

        # Simulate exec_fn being called with the right kwargs
        async def fake_run_effect(**kwargs):
            if kwargs.get("exec_fn"):
                await kwargs["exec_fn"]()
            return {}

        agent.run_effect = AsyncMock(side_effect=fake_run_effect)
        await registry.invoke("capture", participant_id="p-42", age=55)
        assert received == {"participant_id": "p-42", "age": 55}

    @pytest.mark.asyncio
    async def test_invoke_unknown_tool_raises_key_error(self):
        agent = make_mock_agent()
        registry = AgentToolRegistry(agent)
        with pytest.raises(KeyError, match="not registered"):
            await registry.invoke("nonexistent_tool")

    def test_list_tools_returns_all(self):
        agent = make_mock_agent()
        registry = AgentToolRegistry(agent)

        async def fn(): ...
        registry.register("t1", fn, FinancialEffect.RISK_SCORE_COMPUTE, "T1")
        registry.register("t2", fn, FinancialEffect.PARTICIPANT_DATA_READ, "T2")
        assert len(registry.list_tools()) == 2

    def test_list_tools_filters_by_tag(self):
        agent = make_mock_agent()
        registry = AgentToolRegistry(agent)

        async def fn(): ...
        registry.register("t1", fn, FinancialEffect.RISK_SCORE_COMPUTE, "T1", tags=["compute"])
        registry.register("t2", fn, FinancialEffect.PARTICIPANT_DATA_READ, "T2", tags=["read"])
        compute_tools = registry.list_tools(tag="compute")
        assert len(compute_tools) == 1
        assert compute_tools[0].name == "t1"

    def test_get_returns_tool_def(self):
        agent = make_mock_agent()
        registry = AgentToolRegistry(agent)

        async def fn(): ...
        registry.register("my_tool", fn, FinancialEffect.RISK_SCORE_COMPUTE, "Desc")
        tool = registry.get("my_tool")
        assert isinstance(tool, GovernedToolDef)
        assert tool.name == "my_tool"

    def test_get_returns_none_for_unknown(self):
        agent = make_mock_agent()
        registry = AgentToolRegistry(agent)
        assert registry.get("unknown") is None

    def test_names_lists_registered_names(self):
        agent = make_mock_agent()
        registry = AgentToolRegistry(agent)

        async def fn(): ...
        registry.register("a", fn, FinancialEffect.RISK_SCORE_COMPUTE, "A")
        registry.register("b", fn, FinancialEffect.PARTICIPANT_DATA_READ, "B")
        assert set(registry.names()) == {"a", "b"}

    def test_len_returns_count(self):
        agent = make_mock_agent()
        registry = AgentToolRegistry(agent)

        async def fn(): ...
        registry.register("a", fn, FinancialEffect.RISK_SCORE_COMPUTE, "A")
        registry.register("b", fn, FinancialEffect.PARTICIPANT_DATA_READ, "B")
        assert len(registry) == 2

    def test_repr_shows_agent_id_and_count(self):
        agent = make_mock_agent()
        registry = AgentToolRegistry(agent)
        r = repr(registry)
        assert "tool-test-agent" in r
        assert "0" in r

    def test_tool_registry_alias_is_same_class(self):
        assert ToolRegistry is AgentToolRegistry

    @pytest.mark.asyncio
    async def test_invoke_with_custom_intent_reason(self):
        agent = make_mock_agent()
        registry = AgentToolRegistry(agent)

        async def fn(**kwargs): return {}
        registry.register("t", fn, FinancialEffect.RISK_SCORE_COMPUTE, "Default reason")
        await registry.invoke("t", intent_reason="Custom override reason")

        call_kwargs = agent.run_effect.call_args[1]
        assert call_kwargs["intent_reason"] == "Custom override reason"
