"""arc.orchestrators.langchain_chat_model — governed wrapper for any LangChain BaseChatModel.

Closes the LangGraph governance gap: LangGraph nodes that use
``langchain_aws.ChatBedrockConverse`` (or any other LangChain
``BaseChatModel``) directly bypass ``agent.run_effect()`` — the model
call never reaches ControlTower, so there's no policy check and no
audit row for the LLM invocation itself.

This module provides ``GovernedChatModel``, a drop-in replacement that
wraps any ``BaseChatModel`` and routes every ``invoke`` / ``ainvoke``
through ``agent.run_effect()`` — the same path the ``LLMClient``
implementations in ``arc.connectors`` use.

Usage:

    from arc.orchestrators.langchain_chat_model import governed_chat_model
    from langchain_aws import ChatBedrockConverse
    from arc.core.effects import ITSMEffect

    raw_llm = ChatBedrockConverse(model="anthropic.claude-3-5-sonnet-20241022-v2:0")
    llm = governed_chat_model(
        chat_model    = raw_llm,
        agent         = self,                         # the BaseAgent
        effect        = ITSMEffect.EMAIL_CLASSIFY,
        intent_action = "classify_email",
        intent_reason = "Classify intent and priority for email {email_id}",
    )

    # Use exactly like ChatBedrockConverse:
    structured = llm.with_structured_output(Classification)
    result = await structured.ainvoke([HumanMessage(content=prompt)])
    # ↑ This call now routes through agent.run_effect → ControlTower.
    # Policy fires, audit row lands, with metadata:
    #   {"llm_provider": "bedrock", "llm_model": "...",
    #    "prompt_chars": 1234, "message_count": 1}

The wrapper preserves ``bind_tools`` and ``with_structured_output``
semantics — both go through governance because the rebinding is around
``self``, not the wrapped model.

Async-only: ``BaseChatModel._generate`` (sync) raises
``NotImplementedError``. LangGraph and the harness are async by design.
"""

from __future__ import annotations

import logging
from typing import Any, Sequence

from langchain_core.callbacks import (
    AsyncCallbackManagerForLLMRun,
    CallbackManagerForLLMRun,
)
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatResult
from pydantic import ConfigDict, PrivateAttr


logger = logging.getLogger(__name__)


# ── Provider / model label inference ────────────────────────────────────────
#
# When the caller doesn't pin a provider/model label explicitly, we derive
# them from the wrapped chat model. The mapping covers the common cases
# (Bedrock / OpenAI / Anthropic / Vertex). Unknown providers fall back to
# the lowercased class name with the leading ``Chat`` stripped.

_PROVIDER_FROM_CLASS = {
    "ChatBedrockConverse": "bedrock",
    "ChatBedrock":         "bedrock",
    "ChatOpenAI":          "openai",
    "ChatAnthropic":       "anthropic",
    "ChatVertexAI":        "vertex_ai",
    "ChatLiteLLM":         "litellm",
    "ChatOllama":          "ollama",
    "ChatCohere":          "cohere",
    "ChatGroq":            "groq",
}


def _derive_provider(chat_model: BaseChatModel) -> str:
    cls = type(chat_model).__name__
    if cls in _PROVIDER_FROM_CLASS:
        return _PROVIDER_FROM_CLASS[cls]
    name = cls
    if name.startswith("Chat"):
        name = name[len("Chat"):]
    return name.lower() or "unknown"


def _derive_model(chat_model: BaseChatModel) -> str:
    """Best-effort model id lookup.

    LangChain provider classes use ``model`` (most), ``model_id``
    (Bedrock), or ``model_name`` (OpenAI). Try each in order; return
    empty string if none match — caller can pass ``model_label``
    explicitly when they need a stable id.
    """
    for attr in ("model", "model_id", "model_name"):
        val = getattr(chat_model, attr, None)
        if val:
            return str(val)
    return ""


def _content_chars(message: BaseMessage) -> int:
    """Length-in-characters of one message's content.

    LangChain messages can carry str content, a list of content parts,
    or other shapes. We only count the str path precisely; anything
    else falls back to ``len(str(...))`` which is good enough for
    audit telemetry.
    """
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        total = 0
        for part in content:
            if isinstance(part, str):
                total += len(part)
            elif isinstance(part, dict):
                # Common shapes: {"type": "text", "text": "..."}
                text = part.get("text") if isinstance(part.get("text"), str) else None
                total += len(text) if text else len(str(part))
            else:
                total += len(str(part))
        return total
    return len(str(content)) if content is not None else 0


