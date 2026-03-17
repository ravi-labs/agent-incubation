"""
Tests for foundry.memory — ConversationBuffer and FoundryMemoryStore.

Covers:
  ConversationBuffer:
    - add_user / add_assistant / add_system / add_message
    - get_history (all, last_n, role filter)
    - format_context (with/without roles, separator, system_prompt)
    - to_openai_messages
    - session isolation
    - ring-buffer eviction at max_turns
    - clear / clear_all / session_count / turn_count / sessions()

  FoundryMemoryStore + LocalJsonStore:
    - get hit / miss
    - set and retrieve
    - TTL expiry
    - created_at preserved on update
    - delete removes entry
    - keys() lists all keys in namespace
    - all() lists non-expired entries
    - get_or_set caches value on miss, returns cached on hit
    - cross-namespace isolation
    - file persistence (written to disk and reloaded)
"""

import asyncio
import json
import time
import pytest

from foundry.memory.buffer import ConversationBuffer, Message
from foundry.memory.store import FoundryMemoryStore, LocalJsonStore, MemoryEntry


# ─── ConversationBuffer ────────────────────────────────────────────────────────

class TestConversationBuffer:

    def test_add_user_and_retrieve(self):
        buf = ConversationBuffer()
        buf.add_user("s1", "Hello")
        history = buf.get_history("s1")
        assert len(history) == 1
        assert history[0].role == "user"
        assert history[0].content == "Hello"

    def test_add_assistant_and_retrieve(self):
        buf = ConversationBuffer()
        buf.add_assistant("s1", "Hi there")
        history = buf.get_history("s1")
        assert history[0].role == "assistant"

    def test_add_system_and_retrieve(self):
        buf = ConversationBuffer()
        buf.add_system("s1", "Context injected")
        history = buf.get_history("s1")
        assert history[0].role == "system"

    def test_add_message_with_prebuilt_message(self):
        buf = ConversationBuffer()
        msg = Message(role="user", content="Custom message")
        buf.add_message("s1", msg)
        assert buf.get_history("s1")[0].content == "Custom message"

    def test_session_isolation(self):
        buf = ConversationBuffer()
        buf.add_user("alice", "Alice's message")
        buf.add_user("bob", "Bob's message")
        assert len(buf.get_history("alice")) == 1
        assert buf.get_history("alice")[0].content == "Alice's message"
        assert buf.get_history("bob")[0].content == "Bob's message"

    def test_empty_session_returns_empty_list(self):
        buf = ConversationBuffer()
        assert buf.get_history("nonexistent") == []

    def test_ring_buffer_evicts_oldest_at_max_turns(self):
        buf = ConversationBuffer(max_turns=3)
        for i in range(5):
            buf.add_user("s1", f"msg-{i}")
        history = buf.get_history("s1")
        assert len(history) == 3
        assert history[0].content == "msg-2"
        assert history[-1].content == "msg-4"

    def test_get_history_last_n(self):
        buf = ConversationBuffer()
        for i in range(10):
            buf.add_user("s1", f"msg-{i}")
        history = buf.get_history("s1", last_n=3)
        assert len(history) == 3
        assert history[-1].content == "msg-9"

    def test_get_history_role_filter(self):
        buf = ConversationBuffer()
        buf.add_user("s1", "user msg")
        buf.add_assistant("s1", "assistant msg")
        buf.add_user("s1", "user msg 2")
        user_only = buf.get_history("s1", role="user")
        assert all(m.role == "user" for m in user_only)
        assert len(user_only) == 2

    def test_format_context_includes_roles(self):
        buf = ConversationBuffer()
        buf.add_user("s1", "Hello")
        buf.add_assistant("s1", "Hi")
        ctx = buf.format_context("s1")
        assert "User: Hello" in ctx
        assert "Assistant: Hi" in ctx

    def test_format_context_without_roles(self):
        buf = ConversationBuffer()
        buf.add_user("s1", "Hello")
        ctx = buf.format_context("s1", include_roles=False)
        assert "User:" not in ctx
        assert "Hello" in ctx

    def test_format_context_with_system_prompt(self):
        buf = ConversationBuffer(system_prompt="You are a helpful assistant.")
        buf.add_user("s1", "Hello")
        ctx = buf.format_context("s1")
        assert "You are a helpful assistant" in ctx

    def test_format_context_custom_separator(self):
        buf = ConversationBuffer()
        buf.add_user("s1", "A")
        buf.add_user("s1", "B")
        ctx = buf.format_context("s1", separator=" | ")
        assert " | " in ctx

    def test_to_openai_messages_format(self):
        buf = ConversationBuffer()
        buf.add_user("s1", "Hello")
        buf.add_assistant("s1", "Hi")
        msgs = buf.to_openai_messages("s1")
        assert msgs == [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"},
        ]

    def test_to_openai_messages_with_system_prompt(self):
        buf = ConversationBuffer(system_prompt="Be concise.")
        buf.add_user("s1", "Hello")
        msgs = buf.to_openai_messages("s1")
        assert msgs[0] == {"role": "system", "content": "Be concise."}
        assert msgs[1] == {"role": "user", "content": "Hello"}

    def test_clear_removes_session(self):
        buf = ConversationBuffer()
        buf.add_user("s1", "Hello")
        buf.clear("s1")
        assert buf.get_history("s1") == []
        assert "s1" not in buf.sessions()

    def test_clear_all_removes_all_sessions(self):
        buf = ConversationBuffer()
        buf.add_user("s1", "A")
        buf.add_user("s2", "B")
        buf.clear_all()
        assert buf.session_count() == 0

    def test_session_count(self):
        buf = ConversationBuffer()
        buf.add_user("s1", "A")
        buf.add_user("s2", "B")
        assert buf.session_count() == 2

    def test_turn_count(self):
        buf = ConversationBuffer()
        buf.add_user("s1", "A")
        buf.add_user("s1", "B")
        assert buf.turn_count("s1") == 2

    def test_sessions_lists_active_sessions(self):
        buf = ConversationBuffer()
        buf.add_user("alice", "hi")
        buf.add_user("bob", "hey")
        sessions = buf.sessions()
        assert set(sessions) == {"alice", "bob"}

    def test_metadata_stored_on_message(self):
        buf = ConversationBuffer()
        buf.add_user("s1", "Hello", metadata={"intent": "greeting"})
        msg = buf.get_history("s1")[0]
        assert msg.metadata == {"intent": "greeting"}

    def test_repr_shows_session_count(self):
        buf = ConversationBuffer()
        buf.add_user("s1", "hi")
        assert "1" in repr(buf)


