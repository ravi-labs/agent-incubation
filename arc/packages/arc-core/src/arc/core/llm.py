"""
arc.core.llm — provider-agnostic LLM client abstraction.

Every LLM call from an arc agent flows through this Protocol, regardless
of which backend (Bedrock, LiteLLM, future providers) is wired underneath.
Two guarantees:

  1. Every call routes through ``agent.run_effect(...)`` — so the prompt,
     model, and outcome are policy-evaluated and audit-logged like any
     other tool call. The client never bypasses ControlTower.

  2. The client is **stateless w.r.t. the agent**. The agent passes
     itself in at call time rather than at construction. This lets a
     single LLM client be shared across agents and avoids the
     ``Client(agent=self)`` circular pattern in agent __init__.

Two implementations ship in arc-connectors:

  - ``arc.connectors.bedrock_llm.BedrockLLMClient``   (boto3 + Anthropic-on-Bedrock)
  - ``arc.connectors.litellm_client.LiteLLMClient``    (LiteLLM → 100+ providers)

Both have the same surface; agents accept an ``LLMClient`` (this Protocol)
and don't care which is wired.

Example agent:

    from arc.core import LLMClient

    class MyAgent(BaseAgent):
        def __init__(self, *args, llm: LLMClient | None = None, **kwargs):
            super().__init__(*args, **kwargs)
            self.llm = llm

        async def execute(self, **kwargs):
            if self.llm:
                text = await self.llm.generate(
                    agent=self,
                    effect=FinancialEffect.INTERVENTION_DRAFT,
                    intent_action="draft",
                    intent_reason="...",
                    prompt="...",
                )

Caller picks the backend:

    # Bedrock (AWS-native, boto3 + IAM)
    from arc.connectors import BedrockLLMClient
    llm = BedrockLLMClient(model_id="anthropic.claude-3-5-sonnet-20241022-v2:0")

    # LiteLLM (cross-provider — Anthropic, OpenAI, Bedrock, Vertex, Ollama, …)
    from arc.connectors import LiteLLMClient
    llm = LiteLLMClient(model="anthropic/claude-3-5-sonnet")

    agent = MyAgent(manifest, tower, gateway, llm=llm)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class LLMClient(Protocol):
    """Provider-agnostic LLM client.

    Both methods take the calling ``agent`` so they can route through
    ``agent.run_effect(...)`` for policy + audit. ``effect`` is the
    domain-specific enum value the agent declares in its manifest
    (FinancialEffect, HealthcareEffect, LegalEffect, ITSMEffect,
    ComplianceEffect, …).
    """

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
        """Call the model and return the response text.

        The implementation MUST route through ``agent.run_effect(...)``.
        Failure modes:
          - PermissionError      effect not declared in manifest
          - TollgateDenied       policy says no
          - TollgateDeferred     policy needs human review
          - RuntimeError         provider failure after configured retries
        """
        ...

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
        """Same as ``generate`` but returns a parsed JSON object.

        Implementations append a JSON-only system instruction, strip any
        accidental code fences in the response, and parse to ``dict``.
        Raises ``ValueError`` if the response isn't valid JSON.
        """
        ...


# ── Provider-agnostic config + factory ──────────────────────────────────────


@dataclass
class LLMConfig:
    """Declarative spec for which LLMClient to construct.

    The same dataclass serves three roles:

      1. **Platform default** — set once on ``RuntimeConfig.llm`` (read from
         ``ARC_LLM_*`` env vars by default). Both ``HarnessBuilder`` and
         ``RuntimeBuilder`` use it to construct the LLMClient injected into
         every agent that doesn't override.

      2. **Per-agent override** — declared in the agent's ``manifest.yaml``
         under the optional ``llm:`` key. Takes precedence over the platform
         default. Visible in the registry PR so compliance can review which
         provider an agent uses.

      3. **Programmatic override** — passed directly to a builder via
         ``with_llm(client)`` or ``build(Agent, llm=client)``. Highest
         precedence; intended for tests and one-off scripts.

    Field semantics:

      provider           "bedrock" | "litellm" | "" (no LLM)
      model              provider-specific id. For LiteLLM use the
                         ``provider/name`` form (e.g. ``"openai/gpt-4o"``);
                         for Bedrock use the model id (e.g.
                         ``"anthropic.claude-3-5-sonnet-20241022-v2:0"``).
      region             AWS region (Bedrock only).
      fallback_models    LiteLLM fallback chain — tried on rate-limit /
                         transient errors before giving up.
      api_base           Override base URL (LiteLLM, e.g. self-hosted
                         Ollama or LiteLLM proxy).
      max_retries        Provider-internal retry count.
    """
    provider: str = ""                                # "bedrock" | "litellm" | ""
    model: str = ""                                   # provider-specific id
    region: str = ""                                  # bedrock only
    fallback_models: list[str] = field(default_factory=list)
    api_base: str = ""                                # litellm only
    max_retries: int = 3

    # ── Construction ────────────────────────────────────────────────────

    @classmethod
    def from_env(cls) -> "LLMConfig":
        """Read the platform default from environment variables.

        Recognised vars:

          ARC_LLM_PROVIDER          "bedrock" | "litellm" | "" (default: empty → no LLM)
          ARC_LLM_MODEL             provider-specific model id
          ARC_LLM_REGION            AWS region (Bedrock); falls back to AWS_REGION
          ARC_LLM_FALLBACK_MODELS   comma-separated list (LiteLLM)
          ARC_LLM_API_BASE          base URL override (LiteLLM)
          ARC_LLM_MAX_RETRIES       integer; default 3

        An empty ``ARC_LLM_PROVIDER`` returns a config that builds no client
        (``build_client()`` returns None) — the agent runs without an LLM.
        """
        fallbacks_raw = os.getenv("ARC_LLM_FALLBACK_MODELS", "").strip()
        fallbacks = (
            [m.strip() for m in fallbacks_raw.split(",") if m.strip()]
            if fallbacks_raw else []
        )
        return cls(
            provider        = os.getenv("ARC_LLM_PROVIDER", "").strip().lower(),
            model           = os.getenv("ARC_LLM_MODEL", "").strip(),
            region          = os.getenv("ARC_LLM_REGION", os.getenv("AWS_REGION", "")).strip(),
            fallback_models = fallbacks,
            api_base        = os.getenv("ARC_LLM_API_BASE", "").strip(),
            max_retries     = int(os.getenv("ARC_LLM_MAX_RETRIES", "3")),
        )

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "LLMConfig":
        """Parse from a manifest YAML dict (the optional ``llm:`` block).

        Unknown keys are ignored so manifests forward-compatible across
        new fields. Missing fields fall back to dataclass defaults.
        """
        return cls(
            provider        = str(d.get("provider", "")).strip().lower(),
            model           = str(d.get("model", "")).strip(),
            region          = str(d.get("region", "")).strip(),
            fallback_models = list(d.get("fallback_models") or []),
            api_base        = str(d.get("api_base", "")).strip(),
            max_retries     = int(d.get("max_retries", 3)),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize for manifest YAML round-trip. Empty fields are omitted."""
        out: dict[str, Any] = {}
        if self.provider:        out["provider"]        = self.provider
        if self.model:           out["model"]           = self.model
        if self.region:          out["region"]          = self.region
        if self.fallback_models: out["fallback_models"] = list(self.fallback_models)
        if self.api_base:        out["api_base"]        = self.api_base
        if self.max_retries != 3: out["max_retries"]    = self.max_retries
        return out

    # ── The factory ─────────────────────────────────────────────────────

    def is_empty(self) -> bool:
        """True if no provider is configured — ``build_client()`` returns None."""
        return not self.provider

    def build_client(self) -> "LLMClient | None":
        """Construct the LLMClient described by this config.

        Returns ``None`` when ``provider`` is empty (no LLM configured).
        Raises ``ValueError`` for an unknown provider.
        Raises ``ImportError`` when the chosen provider's optional extra
        is not installed (e.g. ``arc-connectors[litellm]`` missing).

        The arc.connectors imports are lazy so arc-core itself does not
        depend on arc-connectors.
        """
        if self.is_empty():
            return None

        if self.provider == "bedrock":
            from arc.connectors import BedrockLLMClient
            kwargs: dict[str, Any] = {"max_retries": self.max_retries}
            if self.model:
                kwargs["model_id"] = self.model
            if self.region:
                kwargs["region"] = self.region
            return BedrockLLMClient(**kwargs)

        if self.provider == "litellm":
            if not self.model:
                raise ValueError(
                    "LLMConfig(provider='litellm') requires a model id "
                    "(set ARC_LLM_MODEL or the manifest's llm.model)."
                )
            from arc.connectors import LiteLLMClient
            kwargs = {"model": self.model, "max_retries": self.max_retries}
            if self.fallback_models:
                kwargs["fallback_models"] = list(self.fallback_models)
            if self.api_base:
                kwargs["api_base"] = self.api_base
            return LiteLLMClient(**kwargs)

        raise ValueError(
            f"Unknown LLM provider: {self.provider!r}. "
            "Valid values: 'bedrock', 'litellm', or '' for no LLM."
        )


