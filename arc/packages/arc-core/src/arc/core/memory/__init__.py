"""
arc.core.memory — agent memory primitives.

Two layers:
  - ConversationBuffer: short-term, in-context message history (bounded ring buffer)
  - FoundryMemoryStore: long-term persisted key-value memory with optional TTL
"""

from .buffer import ConversationBuffer, Message
from .store import (
    DynamoDBMemoryBackend,
    FoundryMemoryStore,
    LocalJsonStore,
    MemoryBackend,
    MemoryEntry,
)

__all__ = [
    "ConversationBuffer",
    "Message",
    "FoundryMemoryStore",
    "MemoryEntry",
    "MemoryBackend",
    "LocalJsonStore",
    "DynamoDBMemoryBackend",
]
