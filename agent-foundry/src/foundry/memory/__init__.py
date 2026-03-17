"""
foundry.memory
──────────────
Agent memory primitives for agent-foundry.

Two layers:
  - ConversationBuffer: short-term, in-context message history (bounded ring buffer)
  - FoundryMemoryStore: long-term persisted key-value memory with optional TTL

Install:
    pip install "agent-foundry"          # ConversationBuffer (no extra deps)
    pip install "agent-foundry[aws]"     # FoundryMemoryStore with DynamoDB backend
"""
from foundry.memory.buffer import ConversationBuffer, Message
from foundry.memory.store import FoundryMemoryStore, MemoryEntry

__all__ = [
    "ConversationBuffer",
    "Message",
    "FoundryMemoryStore",
    "MemoryEntry",
]
