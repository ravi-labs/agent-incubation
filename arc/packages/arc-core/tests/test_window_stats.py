"""Tests for OutcomeTracker.window_stats — feeds the SLO evaluator.

Covers built-in metrics (event_count, error_rate, latency percentiles),
custom dotted-key metrics (numeric mean, boolean rate), the time-window
boundary, and JSONL on-disk reads (which is how the watcher CLI uses it).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from arc.core.observability import OutcomeTracker


# ── Helpers ─────────────────────────────────────────────────────────────────


def _write_events(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


def _ts(now: datetime, **delta) -> str:
    return (now - timedelta(**delta)).isoformat()


# ── In-memory fast paths ────────────────────────────────────────────────────


class TestWindowStatsInMemory:
    @pytest.mark.asyncio
    async def test_empty_tracker_returns_zero_count(self):
        tracker = OutcomeTracker()  # no path → memory only
        stats = tracker.window_stats(agent_id="x", window_seconds=3600)
        assert stats == {"event_count": 0}

    @pytest.mark.asyncio
    async def test_counts_only_window_and_only_agent(self):
        tracker = OutcomeTracker()
        now = datetime.now(timezone.utc)

        # Inject events with manually-set timestamps via record() then
        # mutate _events directly — record() always uses 'now'.
        await tracker.record(agent_id="a", event_type="x", data={})
        await tracker.record(agent_id="a", event_type="x", data={})
        await tracker.record(agent_id="b", event_type="x", data={})
        # One event well outside the window
        tracker._events[0].timestamp = (now - timedelta(hours=10)).isoformat()

        stats = tracker.window_stats(agent_id="a", window_seconds=3600, now=now)
        assert stats["event_count"] == 1   # the other two are out-of-window or other agent

    @pytest.mark.asyncio
    async def test_error_rate_via_status(self):
        tracker = OutcomeTracker()
        await tracker.record(agent_id="a", event_type="run", data={"status": "ok"})
        await tracker.record(agent_id="a", event_type="run", data={"status": "error"})
        await tracker.record(agent_id="a", event_type="run", data={"status": "error"})
        stats = tracker.window_stats(agent_id="a", window_seconds=3600)
        assert stats["event_count"] == 3
        assert stats["error_rate"] == pytest.approx(2 / 3)

    @pytest.mark.asyncio
    async def test_error_rate_via_error_field(self):
        tracker = OutcomeTracker()
        await tracker.record(agent_id="a", event_type="r", data={"error": "boom"})
        await tracker.record(agent_id="a", event_type="r", data={})
        stats = tracker.window_stats(agent_id="a", window_seconds=3600)
        assert stats["error_rate"] == 0.5

    @pytest.mark.asyncio
    async def test_latency_percentiles(self):
        tracker = OutcomeTracker()
        for ms in [100, 200, 300, 400, 500, 600, 700, 800, 900, 1000]:
            await tracker.record(agent_id="a", event_type="r",
                                 data={"latency_ms": ms})
        stats = tracker.window_stats(agent_id="a", window_seconds=3600)
        assert stats["p50_latency_ms"] == pytest.approx(550.0)
        # p95 over 10 sorted values: pos = 0.95 * 9 = 8.55 → between 900 and 1000
        assert stats["p95_latency_ms"] == pytest.approx(955.0)

    @pytest.mark.asyncio
    async def test_latency_omitted_when_no_events_carry_it(self):
        tracker = OutcomeTracker()
        await tracker.record(agent_id="a", event_type="r", data={"status": "ok"})
        stats = tracker.window_stats(agent_id="a", window_seconds=3600)
        assert "p50_latency_ms" not in stats
        assert "p95_latency_ms" not in stats

    @pytest.mark.asyncio
    async def test_custom_metric_numeric_mean(self):
        tracker = OutcomeTracker()
        await tracker.record(agent_id="a", event_type="r",
                             data={"score": 0.7})
        await tracker.record(agent_id="a", event_type="r",
                             data={"score": 0.9})
        stats = tracker.window_stats(
            agent_id="a", window_seconds=3600,
            custom_metrics=["score"],
        )
        assert stats["score"] == pytest.approx(0.8)

    @pytest.mark.asyncio
    async def test_custom_metric_boolean_success_rate(self):
        tracker = OutcomeTracker()
        for v in [True, True, True, False]:
            await tracker.record(agent_id="a", event_type="r",
                                 data={"sent": v})
        stats = tracker.window_stats(
            agent_id="a", window_seconds=3600,
            custom_metrics=["sent"],
        )
        assert stats["sent"] == pytest.approx(0.75)

    @pytest.mark.asyncio
    async def test_custom_metric_dotted_key(self):
        tracker = OutcomeTracker()
        await tracker.record(agent_id="a", event_type="r",
                             data={"plan": {"approval": {"rate": 0.4}}})
        await tracker.record(agent_id="a", event_type="r",
                             data={"plan": {"approval": {"rate": 0.6}}})
        stats = tracker.window_stats(
            agent_id="a", window_seconds=3600,
            custom_metrics=["plan.approval.rate"],
        )
        assert stats["plan.approval.rate"] == pytest.approx(0.5)

    @pytest.mark.asyncio
    async def test_custom_metric_missing_omitted_not_zero(self):
        # No event carries the field at all → key omitted from stats.
        # The SLO evaluator treats a missing metric as a breach, so
        # silently returning 0 would mask the problem.
        tracker = OutcomeTracker()
        await tracker.record(agent_id="a", event_type="r", data={})
        stats = tracker.window_stats(
            agent_id="a", window_seconds=3600,
            custom_metrics=["nonexistent_metric"],
        )
        assert "nonexistent_metric" not in stats


# ── JSONL-backed (cross-process) paths ──────────────────────────────────────


class TestWindowStatsJsonl:
    def test_reads_persisted_events(self, tmp_path: Path):
        """The CLI watcher runs in a fresh process; window_stats must read
        from disk, not just in-memory state."""
        path = tmp_path / "outcomes.jsonl"
        now = datetime.now(timezone.utc)
        _write_events(path, [
            {"agent_id": "a", "event_type": "r",
             "data": {"status": "ok"},
             "timestamp": _ts(now, minutes=5)},
            {"agent_id": "a", "event_type": "r",
             "data": {"status": "error"},
             "timestamp": _ts(now, minutes=10)},
            {"agent_id": "b", "event_type": "r",
             "data": {"status": "error"},
             "timestamp": _ts(now, minutes=10)},
        ])

        tracker = OutcomeTracker(path=path)
        stats = tracker.window_stats(agent_id="a", window_seconds=3600, now=now)
        assert stats["event_count"] == 2
        assert stats["error_rate"] == pytest.approx(0.5)

    def test_excludes_events_outside_window(self, tmp_path: Path):
        path = tmp_path / "outcomes.jsonl"
        now = datetime.now(timezone.utc)
        _write_events(path, [
            {"agent_id": "a", "event_type": "r", "data": {"status": "ok"},
             "timestamp": _ts(now, minutes=5)},
            {"agent_id": "a", "event_type": "r", "data": {"status": "error"},
             "timestamp": _ts(now, hours=10)},   # outside 1h window
        ])

        tracker = OutcomeTracker(path=path)
        stats = tracker.window_stats(agent_id="a", window_seconds=3600, now=now)
        assert stats["event_count"] == 1
        assert stats["error_rate"] == 0.0

    def test_skips_corrupt_lines(self, tmp_path: Path):
        path = tmp_path / "outcomes.jsonl"
        now = datetime.now(timezone.utc)
        path.write_text(
            "{not valid json}\n"
            + json.dumps({"agent_id": "a", "event_type": "r",
                          "data": {}, "timestamp": _ts(now, minutes=1)})
            + "\n"
        )
        tracker = OutcomeTracker(path=path)
        stats = tracker.window_stats(agent_id="a", window_seconds=3600, now=now)
        assert stats["event_count"] == 1

    def test_invalid_window_rejected(self, tmp_path: Path):
        tracker = OutcomeTracker(path=tmp_path / "outcomes.jsonl")
        with pytest.raises(ValueError):
            tracker.window_stats(agent_id="a", window_seconds=0)
