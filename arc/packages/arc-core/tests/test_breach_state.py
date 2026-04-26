"""Tests for the BreachStateStore implementations (hysteresis counter)."""

from __future__ import annotations

from pathlib import Path

from arc.core import InMemoryBreachStateStore, JsonlBreachStateStore


class TestInMemoryBreachStateStore:
    def test_first_breach_starts_counter(self):
        store = InMemoryBreachStateStore()
        s = store.record("a", breached=True)
        assert s.consecutive_breaches == 1
        assert s.last_breached_at != ""

    def test_consecutive_breaches_accumulate(self):
        store = InMemoryBreachStateStore()
        for _ in range(3):
            store.record("a", breached=True)
        assert store.get("a").consecutive_breaches == 3

    def test_pass_resets_counter(self):
        store = InMemoryBreachStateStore()
        store.record("a", breached=True)
        store.record("a", breached=True)
        store.record("a", breached=False)
        assert store.get("a").consecutive_breaches == 0

    def test_unknown_agent_returns_zero(self):
        store = InMemoryBreachStateStore()
        s = store.get("never-seen")
        assert s.agent_id == "never-seen"
        assert s.consecutive_breaches == 0

    def test_isolated_per_agent(self):
        store = InMemoryBreachStateStore()
        store.record("a", breached=True)
        store.record("a", breached=True)
        store.record("b", breached=True)
        assert store.get("a").consecutive_breaches == 2
        assert store.get("b").consecutive_breaches == 1

    def test_reset(self):
        store = InMemoryBreachStateStore()
        store.record("a", breached=True)
        store.record("a", breached=True)
        store.reset("a")
        assert store.get("a").consecutive_breaches == 0


class TestJsonlBreachStateStore:
    def test_persistence_across_instances(self, tmp_path: Path):
        path = tmp_path / "breach_state.jsonl"

        s1 = JsonlBreachStateStore(path)
        s1.record("a", breached=True)
        s1.record("a", breached=True)

        # New instance, same file — must reconstruct counter.
        s2 = JsonlBreachStateStore(path)
        assert s2.get("a").consecutive_breaches == 2

    def test_latest_line_wins(self, tmp_path: Path):
        path = tmp_path / "breach_state.jsonl"
        store = JsonlBreachStateStore(path)
        store.record("a", breached=True)
        store.record("a", breached=True)
        store.record("a", breached=False)   # reset
        # Re-read from disk in a new instance.
        fresh = JsonlBreachStateStore(path)
        assert fresh.get("a").consecutive_breaches == 0

    def test_corrupt_lines_skipped(self, tmp_path: Path):
        path = tmp_path / "breach_state.jsonl"
        path.write_text("not json\n")  # one bad line first
        store = JsonlBreachStateStore(path)
        store.record("a", breached=True)
        # Re-read; should still produce a valid counter for 'a'.
        assert JsonlBreachStateStore(path).get("a").consecutive_breaches == 1
