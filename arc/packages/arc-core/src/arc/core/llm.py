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
