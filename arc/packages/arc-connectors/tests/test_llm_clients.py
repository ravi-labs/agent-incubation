"""
Tests for the LLMClient abstraction and its two implementations.

Both BedrockLLMClient and LiteLLMClient are exercised against a fake
agent that captures the run_effect() call shape. We don't hit Bedrock
or any real provider — boto3 / litellm are stubbed.

Covers:
  - LLMClient Protocol conformance for both implementations
  - generate() routes through agent.run_effect with the right effect/intent
  - generate_json() injects the JSON-only system instruction and parses
  - Provider metadata appears in the run_effect call (llm_provider tag)
  - LiteLLMClient raises a helpful ImportError when litellm is missing
"""

from __future__ import annotations

import json
import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from arc.core import FinancialEffect, LLMClient


# ── Test double: an agent whose run_effect just runs the exec_fn and ───────
# ──                records the call shape for assertions. ────────────────


class FakeAgent:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def run_effect(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        # Match BaseAgent.run_effect: exec_fn returns the actual value
        exec_fn = kwargs.get("exec_fn")
        if exec_fn is None:
            return None
        return await exec_fn()


# ── BedrockLLMClient ───────────────────────────────────────────────────────


class TestBedrockLLMClientProtocol:
    def test_implements_llmclient_protocol(self):
        from arc.connectors.bedrock_llm import BedrockLLMClient
        # Note: runtime_checkable Protocol checks only structural shape (method names).
        client = BedrockLLMClient(model_id="anthropic.claude-3-5-sonnet-20241022-v2:0")
        assert isinstance(client, LLMClient)


class TestBedrockLLMClientGenerate:
    @pytest.mark.asyncio
    async def test_generate_routes_through_run_effect_with_correct_metadata(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        from arc.connectors.bedrock_llm import BedrockLLMClient

        client = BedrockLLMClient(model_id="anthropic.claude-3-5-sonnet-20241022-v2:0")
        # Stub the synchronous _invoke_bedrock so we don't touch boto3
        monkeypatch.setattr(
            client, "_invoke_bedrock", lambda **kw: "drafted text"
        )

        agent = FakeAgent()
        result = await client.generate(
            agent=agent,
            effect=FinancialEffect.INTERVENTION_DRAFT,
            intent_action="draft_intervention",
            intent_reason="test reason",
            prompt="hello there from a test prompt",
            system="be concise",
        )
        assert result == "drafted text"

        assert len(agent.calls) == 1
        call = agent.calls[0]
        assert call["effect"]        is FinancialEffect.INTERVENTION_DRAFT
        assert call["tool"]          == "bedrock"
        assert call["action"]        == "invoke_model"
        assert call["intent_action"] == "draft_intervention"
        assert call["intent_reason"] == "test reason"
        assert call["params"]["model_id"]      == "anthropic.claude-3-5-sonnet-20241022-v2:0"
        assert call["params"]["prompt_tokens"] == 6   # word count of prompt
        assert call["metadata"]["llm_provider"] == "bedrock"
        assert call["metadata"]["llm_model"]    == client.model_id

    @pytest.mark.asyncio
    async def test_generate_json_parses_and_strips_fences(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        from arc.connectors.bedrock_llm import BedrockLLMClient

        client = BedrockLLMClient()
        # Return JSON wrapped in code fences — generate_json must strip them.
        monkeypatch.setattr(
            client, "_invoke_bedrock",
            lambda **kw: '```json\n{"verdict": "ok", "score": 0.9}\n```',
        )

        agent = FakeAgent()
        out = await client.generate_json(
            agent=agent,
            effect=FinancialEffect.RISK_SCORE_COMPUTE,
            intent_action="score",
            intent_reason="test",
            prompt="evaluate this",
        )
        assert out == {"verdict": "ok", "score": 0.9}

    @pytest.mark.asyncio
    async def test_generate_json_raises_on_invalid_json(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        from arc.connectors.bedrock_llm import BedrockLLMClient

        client = BedrockLLMClient()
        monkeypatch.setattr(client, "_invoke_bedrock", lambda **kw: "not json at all")

        with pytest.raises(ValueError, match="non-JSON response"):
            await client.generate_json(
                agent=FakeAgent(),
                effect=FinancialEffect.RISK_SCORE_COMPUTE,
                intent_action="score",
                intent_reason="test",
                prompt="evaluate",
            )


# ── LiteLLMClient ──────────────────────────────────────────────────────────


@pytest.fixture
def fake_litellm(monkeypatch: pytest.MonkeyPatch):
    """Install a fake ``litellm`` module in sys.modules.

    Captures the kwargs each acompletion call gets, returns a configurable
    response. Cleans up automatically on test teardown.
    """
    fake = MagicMock()
    captured: dict[str, Any] = {"calls": []}

    async def _acompletion(**kwargs: Any) -> dict[str, Any]:
        captured["calls"].append(kwargs)
        # Default: echo back a fixed string. Tests can monkey-patch
        # captured["response"] to override.
        content = captured.get("response", "litellm response text")
        return {"choices": [{"message": {"content": content}}]}

    fake.acompletion = _acompletion
    monkeypatch.setitem(sys.modules, "litellm", fake)
    return captured


class TestLiteLLMClientProtocol:
    def test_implements_llmclient_protocol(self):
        from arc.connectors.litellm_client import LiteLLMClient
        client = LiteLLMClient(model="anthropic/claude-3-5-sonnet-20241022")
        assert isinstance(client, LLMClient)


class TestLiteLLMClientGenerate:
    @pytest.mark.asyncio
    async def test_generate_routes_through_run_effect_and_calls_litellm(
        self, fake_litellm: dict[str, Any]
    ):
        from arc.connectors.litellm_client import LiteLLMClient

        client = LiteLLMClient(model="anthropic/claude-3-5-sonnet-20241022")
        agent = FakeAgent()

        result = await client.generate(
            agent=agent,
            effect=FinancialEffect.INTERVENTION_DRAFT,
            intent_action="draft",
            intent_reason="test",
            prompt="hello world",
            system="be brief",
            max_tokens=128,
            temperature=0.5,
        )
        assert result == "litellm response text"

        # run_effect call shape
        assert len(agent.calls) == 1
        call = agent.calls[0]
        assert call["tool"]                     == "litellm"
        assert call["action"]                   == "completion"
        assert call["params"]["model"]          == "anthropic/claude-3-5-sonnet-20241022"
        assert call["metadata"]["llm_provider"] == "litellm"
        assert call["metadata"]["llm_model"]    == "anthropic/claude-3-5-sonnet-20241022"

        # litellm.acompletion call shape
        assert len(fake_litellm["calls"]) == 1
        ll = fake_litellm["calls"][0]
        assert ll["model"]       == "anthropic/claude-3-5-sonnet-20241022"
        assert ll["max_tokens"]  == 128
        assert ll["temperature"] == 0.5
        # System message comes first, then user
        assert ll["messages"][0] == {"role": "system", "content": "be brief"}
        assert ll["messages"][1] == {"role": "user",   "content": "hello world"}

    @pytest.mark.asyncio
    async def test_no_system_message_when_system_is_none(
        self, fake_litellm: dict[str, Any]
    ):
        from arc.connectors.litellm_client import LiteLLMClient

        client = LiteLLMClient(model="openai/gpt-4o")
        await client.generate(
            agent=FakeAgent(),
            effect=FinancialEffect.RISK_SCORE_COMPUTE,
            intent_action="x",
            intent_reason="y",
            prompt="just user",
        )
        ll = fake_litellm["calls"][0]
        assert len(ll["messages"]) == 1
        assert ll["messages"][0]["role"] == "user"

    @pytest.mark.asyncio
    async def test_fallbacks_passed_to_litellm(self, fake_litellm: dict[str, Any]):
        from arc.connectors.litellm_client import LiteLLMClient

        client = LiteLLMClient(
            model="anthropic/claude-3-5-sonnet-20241022",
            fallback_models=["openai/gpt-4o-mini", "bedrock/anthropic.claude-3-haiku-20240307-v1:0"],
        )
        await client.generate(
            agent=FakeAgent(),
            effect=FinancialEffect.RISK_SCORE_COMPUTE,
            intent_action="x", intent_reason="y", prompt="p",
        )
        ll = fake_litellm["calls"][0]
        assert ll["fallbacks"] == [
            "openai/gpt-4o-mini",
            "bedrock/anthropic.claude-3-haiku-20240307-v1:0",
        ]

    @pytest.mark.asyncio
    async def test_generate_json_parses_response(self, fake_litellm: dict[str, Any]):
        from arc.connectors.litellm_client import LiteLLMClient

        fake_litellm["response"] = json.dumps({"verdict": "ok", "score": 0.9})
        client = LiteLLMClient(model="openai/gpt-4o")

        out = await client.generate_json(
            agent=FakeAgent(),
            effect=FinancialEffect.RISK_SCORE_COMPUTE,
            intent_action="x", intent_reason="y", prompt="p",
        )
        assert out == {"verdict": "ok", "score": 0.9}

    @pytest.mark.asyncio
    async def test_missing_litellm_raises_helpful_importerror(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        from arc.connectors.litellm_client import LiteLLMClient

        # Simulate litellm not being installed
        monkeypatch.setitem(sys.modules, "litellm", None)

        client = LiteLLMClient(model="anthropic/claude-3-5-sonnet-20241022")
        with pytest.raises(ImportError, match=r"arc-connectors\[litellm\]"):
            await client.generate(
                agent=FakeAgent(),
                effect=FinancialEffect.RISK_SCORE_COMPUTE,
                intent_action="x", intent_reason="y", prompt="p",
            )
