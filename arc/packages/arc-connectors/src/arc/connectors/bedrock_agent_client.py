"""
arc.connectors.bedrock_agent_client
──────────────────────────────────────────
Streaming client for calling deployed Amazon Bedrock Agents.

Provides BedrockAgentStreamingClient — an async wrapper around
bedrock-agent-runtime that supports both streaming and non-streaming
invocation of a Bedrock Agent from within another arc agent.

Install:
    pip install "arc-connectors[aws]"

Usage:

    from arc.connectors.bedrock_agent_client import (
        BedrockAgentStreamingClient,
        AgentChunk,
    )

    class OrchestratorAgent(BaseAgent):

        def __init__(self, manifest, tower, gateway, tracker=None):
            super().__init__(manifest, tower, gateway, tracker)
            self.risk_agent = BedrockAgentStreamingClient(
                agent=self,
                bedrock_agent_id="ABCDEFGHIJ",
                bedrock_agent_alias_id="TSTALIASID",
                intent_action_prefix="invoke_risk_agent",
            )

        async def execute(self, participant_id: str, **kwargs) -> dict:
            # Streaming — yields chunks as they arrive
            response_text = ""
            async for chunk in self.risk_agent.stream_invoke(
                input_text=f"Compute risk score for participant {participant_id}",
                session_id=participant_id,
                intent_reason="Orchestrate risk score computation",
            ):
                if chunk.is_text:
                    response_text += chunk.text
                    print(chunk.text, end="", flush=True)

            return {"response": response_text}

        # Or non-streaming:
        async def execute_simple(self, participant_id: str, **kwargs) -> str:
            return await self.risk_agent.invoke(
                input_text=f"Compute risk score for participant {participant_id}",
                intent_reason="Get risk score from delegated agent",
            )

Streaming response chunks (AgentChunk):
    - chunk.is_text        → contains model-generated text
    - chunk.is_trace       → trace event (orchestration, guardrail, KB, etc.)
    - chunk.is_return_ctrl → returnControl event (action group invocation)
    - chunk.is_done        → final completion event

Policy enforcement:
    Every invocation goes through run_effect() with the
    BEDROCK_AGENT_INVOKE effect, so it is:
      - Declared in the agent manifest's allowed_effects
      - Evaluated against the YAML policy
      - Audit-logged with session ID, input length, and agent IDs
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, TYPE_CHECKING

if TYPE_CHECKING:
    from arc.core import BaseAgent

logger = logging.getLogger(__name__)


# ── Data model ─────────────────────────────────────────────────────────────────


@dataclass
class AgentChunk:
    """
    A single streaming chunk from a Bedrock Agent invocation.

    Attributes:
        text:       Model-generated text (non-empty for text chunks).
        event_type: Raw event type from Bedrock ResponseStream.
        raw:        The full raw event dict from Bedrock.
        metadata:   Parsed metadata (traces, attribution, etc.).
        is_text:    True if this chunk contains model output text.
        is_trace:   True if this is a trace/debug event.
        is_done:    True if this is the final completion event.
        is_return_ctrl: True if Bedrock is returning control to the caller.
    """
    text:         str  = ""
    event_type:   str  = "unknown"
    raw:          dict = field(default_factory=dict)
    metadata:     dict = field(default_factory=dict)

    @property
    def is_text(self) -> bool:
        return bool(self.text)

    @property
    def is_trace(self) -> bool:
        return self.event_type == "trace"

    @property
    def is_done(self) -> bool:
        return self.event_type in ("completion", "done")

    @property
    def is_return_ctrl(self) -> bool:
        return self.event_type == "returnControl"

    @classmethod
    def from_bedrock_event(cls, event: dict) -> "AgentChunk":
        """Parse a single event from Bedrock's ResponseStream."""
        # Text chunk — model output
        if "chunk" in event:
            raw_chunk = event["chunk"]
            text_bytes = raw_chunk.get("bytes", b"")
            if isinstance(text_bytes, (bytes, bytearray)):
                text = text_bytes.decode("utf-8", errors="replace")
            else:
                text = str(text_bytes)
            return cls(
                text=text,
                event_type="chunk",
                raw=event,
                metadata={
                    "attribution": raw_chunk.get("attribution", {}),
                },
            )

        # Trace event — orchestration, guardrail, KB retrieval, etc.
        if "trace" in event:
            trace = event["trace"]
            return cls(
                event_type="trace",
                raw=event,
                metadata={
                    "trace_type":    _get_trace_type(trace),
                    "agent_id":      trace.get("agentId"),
                    "agent_alias":   trace.get("agentAliasId"),
                    "session_id":    trace.get("sessionId"),
                },
            )

        # Return control — Bedrock is delegating back to the caller
        if "returnControl" in event:
            rc = event["returnControl"]
            return cls(
                event_type="returnControl",
                raw=event,
                metadata={
                    "invocation_id":    rc.get("invocationId"),
                    "invocation_inputs": rc.get("invocationInputs", []),
                },
            )

        # Files returned by the agent
        if "files" in event:
            return cls(
                event_type="files",
                raw=event,
                metadata={"files": event["files"].get("files", [])},
            )

        # Unknown event type — surface it for debugging
        key = next(iter(event), "unknown")
        return cls(event_type=key, raw=event)

    def __repr__(self) -> str:
        if self.is_text:
            preview = self.text[:40].replace("\n", "\\n")
            return f"AgentChunk(text={preview!r})"
        return f"AgentChunk(event_type={self.event_type!r})"