# ─── LocalJsonStore + FoundryMemoryStore ──────────────────────────────────────

class TestFoundryMemoryStore:

    def make_store(self, tmp_path) -> FoundryMemoryStore:
        backend = LocalJsonStore(tmp_path / "memory.json")
        return FoundryMemoryStore(agent_id="test-agent", backend=backend)

    @pytest.mark.asyncio
    async def test_get_returns_none_on_miss(self, tmp_path):
        store = self.make_store(tmp_path)
        result = await store.get("findings", "fund-001")
        assert result is None

    @pytest.mark.asyncio
    async def test_set_and_get_returns_value(self, tmp_path):
        store = self.make_store(tmp_path)
        await store.set("findings", "fund-001", {"severity": "low"})
        result = await store.get("findings", "fund-001")
        assert result == {"severity": "low"}

    @pytest.mark.asyncio
    async def test_set_overwrites_existing_value(self, tmp_path):
        store = self.make_store(tmp_path)
        await store.set("findings", "fund-001", {"severity": "low"})
        await store.set("findings", "fund-001", {"severity": "high"})
        result = await store.get("findings", "fund-001")
        assert result == {"severity": "high"}

    @pytest.mark.asyncio
    async def test_created_at_preserved_on_update(self, tmp_path):
        store = self.make_store(tmp_path)
        await store.set("ns", "key", "v1")
        backend = store._backend
        entry1 = await backend.get("test-agent", "ns", "key")
        created_at_original = entry1.created_at

        await asyncio.sleep(0.01)
        await store.set("ns", "key", "v2")
        entry2 = await backend.get("test-agent", "ns", "key")
        assert entry2.created_at == pytest.approx(created_at_original, abs=0.001)
        assert entry2.updated_at > entry1.updated_at

    @pytest.mark.asyncio
    async def test_ttl_expiry_returns_none(self, tmp_path):
        store = self.make_store(tmp_path)
        # Write an entry that expires in the past
        backend = store._backend
        entry = MemoryEntry(
            namespace="ns", key="expired-key", value="gone",
            agent_id="test-agent",
            created_at=time.time() - 10,
            updated_at=time.time() - 10,
            expires_at=time.time() - 1,   # already expired
        )
        await backend.set(entry)
        result = await store.get("ns", "expired-key")
        assert result is None

    @pytest.mark.asyncio
    async def test_no_ttl_entry_does_not_expire(self, tmp_path):
        store = self.make_store(tmp_path)
        await store.set("ns", "permanent", "stays", ttl_days=None)
        result = await store.get("ns", "permanent")
        assert result == "stays"

    @pytest.mark.asyncio
    async def test_delete_removes_entry(self, tmp_path):
        store = self.make_store(tmp_path)
        await store.set("ns", "key", "value")
        await store.delete("ns", "key")
        result = await store.get("ns", "key")
        assert result is None

    @pytest.mark.asyncio
    async def test_keys_lists_namespace_keys(self, tmp_path):
        store = self.make_store(tmp_path)
        await store.set("findings", "fund-001", "a")
        await store.set("findings", "fund-002", "b")
        await store.set("other_ns", "x", "c")
        keys = await store.keys("findings")
        assert set(keys) == {"fund-001", "fund-002"}

    @pytest.mark.asyncio
    async def test_all_returns_non_expired_entries(self, tmp_path):
        store = self.make_store(tmp_path)
        await store.set("ns", "k1", "v1")
        await store.set("ns", "k2", "v2")
        # Insert an expired one directly
        backend = store._backend
        expired = MemoryEntry(
            namespace="ns", key="k3", value="v3",
            agent_id="test-agent",
            expires_at=time.time() - 1,
        )
        await backend.set(expired)
        entries = await store.all("ns")
        keys = {e.key for e in entries}
        assert "k1" in keys
        assert "k2" in keys
        assert "k3" not in keys

    @pytest.mark.asyncio
    async def test_get_or_set_computes_on_miss(self, tmp_path):
        store = self.make_store(tmp_path)
        called = []

        async def compute():
            called.append(1)
            return {"computed": True}

        result = await store.get_or_set("ns", "key", compute)
        assert result == {"computed": True}
        assert len(called) == 1

    @pytest.mark.asyncio
    async def test_get_or_set_returns_cached_on_hit(self, tmp_path):
        store = self.make_store(tmp_path)
        await store.set("ns", "key", {"cached": True})
        called = []

        async def compute():
            called.append(1)
            return {"computed": True}

        result = await store.get_or_set("ns", "key", compute)
        assert result == {"cached": True}
        assert len(called) == 0   # fn not called

    @pytest.mark.asyncio
    async def test_cross_namespace_isolation(self, tmp_path):
        store = self.make_store(tmp_path)
        await store.set("ns_a", "key", "value_a")
        await store.set("ns_b", "key", "value_b")
        assert await store.get("ns_a", "key") == "value_a"
        assert await store.get("ns_b", "key") == "value_b"

    @pytest.mark.asyncio
    async def test_persists_to_disk_and_reloads(self, tmp_path):
        path = tmp_path / "memory.json"
        backend1 = LocalJsonStore(path)
        store1 = FoundryMemoryStore(agent_id="test-agent", backend=backend1)
        await store1.set("ns", "key", {"persisted": True})

        # Create a fresh backend pointing to the same file
        backend2 = LocalJsonStore(path)
        store2 = FoundryMemoryStore(agent_id="test-agent", backend=backend2)
        result = await store2.get("ns", "key")
        assert result == {"persisted": True}

    @pytest.mark.asyncio
    async def test_file_is_valid_json(self, tmp_path):
        path = tmp_path / "memory.json"
        backend = LocalJsonStore(path)
        store = FoundryMemoryStore(agent_id="test-agent", backend=backend)
        await store.set("ns", "key", {"hello": "world"})

        with open(path) as f:
            data = json.load(f)
        assert isinstance(data, dict)
        assert len(data) == 1
