"""Migrated to arc.core.memory.buffer. Thin re-export shim."""

from arc.core.memory.buffer import ConversationBuffer, Message

__all__ = ["ConversationBuffer", "Message"]