def _get_trace_type(trace: dict) -> str:
    """Extract the specific trace type from a Bedrock trace event."""
    inner = trace.get("trace", {})
    for key in (
        "orchestrationTrace", "preProcessingTrace", "postProcessingTrace",
        "guardrailTrace", "failureTrace", "customOrchestrationTrace",
    ):
        if key in inner:
            return key
    return "unknown"


# ── Client ─────────────────────────────────────────────────────────────────────


class BedrockAgentStreamingClient:
    """
    Async streaming client for invoking an Amazon Bedrock Agent.

    All invocations go through agent.run_effect() so they are:
      - Policy-enforced (requires bedrock.agent.invoke in manifest)
      - Audit-logged with session, input length, and agent identifiers
      - Rate-limited and anomaly-detected by Tollgate

    Args:
        agent:                  The calling BaseAgent instance.
        bedrock_agent_id:       Bedrock Agent ID (not arc agent_id).
        bedrock_agent_alias_id: Alias to invoke (e.g. "TSTALIASID" or
                                production alias from register_bedrock_agent()).
        intent_action_prefix:   Prefix for audit intent_action labels.
        enable_trace:           Include trace events in the stream (default True).
        region:                 AWS region (default from environment).
    """

    def __init__(
        self,
        agent: "BaseAgent",
        bedrock_agent_id: str,
        bedrock_agent_alias_id: str,
        intent_action_prefix: str = "invoke_bedrock_agent",
        enable_trace: bool = True,
        region: str | None = None,
    ):
        self.agent                  = agent
        self.bedrock_agent_id       = bedrock_agent_id
        self.bedrock_agent_alias_id = bedrock_agent_alias_id
        self.intent_action_prefix   = intent_action_prefix
        self.enable_trace           = enable_trace
        self._region                = region
        self._client: Any           = None

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                import boto3
            except ImportError as exc:
                raise ImportError(
                    "boto3 is not installed. Run: pip install 'arc-connectors[aws]'"
                ) from exc
            self._client = boto3.client(
                "bedrock-agent-runtime",
                region_name=self._region,
            )
        return self._client

    def _invoke_streaming_sync(
        self,
        input_text: str,
        session_id: str,
        session_state: dict | None,
    ) -> list[AgentChunk]:
        """
        Synchronous streaming invoke — runs in a thread via asyncio.to_thread().

        Consumes the entire ResponseStream and returns a list of AgentChunks.
        """
        client  = self._get_client()
        kwargs: dict[str, Any] = dict(
            agentId=self.bedrock_agent_id,
            agentAliasId=self.bedrock_agent_alias_id,
            sessionId=session_id,
            inputText=input_text,
            enableTrace=self.enable_trace,
        )
        if session_state:
            kwargs["sessionState"] = session_state

        response = client.invoke_agent(**kwargs)

        chunks: list[AgentChunk] = []
        for event in response.get("completion", []):
            chunk = AgentChunk.from_bedrock_event(event)
            chunks.append(chunk)

        logger.debug(
            "bedrock_agent_stream agent_id=%s alias=%s session=%s chunks=%d",
            self.bedrock_agent_id, self.bedrock_agent_alias_id,
            session_id, len(chunks),
        )
        return chunks

    async def stream_invoke(
        self,
        input_text: str,
        *,
        intent_reason: str,
        session_id: str | None = None,
        session_state: dict | None = None,
        metadata: dict | None = None,
    ) -> AsyncIterator[AgentChunk]:
        """
        Invoke the Bedrock Agent and yield AgentChunks as they arrive.

        This is an async generator — iterate over it to process chunks:

            async for chunk in client.stream_invoke(input_text="...", ...):
                if chunk.is_text:
                    process(chunk.text)
                elif chunk.is_trace:
                    log_trace(chunk.metadata)

        Args:
            input_text:    The user input / prompt to send to the Bedrock Agent.
            intent_reason: Justification for the audit trail.
            session_id:    Bedrock session ID (default: auto-generated UUID).
            session_state: Optional Bedrock sessionState dict (session
                           attributes, prompt session attributes, etc.).
            metadata:      Extra metadata for the Tollgate audit event.

        Yields:
            AgentChunk for each streaming event from Bedrock.
        """
        import uuid
        from arc.core.effects import FinancialEffect

        _session_id = session_id or str(uuid.uuid4())

        async def _exec() -> list[AgentChunk]:
            return await asyncio.to_thread(
                self._invoke_streaming_sync,
                input_text,
                _session_id,
                session_state,
            )

        chunks: list[AgentChunk] = await self.agent.run_effect(
            effect=FinancialEffect.BEDROCK_AGENT_INVOKE,
            tool="bedrock-agent-runtime",
            action="invoke_agent",
            params={
                "bedrock_agent_id":       self.bedrock_agent_id,
                "bedrock_agent_alias_id": self.bedrock_agent_alias_id,
                "session_id":             _session_id,
                "input_length":           len(input_text),
            },
            intent_action=f"{self.intent_action_prefix}.stream",
            intent_reason=intent_reason,
            metadata={
                "bedrock_agent_id":       self.bedrock_agent_id,
                "bedrock_agent_alias_id": self.bedrock_agent_alias_id,
                "input_preview":          input_text[:100],
                **(metadata or {}),
            },
            exec_fn=_exec,
        )

        for chunk in chunks:
            yield chunk

    async def invoke(
        self,
        input_text: str,
        *,
        intent_reason: str,
        session_id: str | None = None,
        session_state: dict | None = None,
        metadata: dict | None = None,
        text_only: bool = True,
    ) -> str | list[AgentChunk]:
        """
        Invoke the Bedrock Agent and return the full response.

        Args:
            input_text:    Prompt to send to the agent.
            intent_reason: Justification for the audit trail.
            session_id:    Bedrock session ID.
            session_state: Optional Bedrock sessionState dict.
            metadata:      Extra metadata for the Tollgate audit event.
            text_only:     If True (default), concatenate and return all text
                           chunks as a single string. If False, return all
                           AgentChunks including traces.

        Returns:
            Concatenated text string (text_only=True) or list of AgentChunks.
        """
        chunks: list[AgentChunk] = []
        async for chunk in self.stream_invoke(
            input_text,
            intent_reason=intent_reason,
            session_id=session_id,
            session_state=session_state,
            metadata=metadata,
        ):
            chunks.append(chunk)

        if text_only:
            return "".join(c.text for c in chunks if c.is_text)
        return chunks
