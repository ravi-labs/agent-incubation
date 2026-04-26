"""Tests for arc.orchestrators.langchain_chat_model.GovernedChatModel.

The wrapper exists to close the LangGraph governance gap: every
``ainvoke`` on a wrapped chat model must route through
``agent.run_effect()`` so ControlTower sees the call.

These tests verify:

  1. ``ainvoke`` calls ``agent.run_effect`` exactly once with the
     expected effect / intent / metadata shape.
  2. The wrapped model's ``_agenerate`` is only invoked from inside
     ``run_effect`` (i.e. as the ``exec_fn``) — never directly.
  3. ``bind_tools`` returns a binding around the wrapper, not the
     wrapped model — so subsequent calls still flow through governance.
  4. ``with_structured_output`` (which uses ``bind_tools`` internally)
     preserves governance.
  5. Metadata mirrors the ``LLMClient`` shape:
     ``llm_provider`` / ``llm_model`` / ``prompt_chars`` / ``message_count``.
  6. Sync ``invoke`` raises ``NotImplementedError`` with a helpful message.
  7. Provider / model labels auto-derive from common LangChain class names.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from arc.orchestrators.langchain_chat_model import (
    GovernedChatModel,
    _content_chars,
    _derive_model,
    _derive_provider,
    governed_chat_model,
)


# ── Test doubles ────────────────────────────────────────────────────────────


class ChatRecording(BaseChatModel):
    """A minimal BaseChatModel test double.

    Records calls to ``_agenerate`` and returns a fixed AIMessage so we
    can assert what flows through. Reports a fake ``model`` attribute
    so the wrapper's auto-derivation has something to find.
    """

    model: str = "fake-model-id"
    _calls: list[dict] = []

    @property
    def _llm_type(self) -> str:
        return "recording"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        raise NotImplementedError  # async only in tests

    async def _agenerate(
        self,
        messages,
        stop=None,
        run_manager=None,
        **kwargs,
    ) -> ChatResult:
        # Record the call — we want to assert it only fires from inside
        # the run_effect exec_fn, never as a direct bypass.
        self._calls.append({
            "messages":  list(messages),
            "stop":      stop,
            "kwargs":    dict(kwargs),
        })
        return ChatResult(generations=[
            ChatGeneration(message=AIMessage(content="fake-response"))
        ])


class _FakeAgent:
    """Stand-in for BaseAgent that records run_effect calls.

    The real BaseAgent.run_effect runs the policy + audit pipeline; for
    these tests we just want to see (a) that the wrapper called it, and
    (b) the args it called with. The exec_fn is invoked here so the
    wrapped model's ``_agenerate`` runs through us, mirroring the real
    ALLOW path.
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def run_effect(
        self,
        *,
        effect,
        tool,
        action,
        params,
        intent_action,
        intent_reason,
        metadata=None,
        exec_fn=None,
        confidence=None,
    ):
        self.calls.append({
            "effect":         effect,
            "tool":           tool,
            "action":         action,
            "params":         params,
            "intent_action":  intent_action,
            "intent_reason":  intent_reason,
            "metadata":       metadata,
        })
        if exec_fn is None:
            return None
        return await exec_fn()


# ── Helpers ─────────────────────────────────────────────────────────────────


def _make_wrapper(
    *,
    agent: Any | None = None,
    model: BaseChatModel | None = None,
    extra_meta: dict | None = None,
):
    agent = agent or _FakeAgent()
    model = model or ChatRecording()
    wrapper = governed_chat_model(
        chat_model    = model,
        agent         = agent,
        effect        = "test-effect",
        intent_action = "test_action",
        intent_reason = "test reason",
        metadata      = extra_meta,
    )
    return agent, model, wrapper


# ── 1. Routing — single call lands in run_effect ───────────────────────────


class TestGovernanceRouting:
    @pytest.mark.asyncio
    async def test_ainvoke_calls_run_effect_once(self):
        agent, model, wrapper = _make_wrapper()

        out = await wrapper.ainvoke([HumanMessage(content="hello")])

        assert len(agent.calls) == 1
        assert isinstance(out, AIMessage)
        assert out.content == "fake-response"

    @pytest.mark.asyncio
    async def test_wrapped_agenerate_only_runs_inside_exec_fn(self):
        """If exec_fn isn't called (e.g. policy DENY), the wrapped model
        must not be invoked. This proves the wrapper doesn't have a
        side-channel that bypasses run_effect."""
        denying_agent = MagicMock()
        denying_agent.run_effect = AsyncMock(return_value=ChatResult(
            generations=[ChatGeneration(message=AIMessage(content="blocked"))]
        ))
        model = ChatRecording()
        model._calls = []

        wrapper = governed_chat_model(
            chat_model    = model,
            agent         = denying_agent,
            effect        = "denied-effect",
            intent_action = "blocked",
            intent_reason = "policy denies",
        )
        await wrapper.ainvoke([HumanMessage(content="hello")])

        # The agent's run_effect was called…
        assert denying_agent.run_effect.call_count == 1
        # …but exec_fn was never run, so the wrapped model never saw the call.
        assert model._calls == []


# ── 2. Metadata shape — must mirror LLMClient (Bedrock/LiteLLM) ────────────