# ── Precedence resolver ────────────────────────────────────────────────────


def resolve_llm(
    *,
    explicit: "LLMClient | None" = None,
    manifest_config: LLMConfig | None = None,
    platform_default: LLMConfig | None = None,
) -> "LLMClient | None":
    """Pick the LLMClient an agent should use given the three sources.

    Precedence (highest → lowest):

      1. ``explicit``         — passed to a builder directly. For tests +
                                one-off scripts. Wins over everything.
      2. ``manifest_config``  — the agent's manifest declares ``llm:``.
                                Visible in the registry; takes precedence
                                over the platform default so an agent can
                                say "I need GPT-4o for this use case" even
                                when the platform default is Bedrock.
      3. ``platform_default`` — ``RuntimeConfig.llm`` from env vars.
                                The fallback every agent gets unless
                                they override.
      4. ``None``             — agent runs without an LLM (algorithmic
                                path for templates / classifiers that
                                don't need a model).

    The resolver doesn't construct anything itself — it just delegates to
    ``LLMConfig.build_client()`` once it picks the winning config.
    """
    if explicit is not None:
        return explicit

    if manifest_config is not None and not manifest_config.is_empty():
        return manifest_config.build_client()

    if platform_default is not None and not platform_default.is_empty():
        return platform_default.build_client()

    return None
