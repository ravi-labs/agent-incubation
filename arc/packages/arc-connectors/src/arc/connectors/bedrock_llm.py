"""
arc.connectors.bedrock_llm
──────────────────────────────────
Amazon Bedrock LLM client for arc agents.

Routes every Claude invocation through run_effect() so LLM calls are
policy-enforced, audit-logged, and counted against the agent's declared
manifest — exactly like any other tool call.

Install:
    pip install "arc-connectors[aws]"

Usage inside an agent's execute() or a LangGraph node:

    from arc.connectors.bedrock_llm import BedrockLLMClient
    from arc.core.effects import FinancialEffect

    class RetirementTrajectoryAgent(BaseAgent):

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.llm = BedrockLLMClient(
                agent=self,
                model_id="anthropic.claude-3-5-sonnet-20241022-v2:0",
            )

        async def execute(self, **kwargs):
            draft = await self.llm.generate(
                effect=FinancialEffect.INTERVENTION_DRAFT,
                intent_action="draft_intervention",
                intent_reason="Personalise retirement message for at-risk participant",
                system="You are a retirement planning assistant. Write clear, empathetic messages.",
                prompt=f"Write a 2-sentence retirement savings nudge for {participant['name']}, "
                       f"age {participant['age']}, currently replacing "
                       f"{score['income_replacement_pct']}% of their income.",
            )

Model IDs (Anthropic on Bedrock):
    anthropic.claude-3-5-sonnet-20241022-v2:0   ← recommended
    anthropic.claude-3-5-haiku-20241022-v1:0    ← fast / low cost
    anthropic.claude-3-opus-20240229-v1:0        ← highest capability
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from arc.core.effects import FinancialEffect

logger = logging.getLogger(__name__)

# Default Bedrock model — Claude Sonnet is the best value for financial services
DEFAULT_MODEL_ID = "anthropic.claude-3-5-sonnet-20241022-v2:0"
BEDROCK_API_VERSION = "bedrock-2023-05-31"


class BedrockLLMClient:
    """
    Calls Claude on Amazon Bedrock, routing each call through run_effect().

    Why route LLM calls through run_effect()?
    - Every Claude invocation is audit-logged with its intent and effect
    - The policy engine can DENY or ASK-for-approval on sensitive prompts
    - Token usage and call counts are tracked in the outcome store
    - The manifest must declare the effect — teams can't silently add LLM calls

    The agent must declare the effect(s) used in its manifest's allowed_effects.
    Typically this is a Tier 3 (Draft) or Tier 4 (Output) effect.
    """

    def __init__(
        self,
        agent: Any,          # BaseAgent — typed as Any to avoid circular import
        model_id: str = DEFAULT_MODEL_ID,
        region: str | None = None,
        max_retries: int = 3,
    ):
        """
        Args:
            agent:       The BaseAgent instance (provides run_effect, manifest).
            model_id:    Bedrock model ID for Claude.
            region:      AWS region (defaults to boto3 session default / AWS_DEFAULT_REGION).
            max_retries: Number of retries on throttling / transient errors.
        """
        self.agent = agent
        self.model_id = model_id
        self.region = region
        self.max_retries = max_retries
        self._client: Any = None   # lazy boto3 client

    def _get_client(self) -> Any:
        """Lazy-init boto3 bedrock-runtime client."""
        if self._client is None:
            try:
                import boto3
            except ImportError as exc:
                raise ImportError(
                    "boto3 is not installed. Run: pip install 'arc-connectors[aws]'"
                ) from exc
            kwargs: dict[str, Any] = {}
            if self.region:
                kwargs["region_name"] = self.region
            self._client = boto3.client("bedrock-runtime", **kwargs)
        return self._client

    # ── Core generate ──────────────────────────────────────────────────────────

    async def generate(
        self,
        *,
        effect: FinancialEffect,
        intent_action: str,
        intent_reason: str,
        prompt: str,
        system: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.3,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """
        Call Claude and return the response text.

        The call goes through run_effect() → ControlTower → ERISA policy.
        The prompt is NOT stored in the policy engine — only the effect, intent,
        token count, and model ID are audit-logged.

        Args:
            effect:         FinancialEffect to log (must be in manifest.allowed_effects).
            intent_action:  Short descriptor (e.g., "draft_intervention").
            intent_reason:  Human-readable reason for audit log.
            prompt:         The user-turn prompt for Claude.
            system:         Optional system prompt.
            max_tokens:     Maximum tokens in the response.
            temperature:    Sampling temperature (0.0 = deterministic, 1.0 = creative).
            metadata:       Extra metadata for policy when: conditions.

        Returns:
            Claude's response text.
        """
        prompt_tokens = len(prompt.split())  # rough estimate for logging

        async def _exec_fn():
            return await asyncio.to_thread(
                self._invoke_bedrock,
                prompt=prompt,
                system=system,
                max_tokens=max_tokens,
                temperature=temperature,
            )

        result = await self.agent.run_effect(
            effect=effect,
            tool="bedrock",
            action="invoke_model",
            params={
                "model_id":     self.model_id,
                "prompt_tokens": prompt_tokens,
                "max_tokens":   max_tokens,
            },
            intent_action=intent_action,
            intent_reason=intent_reason,
            metadata={
                "llm_model":    self.model_id,
                "prompt_chars": len(prompt),
                **(metadata or {}),
            },
            exec_fn=_exec_fn,
        )

        logger.debug(
            "bedrock_generate model=%s effect=%s chars=%d",
            self.model_id, effect.value, len(result),
        )
        return result

    async def generate_json(
        self,
        *,
        effect: FinancialEffect,
        intent_action: str,
        intent_reason: str,
        prompt: str,
        system: str | None = None,
        max_tokens: int = 2048,
        temperature: float = 0.1,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """
        Call Claude and parse the response as JSON.

        Appends a JSON instruction to the system prompt automatically.
        Raises ValueError if Claude's response is not valid JSON.

        Returns:
            Parsed dict from Claude's response.
        """
        json_system = (
            (system + "\n\n" if system else "") +
            "Always respond with valid JSON only. No prose, no code fences — just the JSON object."
        )

        text = await self.generate(
            effect=effect,
            intent_action=intent_action,
            intent_reason=intent_reason,
            prompt=prompt,
            system=json_system,
            max_tokens=max_tokens,
            temperature=temperature,
            metadata=metadata,
        )

        # Strip any accidental code fences
        clean = text.strip()
        if clean.startswith("```"):
            clean = clean.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

        try:
            return json.loads(clean)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Claude returned non-JSON response. "
                f"First 200 chars: {clean[:200]}"
            ) from exc

    # ── Low-level Bedrock call ─────────────────────────────────────────────────

    def _invoke_bedrock(
        self,
        prompt: str,
        system: str | None,
        max_tokens: int,
        temperature: float,
    ) -> str:
        """
        Synchronous Bedrock invocation (run in thread pool via asyncio.to_thread).
        Handles throttling with exponential back-off.
        """
        import time

        client = self._get_client()
        body: dict[str, Any] = {
            "anthropic_version": BEDROCK_API_VERSION,
            "max_tokens":        max_tokens,
            "temperature":       temperature,
            "messages": [
                {"role": "user", "content": prompt}
            ],
        }
        if system:
            body["system"] = system

        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                response = client.invoke_model(
                    modelId=self.model_id,
                    body=json.dumps(body),
                    contentType="application/json",
                    accept="application/json",
                )
                result = json.loads(response["body"].read())
                return result["content"][0]["text"]

            except Exception as exc:
                last_exc = exc
                exc_name = type(exc).__name__

                # Retry on throttling or transient service errors
                if "ThrottlingException" in exc_name or "ServiceUnavailable" in exc_name:
                    backoff = 2 ** attempt
                    logger.warning(
                        "Bedrock throttled (attempt %d/%d), retrying in %ds",
                        attempt + 1, self.max_retries, backoff,
                    )
                    time.sleep(backoff)
                else:
                    raise

        raise RuntimeError(
            f"Bedrock invocation failed after {self.max_retries} attempts"
        ) from last_exc
