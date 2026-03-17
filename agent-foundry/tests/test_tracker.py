"""
Tests for OutcomeTracker.

Validates that:
  - Events are recorded in-memory
  - Events are persisted to JSONL
  - Filtering by agent_id and event_type works
  - Summary counts are correct
  - No-path mode works (in-memory only)
"""

import json
import pytest
from pathlib import Path

from foundry.observability.tracker import OutcomeEvent, OutcomeTracker


# ─── In-Memory Recording ─────────────────────────────────────────────────────

class TestInMemoryRecording:
    @pytest.mark.asyncio
    async def test_record_single_event(self):
        tracker = OutcomeTracker()
        event = await tracker.record(
            agent_id="test-agent",
            event_type="intervention_sent",
            data={"participant_id": "p-001"},
        )
        assert event.agent_id == "test-agent"
        assert event.event_type == "intervention_sent"
        assert event.data == {"participant_id": "p-001"}
        assert event.timestamp  # Should be set automatically

    @pytest.mark.asyncio
    async def test_record_multiple_events(self):
        tracker = OutcomeTracker()
        await tracker.record("a1", "event_a", {})
        await tracker.record("a1", "event_b", {})
        await tracker.record("a2", "event_a", {})
        assert len(tracker.events()) == 3

    @pytest.mark.asyncio
    async def test_events_returns_all_by_default(self):
        tracker = OutcomeTracker()
        await tracker.record("agent-1", "type_x", {"k": "v"})
        await tracker.record("agent-2", "type_y", {"k": "v"})
        assert len(tracker.events()) == 2

    @pytest.mark.asyncio
    async def test_filter_by_agent_id(self):
        tracker = OutcomeTracker()
        await tracker.record("agent-1", "event_a", {})
        await tracker.record("agent-1", "event_b", {})
        await tracker.record("agent-2", "event_a", {})
        filtered = tracker.events(agent_id="agent-1")
        assert len(filtered) == 2
        assert all(e.agent_id == "agent-1" for e in filtered)

    @pytest.mark.asyncio
    async def test_filter_by_event_type(self):
        tracker = OutcomeTracker()
        await tracker.record("agent-1", "intervention_sent", {})
        await tracker.record("agent-1", "intervention_acted_on", {})
        await tracker.record("agent-2", "intervention_sent", {})
        filtered = tracker.events(event_type="intervention_sent")
        assert len(filtered) == 2
        assert all(e.event_type == "intervention_sent" for e in filtered)

    @pytest.mark.asyncio
    async def test_filter_combined(self):
        tracker = OutcomeTracker()
        await tracker.record("agent-1", "event_a", {})
        await tracker.record("agent-1", "event_b", {})
        await tracker.record("agent-2", "event_a", {})
        filtered = tracker.events(agent_id="agent-1", event_type="event_a")
        assert len(filtered) == 1

    @pytest.mark.asyncio
    async def test_filter_returns_empty_list_if_no_match(self):
        tracker = OutcomeTracker()
        await tracker.record("agent-1", "event_a", {})
        assert tracker.events(agent_id="nonexistent") == []


# ─── Summary ─────────────────────────────────────────────────────────────────

class TestSummary:
    @pytest.mark.asyncio
    async def test_summary_counts_by_type(self):
        tracker = OutcomeTracker()
        await tracker.record("a", "intervention_sent", {})
        await tracker.record("a", "intervention_sent", {})
        await tracker.record("a", "intervention_acted_on", {})
        summary = tracker.summary()
        assert summary["intervention_sent"] == 2
        assert summary["intervention_acted_on"] == 1

    @pytest.mark.asyncio
    async def test_summary_empty_tracker(self):
        tracker = OutcomeTracker()
        assert tracker.summary() == {}

    @pytest.mark.asyncio
    async def test_summary_updates_with_new_events(self):
        tracker = OutcomeTracker()
        assert tracker.summary() == {}
        await tracker.record("a", "event_x", {})
        assert tracker.summary()["event_x"] == 1
        await tracker.record("a", "event_x", {})
        assert tracker.summary()["event_x"] == 2


# ─── JSONL Persistence ────────────────────────────────────────────────────────

class TestJsonlPersistence:
    @pytest.mark.asyncio
    async def test_events_written_to_file(self, tmp_path):
        path = tmp_path / "outcomes.jsonl"
        tracker = OutcomeTracker(path=path)
        await tracker.record("test-agent", "intervention_sent", {"participant_id": "p-001"})
        await tracker.record("test-agent", "intervention_acted_on", {"participant_id": "p-001"})

        assert path.exists()
        lines = path.read_text().strip().split("\n")
        assert len(lines) == 2

    @pytest.mark.asyncio
    async def test_written_events_are_valid_json(self, tmp_path):
        path = tmp_path / "outcomes.jsonl"
        tracker = OutcomeTracker(path=path)
        await tracker.record("agent", "test_event", {"key": "value", "count": 42})

        with open(path) as f:
            event_dict = json.loads(f.readline())

        assert event_dict["agent_id"] == "agent"
        assert event_dict["event_type"] == "test_event"
        assert event_dict["data"]["key"] == "value"
        assert event_dict["timestamp"]  # Should be set

    @pytest.mark.asyncio
    async def test_events_appended_not_overwritten(self, tmp_path):
        path = tmp_path / "outcomes.jsonl"
        tracker = OutcomeTracker(path=path)

        await tracker.record("agent", "event_1", {})
        await tracker.record("agent", "event_2", {})
        await tracker.record("agent", "event_3", {})

        lines = path.read_text().strip().split("\n")
        assert len(lines) == 3

    @pytest.mark.asyncio
    async def test_no_path_no_file_created(self, tmp_path):
        tracker = OutcomeTracker(path=None)
        await tracker.record("agent", "event", {})
        # No file should be created anywhere
        assert len(list(tmp_path.iterdir())) == 0

    @pytest.mark.asyncio
    async def test_parent_directory_created_if_missing(self, tmp_path):
        path = tmp_path / "nested" / "deep" / "outcomes.jsonl"
        tracker = OutcomeTracker(path=path)
        await tracker.record("agent", "event", {})
        assert path.exists()


# ─── Session ID ───────────────────────────────────────────────────────────────

class TestSessionId:
    @pytest.mark.asyncio
    async def test_session_id_attached_to_events(self):
        tracker = OutcomeTracker(session_id="run-2025-01-01")
        event = await tracker.record("agent", "event", {})
        assert event.session_id == "run-2025-01-01"

    @pytest.mark.asyncio
    async def test_no_session_id_is_none(self):
        tracker = OutcomeTracker()
        event = await tracker.record("agent", "event", {})
        assert event.session_id is None


# ─── OutcomeEvent ─────────────────────────────────────────────────────────────

class TestOutcomeEvent:
    def test_to_dict_contains_all_fields(self):
        event = OutcomeEvent(
            agent_id="agent-1",
            event_type="test_event",
            data={"key": "value"},
            timestamp="2025-01-01T00:00:00+00:00",
        )
        d = event.to_dict()
        assert d["agent_id"] == "agent-1"
        assert d["event_type"] == "test_event"
        assert d["data"] == {"key": "value"}
        assert d["timestamp"] == "2025-01-01T00:00:00+00:00"

    def test_to_json_is_valid_json(self):
        event = OutcomeEvent(
            agent_id="a", event_type="e", data={"x": 1},
            timestamp="2025-01-01T00:00:00+00:00",
        )
        parsed = json.loads(event.to_json())
        assert parsed["agent_id"] == "a"
        assert parsed["data"]["x"] == 1