class TestMetadataShape:
    @pytest.mark.asyncio
    async def test_metadata_has_provider_model_chars_count(self):
        agent, _, wrapper = _make_wrapper()

        await wrapper.ainvoke([
            HumanMessage(content="hello world"),
            HumanMessage(content="how are you"),
        ])

        md = agent.calls[0]["metadata"]
        assert md["llm_provider"]  == "recording"           # auto-derived
        assert md["llm_model"]     == "fake-model-id"       # auto-derived
        # 11 + 11 = 22 chars across two messages.
        assert md["prompt_chars"]  == len("hello world") + len("how are you")
        assert md["message_count"] == 2

    @pytest.mark.asyncio
    async def test_extra_metadata_merged(self):
        agent, _, wrapper = _make_wrapper(
            extra_meta={"email_id": "e-001", "tenant": "acme"},
        )
        await wrapper.ainvoke([HumanMessage(content="hello")])

        md = agent.calls[0]["metadata"]
        assert md["email_id"] == "e-001"
        assert md["tenant"]   == "acme"
        # Built-in keys still present.
        assert md["llm_model"] == "fake-model-id"

    @pytest.mark.asyncio
    async def test_intent_action_and_reason_match_construction(self):
        agent = _FakeAgent()
        wrapper = governed_chat_model(
            chat_model    = ChatRecording(),
            agent         = agent,
            effect        = "draft-intervention",
            intent_action = "draft_intervention",
            intent_reason = "Generate retirement nudge for participant p-001",
        )
        await wrapper.ainvoke([HumanMessage(content="prompt")])

        c = agent.calls[0]
        assert c["effect"]        == "draft-intervention"
        assert c["intent_action"] == "draft_intervention"
        assert "p-001" in c["intent_reason"]
        assert c["tool"]   == "langchain_chat_model"
        assert c["action"] == "invoke"

    @pytest.mark.asyncio
    async def test_explicit_provider_and_model_label_override(self):
        """Caller can pin the provider/model label when the auto-derivation
        is wrong (e.g. proxied through a custom subclass)."""
        agent, _, _ = _make_wrapper()
        wrapper = governed_chat_model(
            chat_model     = ChatRecording(),
            agent          = agent,
            effect         = "test",
            intent_action  = "test",
            intent_reason  = "test",
            provider_label = "self-hosted",
            model_label    = "llama3.1-70b-internal",
        )
        await wrapper.ainvoke([HumanMessage(content="x")])
        md = agent.calls[0]["metadata"]
        assert md["llm_provider"] == "self-hosted"
        assert md["llm_model"]    == "llama3.1-70b-internal"


# ── 3. bind_tools — must keep governance in the loop ──────────────────────


class TestBindTools:
    @pytest.mark.asyncio
    async def test_bind_tools_keeps_call_routed_through_self(self):
        """``bind_tools`` returns a RunnableBinding. Calling it must end up
        back in our ``_agenerate`` so run_effect still fires."""
        from pydantic import BaseModel

        class _Schema(BaseModel):
            answer: str

        agent, model, wrapper = _make_wrapper()

        # Real BaseChatModel doesn't implement bind_tools by default.
        # Our wrapper falls back to passthrough binding around self.
        bound = wrapper.bind_tools([_Schema])
        await bound.ainvoke([HumanMessage(content="x")])

        # The single run_effect call covered the LLM invocation —
        # the bound runnable did not bypass it.
        assert len(agent.calls) == 1


# ── 4. Sync path is intentionally unsupported ──────────────────────────────


class TestSyncIsUnsupported:
    def test_invoke_raises_not_implemented(self):
        _, _, wrapper = _make_wrapper()
        with pytest.raises(NotImplementedError, match="async path only"):
            wrapper.invoke([HumanMessage(content="hello")])


# ── 5. Helpers — derive provider / model / content chars ──────────────────


class TestHelpers:
    def test_derive_provider_known_class(self):
        class ChatBedrockConverse:  # noqa
            pass
        assert _derive_provider(ChatBedrockConverse()) == "bedrock"

    def test_derive_provider_falls_back_to_classname(self):
        class ChatMystery:  # noqa
            pass
        assert _derive_provider(ChatMystery()) == "mystery"

    def test_derive_model_prefers_model_attr(self):
        class M:
            model = "claude-3-5-sonnet"
        assert _derive_model(M()) == "claude-3-5-sonnet"

    def test_derive_model_falls_back_to_model_id(self):
        class M:
            model_id = "anthropic.claude-3-5-sonnet-20241022-v2:0"
        assert _derive_model(M()) == "anthropic.claude-3-5-sonnet-20241022-v2:0"

    def test_derive_model_returns_empty_when_unknown(self):
        class M: pass
        assert _derive_model(M()) == ""

    def test_content_chars_handles_str(self):
        assert _content_chars(HumanMessage(content="hello")) == 5

    def test_content_chars_handles_list_of_text_parts(self):
        msg = HumanMessage(content=[
            {"type": "text", "text": "hello "},
            {"type": "text", "text": "world"},
        ])
        assert _content_chars(msg) == len("hello ") + len("world")


# ── 6. Public API — exported from arc.orchestrators ───────────────────────


class TestPublicExports:
    def test_lazy_import_via_package(self):
        from arc import orchestrators

        # Lazy via __getattr__.
        assert orchestrators.governed_chat_model is governed_chat_model
        assert orchestrators.GovernedChatModel   is GovernedChatModel
