"""
arc.connectors.bedrock_llm
──────────────────────────────────
Amazon Bedrock LLM client for arc agents — implements ``arc.core.LLMClient``.

Routes every Claude invocation through ``agent.run_effect()`` so LLM calls
are policy-enforced, audit-logged, and counted against the agent's declared
manifest — exactly like any other tool call.

The client is **stateless w.r.t. the agent**: the agent passes itself in at
call time, not at construction. One ``BedrockLLMClient`` can be shared
across agents.

Install:
    pip install "arc-connectors[aws]"

Usage:

    from arc.connectors import BedrockLLMClient

    llm = BedrockLLMClient(
        model_id="anthropic.claude-3-5-sonnet-20241022-v2:0",
        region="us-east-1",
    )

    class RetirementTrajectoryAgent(BaseAgent):
        def __init__(self, *args, llm: LLMClient | None = None, **kwargs):
            super().__init__(*args, **kwargs)
            self.llm = llm

        async def execute(self, **kwargs):
            text = await self.llm.generate(
                agent=self,
                effect=FinancialEffect.INTERVENTION_DRAFT,
                intent_action="draft_intervention",
                intent_reason="Personalise retirement message for at-risk participant",
                system="You are a retirement planning assistant...",
                prompt="Write a 2-sentence retirement savings nudge for ...",
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

logger = logging.getLogger(__name__)

# Default Bedrock model — Claude Sonnet is the best value for financial services
DEFAULT_MODEL_ID = "anthropic.claude-3-5-sonnet-20241022-v2:0"
BEDROCK_API_VERSION = "bedrock-2023-05-31"


class BedrockLLMClient:
    """
    Calls Claude on Amazon Bedrock, routing each call through the agent's
    ``run_effect()``. Conforms to ``arc.core.LLMClient``.

    Why route LLM calls through run_effect()?
      - Every Claude invocation is audit-logged with its intent and effect.
      - The policy engine can DENY or ASK-for-approval on sensitive prompts.
      - Token counts and call rates are tracked in the outcome store.
      - The manifest must declare the effect — teams can't silently add
        LLM calls without going through the registry review.

    The agent must declare the effect(s) it uses in ``manifest.allowed_effects``.
    Typically Tier 3 (Draft) or Tier 4 (Output).
    """

    def __init__(
        self,
        *,
        model_id: str = DEFAULT_MODEL_ID,
        region: str | None = None,
        max_retries: int = 3,
        redactor: Any = None,
    ):
        """
        Args:
            model_id:    Bedrock model ID for Claude.
            region:      AWS region (defaults to boto3 session default /
                         AWS_DEFAULT_REGION).
            max_retries: Number of retries on throttling / transient errors.
            redactor:    Optional ``arc.core.Redactor`` instance. When set,
                         every prompt + system message is redacted *before*
                         leaving the trust boundary into Bedrock. Defaults
                         to ``None`` — no redaction; opt-in.
                         For regulated domains (PII / SSN / account numbers),
                         pass ``Redactor()`` here. The original (un-redacted)
                         prompt is never stored or logged either way; only
                         ``prompt_chars`` lands in the audit row.
        """
        self.model_id    = model_id
        self.region      = region
        self.max_retries = max_retries
        self.redactor    = redactor
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
        Call Claude via Bedrock and return the response text.

        Goes through ``agent.run_effect()`` → ControlTower → policy engine.
        The prompt is NOT stored in the policy engine — only the effect,
        intent, token estimate, and model ID are audit-logged.

        Args:
            agent:          The BaseAgent making the call. Provides
                            ``run_effect`` and the active manifest.
            effect:         Domain effect to log (must be in manifest.allowed_effects).
            intent_action:  Short descriptor (e.g., "draft_intervention").
            intent_reason:  Human-readable reason for the audit log.
            prompt:         User-turn prompt.
            system:         Optional system prompt.
            max_tokens:     Maximum tokens in the response.
            temperature:    Sampling temperature (0.0 = deterministic, 1.0 = creative).
            metadata:       Extra metadata for policy ``when:`` conditions.

        Returns:
            Claude's response text.
        """
        # Redact PII before the prompt leaves our trust boundary. The
        # audit log captures only `prompt_chars` (length), not the prompt
        # itself, so we redact for the *Bedrock* request specifically.
        outbound_prompt = prompt
        outbound_system = system
        if self.redactor is not None:
            outbound_prompt = self.redactor.redact_text(prompt)
            outbound_system = self.redactor.redact_text(system) if system else system

        prompt_tokens = len(prompt.split())  # rough estimate for logging

        async def _exec_fn():
            return await asyncio.to_thread(
                self._invoke_bedrock,
                prompt=outbound_prompt,
                system=outbound_system,
                max_tokens=max_tokens,
                temperature=temperature,
            )

        result = await agent.run_effect(
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
                "llm_provider": "bedrock",
                "prompt_chars": len(prompt),
                **(metadata or {}),
            },
            exec_fn=_exec_fn,
        )

        # Telemetry: token + char counts for LLM cost dashboards. Best-effort.
        # We don't have exact token counts here without parsing the Bedrock
        # response usage block; chars + word-estimate are good enough for
        # trend graphs and cost-per-run estimation.
        try:
            tel = getattr(agent, "telemetry", None)
            if tel is not None:
                tags = {
                    "agent_id": agent.manifest.agent_id,
                    "model":    self.model_id,
                    "provider": "bedrock",
                }
                tel.count("arc.llm.tokens_in",  float(prompt_tokens), tags=tags)
                tel.count("arc.llm.chars_out",  float(len(result)),    tags=tags)
        except Exception as exc:  # noqa: BLE001
            logger.debug("bedrock_telemetry_emit_failed err=%s", exc)

        logger.debug(
            "bedrock_generate model=%s effect=%s chars=%d",
            self.model_id, getattr(effect, "value", effect), len(result),
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
        Call Claude and parse the response as JSON.

        Appends a JSON-only system instruction. Strips any accidental
        code fences. Raises ``ValueError`` if the response isn't valid JSON.
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
