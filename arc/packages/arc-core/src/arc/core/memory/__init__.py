"""
arc.core.memory — agent memory primitives.

Two layers:
  - ConversationBuffer: short-term, in-context message history (bounded ring buffer)
  - AgentMemoryStore: long-term persisted key-value memory with optional TTL
"""

from .buffer import ConversationBuffer, Message
from .store import (
    DynamoDBMemoryBackend,
    AgentMemoryStore,
    LocalJsonStore,
    MemoryBackend,
    MemoryEntry,
)

__all__ = [
    "ConversationBuffer",
    "Message",
    "AgentMemoryStore",
    "MemoryEntry",
    "MemoryBackend",
    "LocalJsonStore",
    "DynamoDBMemoryBackend",
]
