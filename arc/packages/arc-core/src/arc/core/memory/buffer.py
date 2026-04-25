"""
foundry.memory.buffer
──────────────────────
Short-term, in-context conversation memory for Foundry agents.

ConversationBuffer is a bounded ring buffer of Message objects. It holds the
last N turns of a conversation so agents can maintain context across multi-turn
interactions without a checkpointer or external store.

This is zero-dependency (no LangChain, no AWS required) and works with any
BaseAgent. For LangGraph agents that already use a checkpointer, FoundryState
already persists full graph state — use ConversationBuffer for non-graph agents
or for pre/post-processing outside the graph.

Usage in a BaseAgent:

    class MyAgent(BaseAgent):

        def __init__(self, manifest, tower, gateway, tracker=None):
            super().__init__(manifest, tower, gateway, tracker)
            self.memory = ConversationBuffer(max_turns=20)

        async def execute(self, user_input: str, session_id: str = "default", **kwargs):
            # Add the user turn
            self.memory.add_user(session_id, user_input)

            # Build context string for the LLM prompt
            context = self.memory.format_context(session_id)

            # ... run the agent ...
            response = await self._generate(context + "\\n" + user_input)

            # Add the agent turn
            self.memory.add_assistant(session_id, response)
            return {"response": response}

Usage with session isolation:

    # Each session_id has independent conversation history
    buffer.add_user("session-alice",  "What is my balance?")
    buffer.add_user("session-bob",    "Show me my risk score")
    alice_history = buffer.get_history("session-alice")   # only Alice's turns
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Literal


# ── Data model ─────────────────────────────────────────────────────────────────


@dataclass
class Message:
    """
    A single conversation turn.

    Attributes:
        role:       "user", "assistant", or "system"
        content:    The message text.
        timestamp:  Unix timestamp when the message was added.
        metadata:   Arbitrary key-value context (tool_name, effect, etc.)
    """
    role:      Literal["user", "assistant", "system"]
    content:   str
    timestamp: float = field(default_factory=time.time)
    metadata:  dict  = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "role":      self.role,
            "content":   self.content,
            "timestamp": self.timestamp,
            "metadata":  self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Message":
        return cls(
            role=d["role"],
            content=d["content"],
            timestamp=d.get("timestamp", time.time()),
            metadata=d.get("metadata", {}),
        )


# ── Buffer ─────────────────────────────────────────────────────────────────────


class ConversationBuffer:
    """
    Bounded in-memory conversation history, keyed by session_id.

    Stores the last `max_turns` messages per session. When the limit is
    reached, the oldest message is automatically dropped (FIFO).

    Thread-safety: Not thread-safe. For concurrent agents, use one buffer
    per coroutine or protect with asyncio.Lock.

    Args:
        max_turns:      Maximum messages to retain per session (default 50).
        system_prompt:  Optional system prompt prepended to every context window.
    """

    def __init__(
        self,
        max_turns: int = 50,
        system_prompt: str | None = None,
    ):
        self.max_turns     = max_turns
        self.system_prompt = system_prompt
        self._sessions: dict[str, deque[Message]] = {}

    # ── Write ──────────────────────────────────────────────────────────────

    def add_user(
        self,
        session_id: str,
        content: str,
        metadata: dict | None = None,
    ) -> None:
        """Add a user turn to the session history."""
        self._append(session_id, Message(role="user", content=content, metadata=metadata or {}))

    def add_assistant(
        self,
        session_id: str,
        content: str,
        metadata: dict | None = None,
    ) -> None:
        """Add an assistant (agent) response to the session history."""
        self._append(session_id, Message(role="assistant", content=content, metadata=metadata or {}))

    def add_system(
        self,
        session_id: str,
        content: str,
        metadata: dict | None = None,
    ) -> None:
        """Add a system message (tool results, injected context) to the session history."""
        self._append(session_id, Message(role="system", content=content, metadata=metadata or {}))

    def add_message(self, session_id: str, message: Message) -> None:
        """Add a pre-built Message to the session history."""
        self._append(session_id, message)

    def _append(self, session_id: str, message: Message) -> None:
        if session_id not in self._sessions:
            self._sessions[session_id] = deque(maxlen=self.max_turns)
        self._sessions[session_id].append(message)

    # ── Read ───────────────────────────────────────────────────────────────

    def get_history(
        self,
        session_id: str,
        last_n: int | None = None,
        role: str | None = None,
    ) -> list[Message]:
        """
        Return the conversation history for a session.

        Args:
            session_id: Session to retrieve.
            last_n:     If set, return only the last N messages.
            role:       If set, filter to only messages with this role.

        Returns:
            List of Message objects in chronological order.
        """
        msgs = list(self._sessions.get(session_id, []))
        if role:
            msgs = [m for m in msgs if m.role == role]
        if last_n is not None:
            msgs = msgs[-last_n:]
        return msgs

    def format_context(
        self,
        session_id: str,
        last_n: int | None = None,
        separator: str = "\n",
        include_roles: bool = True,
    ) -> str:
        """
        Format the conversation history as a plain-text context string.

        Suitable for injecting into an LLM prompt:

            context = buffer.format_context("session-42", last_n=10)
            prompt = f"{context}\\nUser: {new_message}"

        Args:
            session_id:    Session to format.
            last_n:        Only include the last N turns (default: all).
            separator:     String between turns (default: newline).
            include_roles: Prefix each message with "User:" / "Assistant:".

        Returns:
            A single formatted string of the conversation history.
        """
        parts: list[str] = []
        if self.system_prompt:
            parts.append(f"System: {self.system_prompt}" if include_roles else self.system_prompt)

        for msg in self.get_history(session_id, last_n=last_n):
            if include_roles:
                role_label = msg.role.capitalize()
                parts.append(f"{role_label}: {msg.content}")
            else:
                parts.append(msg.content)

        return separator.join(parts)

    def to_openai_messages(
        self,
        session_id: str,
        last_n: int | None = None,
    ) -> list[dict]:
        """
        Return conversation history as OpenAI-compatible message dicts.

        Compatible with Bedrock Converse API, OpenAI Chat Completions,
        LangChain HumanMessage/AIMessage, and Anthropic Messages API.

            messages = buffer.to_openai_messages("session-42")
            response = openai.chat.completions.create(
                model="gpt-4o",
                messages=messages,
            )

        Returns:
            List of {"role": "...", "content": "..."} dicts.
        """
        result: list[dict] = []
        if self.system_prompt:
            result.append({"role": "system", "content": self.system_prompt})
        for msg in self.get_history(session_id, last_n=last_n):
            result.append({"role": msg.role, "content": msg.content})
        return result

    # ── Management ─────────────────────────────────────────────────────────

    def clear(self, session_id: str) -> None:
        """Clear all history for a session."""
        self._sessions.pop(session_id, None)

    def clear_all(self) -> None:
        """Clear all sessions."""
        self._sessions.clear()

    def session_count(self) -> int:
        """Number of active sessions."""
        return len(self._sessions)

    def turn_count(self, session_id: str) -> int:
        """Number of messages in a session."""
        return len(self._sessions.get(session_id, []))

    def sessions(self) -> list[str]:
        """List of active session IDs."""
        return list(self._sessions.keys())

    def __repr__(self) -> str:
        return (
            f"ConversationBuffer(sessions={self.session_count()}, "
            f"max_turns={self.max_turns})"
        )