# ── The wrapper ─────────────────────────────────────────────────────────────


class GovernedChatModel(BaseChatModel):
    """Route every LangChain chat-model call through ``agent.run_effect()``.

    Pin the wrapper to one (effect, intent_action, intent_reason) triple
    at construction — that's the semantic action the wrapped model
    represents in this node (e.g. ``ITSMEffect.EMAIL_CLASSIFY`` +
    ``"classify_email"``). LangGraph nodes naturally have one semantic
    action per LLM call, so per-construction binding is the right grain.

    Audit metadata mirrors ``arc.connectors.BedrockLLMClient`` /
    ``LiteLLMClient`` exactly:

      ``llm_provider``   "bedrock" / "openai" / …  (auto-derived)
      ``llm_model``      provider-specific id      (auto-derived)
      ``prompt_chars``   summed message content length
      ``message_count``  number of messages in the call
      …                  any extras passed via ``metadata={...}``

    ``bind_tools`` and ``with_structured_output`` are preserved: the
    rebinding wraps ``self`` so any subsequent call still flows through
    ``run_effect``.
    """

    # Allow non-pydantic types (BaseAgent, effect enums) without bespoke
    # validators — these are private attrs anyway.
    model_config = ConfigDict(arbitrary_types_allowed=True)

    _wrapped:        BaseChatModel = PrivateAttr()
    _agent:          Any           = PrivateAttr()
    _effect:         Any           = PrivateAttr()
    _intent_action:  str           = PrivateAttr()
    _intent_reason:  str           = PrivateAttr()
    _tool:           str           = PrivateAttr()
    _action:         str           = PrivateAttr()
    _extra_meta:     dict[str, Any] | None = PrivateAttr(default=None)
    _provider_label: str           = PrivateAttr()
    _model_label:    str           = PrivateAttr()

    def __init__(
        self,
        *,
        chat_model:     BaseChatModel,
        agent:          Any,
        effect:         Any,
        intent_action:  str,
        intent_reason:  str,
        tool:           str = "langchain_chat_model",
        action:         str = "invoke",
        metadata:       dict[str, Any] | None = None,
        provider_label: str | None = None,
        model_label:    str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._wrapped        = chat_model
        self._agent          = agent
        self._effect         = effect
        self._intent_action  = intent_action
        self._intent_reason  = intent_reason
        self._tool           = tool
        self._action         = action
        self._extra_meta     = dict(metadata) if metadata else None
        self._provider_label = provider_label or _derive_provider(chat_model)
        self._model_label    = model_label    or _derive_model(chat_model)

    # ── Identity ────────────────────────────────────────────────────────

    @property
    def _llm_type(self) -> str:  # required by BaseChatModel
        return f"governed-{self._provider_label}"

    @property
    def wrapped_model(self) -> BaseChatModel:
        """Expose the underlying chat model — useful for tests."""
        return self._wrapped

    # ── Sync path: not supported. LangGraph + arc are async. ───────────

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        raise NotImplementedError(
            "GovernedChatModel supports the async path only "
            "(ainvoke / astream / abatch). LangGraph and arc are async "
            "by design; sync calls would block the run_effect coroutine. "
            "Drive the call with `await llm.ainvoke(...)` or "
            "`asyncio.run(...)` from a sync entry point."
        )

    # ── Async path: the real work happens here ─────────────────────────

    async def _agenerate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: AsyncCallbackManagerForLLMRun | None = None,
        **kwargs: Any,
    ) -> ChatResult:
        """Route the model call through ``agent.run_effect()``.

        ``run_effect`` does the policy + audit work, then invokes
        ``_exec_fn`` only on ALLOW. The wrapped model's ``_agenerate``
        is what ``_exec_fn`` calls — so the actual provider request
        only fires after governance approves.
        """
        prompt_chars  = sum(_content_chars(m) for m in messages)
        message_count = len(messages)

        async def _exec_fn() -> ChatResult:
            return await self._wrapped._agenerate(
                messages,
                stop=stop,
                run_manager=run_manager,
                **kwargs,
            )

        result = await self._agent.run_effect(
            effect        = self._effect,
            tool          = self._tool,
            action        = self._action,
            params        = {
                "model":         self._model_label,
                "message_count": message_count,
                "prompt_chars":  prompt_chars,
            },
            intent_action = self._intent_action,
            intent_reason = self._intent_reason,
            metadata      = {
                "llm_provider":  self._provider_label,
                "llm_model":     self._model_label,
                "prompt_chars":  prompt_chars,
                "message_count": message_count,
                **(self._extra_meta or {}),
            },
            exec_fn       = _exec_fn,
        )

        logger.debug(
            "governed_chat_model invoke provider=%s model=%s effect=%s "
            "messages=%d chars=%d",
            self._provider_label, self._model_label, self._effect,
            message_count, prompt_chars,
        )
        return result

    # ── bind_tools — preserve governance through tool binding ──────────

    def bind_tools(
        self,
        tools: Sequence[Any],
        *,
        tool_choice: Any = None,
        **kwargs: Any,
    ):
        """Bind tools while keeping the call routed through ``self``.

        The wrapped model's ``bind_tools`` is used for tool-format
        conversion (each provider has its own tool schema), and the
        converted kwargs are then re-bound around ``self`` so a
        subsequent ``ainvoke`` still flows through ``_agenerate``.

        Without this, ``self._wrapped.bind_tools(tools)`` would return
        a ``RunnableBinding`` over the wrapped model — invoking it
        would skip ``run_effect``, which is exactly the bug we're
        fixing.
        """
        # Round-trip the tools through the wrapped model so we get the
        # provider-specific converted form (e.g. JSON schema → Bedrock
        # tool format). The ``RunnableBinding`` we get back wraps the
        # wrapped model; we discard the binding and re-bind around self.
        bind_kwargs: dict[str, Any] = {}
        if tool_choice is not None:
            bind_kwargs["tool_choice"] = tool_choice
        bind_kwargs.update(kwargs)

        try:
            inner = self._wrapped.bind_tools(tools, **bind_kwargs)
            converted = (
                dict(inner.kwargs) if hasattr(inner, "kwargs") else None
            )
        except NotImplementedError:
            # Wrapped model doesn't support tool binding natively — fall
            # through to raw passthrough.
            converted = None

        if converted is None:
            # Provider doesn't expose converted form — pass tools as-is.
            converted = {"tools": list(tools), **bind_kwargs}

        # ``Runnable.bind`` returns a ``RunnableBinding`` over self.
        return self.bind(**converted)


