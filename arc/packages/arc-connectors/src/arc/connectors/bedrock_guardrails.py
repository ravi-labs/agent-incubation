"""
arc.connectors.bedrock_guardrails
─────────────────────────────────────────
Amazon Bedrock Guardrails integration for arc agents.

Bedrock Guardrails is a content filtering and safety layer for LLM inputs and
outputs. This module integrates it as an additional defence layer ALONGSIDE
Tollgate — Tollgate enforces business policy (ALLOW/ASK/DENY on effects),
while Guardrails enforces content safety (PII redaction, topic blocking,
profanity filtering, word filters, grounding checks).

They are complementary, not alternatives:
  - Tollgate:    "Is this agent ALLOWED to do this action?"
  - Guardrails:  "Is the INPUT/OUTPUT content SAFE to process/return?"

Architecture:

    User input
        │
        ▼
    [Guardrails.check_input()] ← Content filtering, PII redaction, topic block
        │
        ▼ (clean input)
    [agent.execute()]
        │
        ▼
    [Guardrails.check_output()] ← Grounding, output filtering
        │
        ▼ (safe output)
    User

Install:
    pip install "arc-connectors[aws]"

Usage — standalone (wrap execute calls):

    from arc.connectors.bedrock_guardrails import BedrockGuardrailsAdapter

    class MyAgent(BaseAgent):

        def __init__(self, manifest, tower, gateway, tracker=None):
            super().__init__(manifest, tower, gateway, tracker)
            self.guardrails = BedrockGuardrailsAdapter(
                guardrail_id="abc123",
                guardrail_version="DRAFT",
                region="us-east-1",
            )

        async def execute(self, user_input: str, **kwargs) -> dict:
            # Check input against Guardrails before processing
            clean_input = await self.guardrails.check_input(
                text=user_input,
                session_id=kwargs.get("session_id", "default"),
            )

            # ... agent logic with clean_input ...
            response = await self._generate(clean_input)

            # Check output before returning
            safe_output = await self.guardrails.check_output(
                text=response,
                session_id=kwargs.get("session_id", "default"),
            )
            return {"response": safe_output}

Usage — mixin pattern (auto-wraps execute()):

    from arc.connectors.bedrock_guardrails import GuardrailsMixin

    class SafeAgent(GuardrailsMixin, BaseAgent):
        guardrail_id      = "abc123"
        guardrail_version = "DRAFT"

        async def execute(self, user_input: str, **kwargs) -> dict:
            # Input is already screened by GuardrailsMixin before this runs
            # Output will be screened before returning to the caller
            return {"response": await self._generate(user_input)}

Guardrail actions:
    - NONE:      Content passed — no intervention.
    - GUARDRAIL_INTERVENED: Content was filtered/blocked/redacted.
      BedrockGuardrailsAdapter raises GuardrailIntervention in this case.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from arc.core import BaseAgent

logger = logging.getLogger(__name__)


# ── Exceptions ─────────────────────────────────────────────────────────────────


class GuardrailIntervention(Exception):
    """
    Raised when Bedrock Guardrails intervenes on content.

    Attributes:
        action:   "GUARDRAIL_INTERVENED"
        reason:   Guardrails assessment reason (topic, PII, etc.)
        outputs:  The blocked/redacted outputs from Guardrails.
    """
    def __init__(self, reason: str, outputs: list | None = None):
        super().__init__(f"Bedrock Guardrails blocked content: {reason}")
        self.reason  = reason
        self.outputs = outputs or []


# ── Assessment result ──────────────────────────────────────────────────────────


@dataclass
class GuardrailAssessment:
    """
    Result of a Bedrock Guardrails applyGuardrail call.

    Attributes:
        action:          "NONE" (passed) or "GUARDRAIL_INTERVENED" (blocked).
        outputs:         List of output content dicts from Guardrails.
        assessments:     Full assessment details (topic, word, PII, etc.)
        usage:           Token usage from the Guardrails API.
        intervened:      True if the guardrail blocked or modified the content.
        safe_text:       The (possibly redacted) safe text if action=NONE.
                         None if blocked entirely.
    """
    action:      str
    outputs:     list = field(default_factory=list)
    assessments: list = field(default_factory=list)
    usage:       dict = field(default_factory=dict)

    @property
    def intervened(self) -> bool:
        return self.action == "GUARDRAIL_INTERVENED"

    @property
    def safe_text(self) -> str | None:
        """Return the output text if the guardrail passed or redacted (not blocked)."""
        if not self.outputs:
            return None
        first = self.outputs[0]
        if isinstance(first, dict):
            return first.get("text", {}).get("text") if "text" in first else None
        return None

    @classmethod
    def from_response(cls, response: dict) -> "GuardrailAssessment":
        return cls(
            action=response.get("action", "NONE"),
            outputs=response.get("outputs", []),
            assessments=response.get("assessments", []),
            usage=response.get("usage", {}),
        )


# ── Adapter ────────────────────────────────────────────────────────────────────


class BedrockGuardrailsAdapter:
    """
    Applies Amazon Bedrock Guardrails to agent inputs and outputs.

    Wraps the bedrock-runtime `apply_guardrail` API with async support
    and structured result handling.

    Args:
        guardrail_id:      The Bedrock Guardrail ID.
        guardrail_version: Guardrail version ("DRAFT" or a version number string).
        region:            AWS region (default: from environment).
        raise_on_block:    If True (default), raise GuardrailIntervention when
                           the guardrail intervenes. If False, return the
                           assessment and let the caller decide.
    """

    def __init__(
        self,
        guardrail_id: str,
        guardrail_version: str = "DRAFT",
        region: str | None = None,
        raise_on_block: bool = True,
    ):
        self.guardrail_id      = guardrail_id
        self.guardrail_version = guardrail_version
        self._region           = region
        self.raise_on_block    = raise_on_block
        self._client: Any      = None

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                import boto3
            except ImportError as exc:
                raise ImportError(
                    "boto3 is required for BedrockGuardrailsAdapter. "
                    "Run: pip install 'arc-connectors[aws]'"
                ) from exc
            self._client = boto3.client("bedrock-runtime", region_name=self._region)
        return self._client

    def _apply_sync(self, source: str, text: str, session_id: str) -> dict:
        """Synchronous apply_guardrail — called via asyncio.to_thread()."""
        client = self._get_client()
        return client.apply_guardrail(
            guardrailIdentifier=self.guardrail_id,
            guardrailVersion=self.guardrail_version,
            source=source,    # "INPUT" or "OUTPUT"
            content=[{"text": {"text": text}}],
        )

    async def _check(
        self,
        source: str,
        text: str,
        session_id: str,
    ) -> GuardrailAssessment:
        """Call apply_guardrail async and return a structured assessment."""
        raw = await asyncio.to_thread(self._apply_sync, source, text, session_id)
        assessment = GuardrailAssessment.from_response(raw)

        logger.info(
            "guardrails_%s id=%s session=%s action=%s",
            source.lower(), self.guardrail_id, session_id, assessment.action,
        )

        if assessment.intervened and self.raise_on_block:
            reasons = [
                a.get("topicPolicy", {}).get("topics", [{}])[0].get("type", "unknown")
                for a in assessment.assessments
                if a
            ]
            reason = ", ".join(str(r) for r in reasons) or "content blocked"
            raise GuardrailIntervention(reason=reason, outputs=assessment.outputs)

        return assessment

    async def check_input(
        self,
        text: str,
        session_id: str = "default",
    ) -> str:
        """
        Apply Guardrails to a user input string.

        Args:
            text:       The raw user input to check.
            session_id: Session identifier for audit context.

        Returns:
            The safe (possibly redacted) input text if the guardrail passed.

        Raises:
            GuardrailIntervention: If the guardrail blocked the input and
                                   raise_on_block=True (default).
        """
        assessment = await self._check("INPUT", text, session_id)
        return assessment.safe_text or text

    async def check_output(
        self,
        text: str,
        session_id: str = "default",
    ) -> str:
        """
        Apply Guardrails to an agent output string before returning to the user.

        Args:
            text:       The raw agent output to check.
            session_id: Session identifier for audit context.

        Returns:
            The safe (possibly redacted) output text.

        Raises:
            GuardrailIntervention: If the guardrail blocked the output and
                                   raise_on_block=True (default).
        """
        assessment = await self._check("OUTPUT", text, session_id)
        return assessment.safe_text or text

    async def check_both(
        self,
        input_text: str,
        output_text: str,
        session_id: str = "default",
    ) -> tuple[str, str]:
        """
        Apply Guardrails to both input and output in parallel.

        Returns:
            Tuple of (safe_input, safe_output).
        """
        input_assessment, output_assessment = await asyncio.gather(
            self._check("INPUT",  input_text,  session_id),
            self._check("OUTPUT", output_text, session_id),
        )
        return (
            input_assessment.safe_text  or input_text,
            output_assessment.safe_text or output_text,
        )


# ── GuardrailsMixin ────────────────────────────────────────────────────────────


class GuardrailsMixin:
    """
    Mixin that auto-wraps execute() with Bedrock Guardrails input/output screening.

    Add to any BaseAgent subclass along with class-level guardrail configuration:

        class SafeAgent(GuardrailsMixin, BaseAgent):
            guardrail_id      = "abc123def456"
            guardrail_version = "1"           # or "DRAFT"
            guardrail_region  = "us-east-1"   # optional

            async def execute(self, user_input: str, **kwargs) -> dict:
                # user_input has already been screened by GuardrailsMixin
                response = await self._generate(user_input)
                # response will be screened before returning
                return {"response": response}

    The mixin intercepts execute() and:
      1. Checks input text (from "user_input", "input", or "message" kwargs)
      2. Calls the real execute() with the safe input
      3. Checks the output text (from "response" or "text" keys if output is dict)

    MRO note: Put GuardrailsMixin BEFORE BaseAgent in the class definition:
        class MyAgent(GuardrailsMixin, BaseAgent): ...   ✓
        class MyAgent(BaseAgent, GuardrailsMixin): ...   ✗  (won't intercept)
    """

    # Set these as class attributes in your subclass
    guardrail_id:      str = ""
    guardrail_version: str = "DRAFT"
    guardrail_region:  str | None = None

    def _get_guardrails_adapter(self) -> BedrockGuardrailsAdapter:
        if not hasattr(self, "_guardrails_adapter") or self._guardrails_adapter is None:  # type: ignore[attr-defined]
            if not self.guardrail_id:
                raise RuntimeError(
                    f"{type(self).__name__} uses GuardrailsMixin but guardrail_id is not set. "
                    "Set guardrail_id as a class attribute."
                )
            self._guardrails_adapter = BedrockGuardrailsAdapter(
                guardrail_id=self.guardrail_id,
                guardrail_version=self.guardrail_version,
                region=self.guardrail_region,
            )
        return self._guardrails_adapter  # type: ignore[attr-defined]

    async def execute(self, **kwargs: Any) -> Any:
        """
        Guardrail-wrapped execute().

        Screens input before passing to the real execute(), then screens
        the output before returning.
        """
        adapter    = self._get_guardrails_adapter()
        session_id = str(kwargs.get("session_id", "default"))

        # ── Screen input ───────────────────────────────────────────────────────
        for key in ("user_input", "input", "message", "query"):
            if key in kwargs and isinstance(kwargs[key], str):
                kwargs[key] = await adapter.check_input(kwargs[key], session_id)
                break

        # ── Call real execute() ────────────────────────────────────────────────
        result = await super().execute(**kwargs)  # type: ignore[misc]

        # ── Screen output ──────────────────────────────────────────────────────
        if isinstance(result, dict):
            for key in ("response", "text", "output", "message", "answer"):
                if key in result and isinstance(result[key], str):
                    result[key] = await adapter.check_output(result[key], session_id)
                    break
        elif isinstance(result, str):
            result = await adapter.check_output(result, session_id)

        return result
