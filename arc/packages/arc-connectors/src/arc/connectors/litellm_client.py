"""
arc.connectors.litellm_client
──────────────────────────────
LiteLLM-backed LLM client for arc agents — implements ``arc.core.LLMClient``.

LiteLLM (https://github.com/BerriAI/litellm) is a unified Python interface
to 100+ model providers. One ``generate`` call, the ``model`` string picks
the backend:

    "anthropic/claude-3-5-sonnet-20241022"
    "openai/gpt-4o"
    "bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0"
    "vertex_ai/gemini-1.5-pro"
    "ollama/llama3.1"          # local
    …

Same governance guarantees as ``BedrockLLMClient``: every call routes
through ``agent.run_effect(...)`` → ControlTower → policy + audit. The
client is stateless w.r.t. the agent.

Install:
    pip install "arc-connectors[litellm]"

Plus whichever provider's credentials you need set in env vars
(``ANTHROPIC_API_KEY``, ``OPENAI_API_KEY``, ``AWS_*`` for Bedrock,
``GOOGLE_APPLICATION_CREDENTIALS`` for Vertex, …) — LiteLLM handles
provider auth on its own.

Usage:

    from arc.connectors import LiteLLMClient

    llm = LiteLLMClient(
        model="anthropic/claude-3-5-sonnet-20241022",
        # Optional: ordered fallbacks tried on rate-limit / transient errors
        fallback_models=["openai/gpt-4o-mini"],
        max_retries=3,
    )

    class MyAgent(BaseAgent):
        def __init__(self, *args, llm: LLMClient | None = None, **kwargs):
            super().__init__(*args, **kwargs)
            self.llm = llm

        async def execute(self, **kwargs):
            text = await self.llm.generate(
                agent=self,
                effect=FinancialEffect.INTERVENTION_DRAFT,
                intent_action="draft",
                intent_reason="…",
                prompt="…",
            )

Why LiteLLM alongside Bedrock?
  - Multi-provider routing without per-provider client classes.
  - Local-model support (Ollama) for offline harness runs.
  - Built-in fallbacks for throttling — try a cheaper model when the
    primary is rate-limited.
  - Same observability surface across providers (cost, latency, token
    counts) when the platform team standardises on LiteLLM telemetry.

Why keep BedrockLLMClient too?
  - Direct boto3 path is one less dependency for AWS-native deploys.
  - Production-critical agents in regulated domains often want the
    smallest possible blast radius — boto3 is already audited.
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


class LiteLLMClient:
    """
    LLM client backed by LiteLLM. Conforms to ``arc.core.LLMClient``.

    The client is stateless w.r.t. the agent — the agent passes itself
    in at call time. One LiteLLMClient can be shared across agents.
    """

    def __init__(
        self,
        *,
        model: str,
        fallback_models: list[str] | None = None,
        max_retries: int = 3,
        api_base: str | None = None,
        api_key: str | None = None,
        extra_completion_kwargs: dict[str, Any] | None = None,
    ):
        """
        Args:
            model:                 LiteLLM model identifier with provider
                                   prefix, e.g. ``"anthropic/claude-3-5-sonnet"``,
                                   ``"openai/gpt-4o"``, ``"bedrock/anthropic.claude-..."``,
                                   ``"ollama/llama3.1"``.
            fallback_models:       Ordered list of models to fall back to on
                                   transient / rate-limit errors. LiteLLM tries
                                   each in turn before giving up.
            max_retries:           Retries per model attempt before falling
                                   back. LiteLLM handles backoff internally.
            api_base:              Override the provider base URL (e.g., for a
                                   self-hosted Ollama or LiteLLM proxy).
            api_key:               Explicit key. Usually omitted — LiteLLM
                                   reads ``ANTHROPIC_API_KEY`` / ``OPENAI_API_KEY``
                                   / etc. from the environment.
            extra_completion_kwargs:
                                   Passed straight to ``litellm.acompletion``.
                                   Use for provider-specific options (e.g.,
                                   ``{"safety_settings": [...]}`` for Vertex).
        """
        self.model = model
        self.fallback_models = list(fallback_models or [])
        self.max_retries = max_retries
        self.api_base = api_base
        self.api_key = api_key
        self.extra_completion_kwargs = dict(extra_completion_kwargs or {})

    # ── LLMClient protocol surface ─────────────────────────────────────────────

    async def generate(
        self,
        *,
        agent: Any,
        effect: Any,
        intent_action: str,
        intent_reason: str,
        prompt: str,
        system: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.3,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """
        Call the configured model via LiteLLM and return the response text.

        Goes through ``agent.run_effect()`` → ControlTower → policy engine.
        Same audit shape as ``BedrockLLMClient``: only the effect, intent,
        model id, and token estimate are logged — the prompt content is not.
        """
        prompt_tokens = len(prompt.split())   # rough estimate for logging

        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        async def _exec_fn():
            return await self._litellm_acompletion(
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )

        result = await agent.run_effect(
            effect=effect,
            tool="litellm",
            action="completion",
            params={
                "model":         self.model,
                "prompt_tokens": prompt_tokens,
                "max_tokens":    max_tokens,
            },
            intent_action=intent_action,
            intent_reason=intent_reason,
            metadata={
                "llm_model":    self.model,
                "llm_provider": "litellm",
                "prompt_chars": len(prompt),
                **(metadata or {}),
            },
            exec_fn=_exec_fn,
        )

        logger.debug(
            "litellm_generate model=%s effect=%s chars=%d",
            self.model, getattr(effect, "value", effect), len(result),
        )
        return result

    async def generate_json(
        self,
        *,
        agent: Any,
        effect: Any,
        intent_action: str,
        intent_reason: str,
        prompt: str,
        system: str | None = None,
        max_tokens: int = 2048,
        temperature: float = 0.1,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Same as ``generate`` but appends a JSON-only system instruction
        and parses the response. Raises ``ValueError`` on invalid JSON.
        """
        json_system = (
            (system + "\n\n" if system else "") +
            "Always respond with valid JSON only. No prose, no code fences — just the JSON object."
        )

        text = await self.generate(
            agent=agent,
            effect=effect,
            intent_action=intent_action,
            intent_reason=intent_reason,
            prompt=prompt,
            system=json_system,
            max_tokens=max_tokens,
            temperature=temperature,
            metadata=metadata,
        )

        clean = text.strip()
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        try:
            return json.loads(clean)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Model {self.model} returned non-JSON response. "
                f"First 200 chars: {clean[:200]}"
            ) from exc

    # ── Low-level LiteLLM call ─────────────────────────────────────────────────

    async def _litellm_acompletion(
        self,
        messages: list[dict[str, str]],
        max_tokens: int,
        temperature: float,
    ) -> str:
        """Invoke ``litellm.acompletion`` with retries + fallback models."""
        try:
            import litellm  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ImportError(
                "litellm is not installed. Run: pip install 'arc-connectors[litellm]'"
            ) from exc

        kwargs: dict[str, Any] = {
            "model":       self.model,
            "messages":    messages,
            "max_tokens":  max_tokens,
            "temperature": temperature,
            "num_retries": self.max_retries,
            **self.extra_completion_kwargs,
        }
        if self.fallback_models:
            kwargs["fallbacks"] = self.fallback_models
        if self.api_base is not None:
            kwargs["api_base"] = self.api_base
        if self.api_key is not None:
            kwargs["api_key"] = self.api_key

        response = await litellm.acompletion(**kwargs)
        # OpenAI-compatible response shape across every provider
        return response["choices"][0]["message"]["content"]