# ── Factory ─────────────────────────────────────────────────────────────────


def governed_chat_model(
    *,
    chat_model:     BaseChatModel,
    agent:          Any,
    effect:         Any,
    intent_action:  str,
    intent_reason:  str,
    tool:           str = "langchain_chat_model",
    action:         str = "invoke",
    metadata:       dict[str, Any] | None = None,
    provider_label: str | None = None,
    model_label:    str | None = None,
) -> GovernedChatModel:
    """Wrap a LangChain ``BaseChatModel`` for governed invocation.

    Thin factory that constructs a ``GovernedChatModel``. Prefer it
    over the class constructor; the factory keeps the call sites
    declarative and gives us a single hook for future defaults.

    Args:
        chat_model:     The wrapped model (any ``BaseChatModel`` —
                        ``ChatBedrockConverse``, ``ChatOpenAI``, etc.).
        agent:          The ``BaseAgent`` instance whose ``run_effect``
                        the wrapper calls. Must be the live agent so
                        the manifest + ControlTower it owns are in scope.
        effect:         Domain effect for the audit row. Must appear in
                        ``agent.manifest.allowed_effects``.
        intent_action:  Short verb-shaped descriptor (``"classify_email"``).
        intent_reason:  Human-readable reason for compliance review.
        tool:           Audit-row ``tool`` name. Defaults to
                        ``"langchain_chat_model"``.
        action:         Audit-row ``action`` verb. Defaults to ``"invoke"``.
        metadata:       Extra keys merged into the audit metadata dict.
        provider_label: Override the auto-derived provider name.
        model_label:    Override the auto-derived model id.

    Returns:
        A ``GovernedChatModel`` ready to drop into a LangGraph node.
    """
    return GovernedChatModel(
        chat_model     = chat_model,
        agent          = agent,
        effect         = effect,
        intent_action  = intent_action,
        intent_reason  = intent_reason,
        tool           = tool,
        action         = action,
        metadata       = metadata,
        provider_label = provider_label,
        model_label    = model_label,
    )


__all__ = ["GovernedChatModel", "governed_chat_model"]
