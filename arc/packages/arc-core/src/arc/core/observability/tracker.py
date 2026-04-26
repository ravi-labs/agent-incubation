"""
OutcomeTracker — records outcome events for ROI measurement and drift detection.

Every agent that produces an output should record whether that output
led to a real-world result. The tracker writes JSONL audit events that
can be queried to answer the proof-point questions defined in each
agent's success metrics.

Example outcome events:
  - intervention_sent        (Retirement Trajectory agent sent a message)
  - intervention_acted_on    (Participant changed their contribution rate)
  - finding_emitted          (Fiduciary Watchdog emitted a compliance finding)
  - finding_resolved         (Plan sponsor acted on the finding)
  - outreach_delivered       (Life Event agent sent outreach)
  - outreach_acted_on        (Participant completed a rollover)
  - recommendation_delivered (Plan Design Optimizer delivered to RM)
  - recommendation_adopted   (Sponsor implemented the recommendation)
"""

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)


# ── Built-in metric keys ────────────────────────────────────────────────────
# These are computed by ``window_stats`` for any agent without the agent
# having to emit them explicitly. SLO rules in a manifest can reference
# them directly. Custom metrics (dotted keys into ``data``) live alongside
# these in the same returned dict.

_BUILTIN_METRICS = ("event_count", "error_rate", "p95_latency_ms", "p50_latency_ms")


@dataclass
class OutcomeEvent:
    """
    A single outcome event recorded by an agent.

    Attributes:
        agent_id:    The agent that produced the outcome.
        event_type:  What happened (e.g., "intervention_sent").
        data:        Structured payload (participant_id, metric values, etc.).
        timestamp:   ISO 8601 UTC timestamp.
        session_id:  Optional run/session identifier for correlation.
    """
    agent_id: str
    event_type: str
    data: dict[str, Any]
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    session_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict())


class OutcomeTracker:
    """
    Records and persists outcome events for ROI tracking.

    Writes to a JSONL file (one event per line) compatible with
    Tollgate's audit log format and any downstream analytics pipeline.

    Usage:
        tracker = OutcomeTracker(path="outcomes.jsonl")
        await tracker.record(
            agent_id="retirement-trajectory",
            event_type="intervention_sent",
            data={"participant_id": "p-001", "channel": "email"},
        )
    """

    def __init__(
        self,
        path: str | Path | None = None,
        session_id: str | None = None,
    ):
        """
        Args:
            path:       Path to the JSONL output file.
                        If None, events are only logged (not persisted).
            session_id: Optional session/run identifier for correlation.
        """
        self.path = Path(path) if path else None
        self.session_id = session_id
        self._events: list[OutcomeEvent] = []

    async def record(
        self,
        agent_id: str,
        event_type: str,
        data: dict[str, Any],
    ) -> OutcomeEvent:
        """
        Record an outcome event.

        Args:
            agent_id:   Agent producing the outcome.
            event_type: Descriptor (e.g., "intervention_sent").
            data:       Structured event data.

        Returns:
            The recorded OutcomeEvent.
        """
        event = OutcomeEvent(
            agent_id=agent_id,
            event_type=event_type,
            data=data,
            session_id=self.session_id,
        )
        self._events.append(event)
        logger.info("outcome agent=%s event=%s", agent_id, event_type)

        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as f:
                f.write(event.to_json() + "\n")

        return event

    def events(
        self,
        agent_id: str | None = None,
        event_type: str | None = None,
    ) -> list[OutcomeEvent]:
        """
        Query recorded events, optionally filtered.

        Args:
            agent_id:   Filter by agent ID.
            event_type: Filter by event type.
        """
        results = self._events
        if agent_id:
            results = [e for e in results if e.agent_id == agent_id]
        if event_type:
            results = [e for e in results if e.event_type == event_type]
        return results

    def summary(self) -> dict[str, int]:
        """Return a count of events by type."""
        counts: dict[str, int] = {}
        for event in self._events:
            counts[event.event_type] = counts.get(event.event_type, 0) + 1
        return counts

    # ── Window stats — feeds the SLO evaluator ──────────────────────────

    def window_stats(
        self,
        *,
        agent_id: str,
        window_seconds: int,
        custom_metrics: Iterable[str] = (),
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Compute aggregate stats for one agent over a recent time window.

        Reads from the persisted JSONL file when ``self.path`` is set —
        works across processes, which is what the standalone ``arc agent
        watch`` CLI needs. Falls back to in-memory events when there is no
        path (harness / unit-test mode).

        Built-in metrics (always present):
          - ``event_count``      number of events in the window
          - ``error_rate``       share of events with ``data["status"] == "error"``
                                 (falls back to events with ``data["error"]``
                                 truthy). Range 0.0–1.0.
          - ``p50_latency_ms``,
            ``p95_latency_ms``   percentiles of ``data["latency_ms"]``,
                                 considering only events that carry that
                                 field. Missing → key absent.

        Custom metrics: pass dotted keys (``"intervention_send_success_rate"``,
        ``"plan.approval.rate"``) and the tracker computes them from
        ``data``. The convention:

          - If every matching event has a numeric value at that path,
            return the **mean**.
          - If values are booleans (``True`` / ``False``) it computes the
            **success rate** (count(true) / count_with_field).

        Custom metric values that don't fit either shape are skipped; the
        key is omitted from the result. The SLO evaluator treats a missing
        key as a breach, which is the safe default.

        Args:
            agent_id:        agent to compute over.
            window_seconds:  how far back to look from ``now``.
            custom_metrics:  dotted keys to project from ``OutcomeEvent.data``.
            now:             evaluation reference time (default: utcnow).

        Returns:
            dict mapping metric name → value (or omitted when not computable).
        """
        if window_seconds <= 0:
            raise ValueError(f"window_seconds must be > 0; got {window_seconds}")
        ref = now or datetime.now(timezone.utc)
        cutoff = ref - timedelta(seconds=window_seconds)

        events = self._load_events_for_window(agent_id, cutoff)

        out: dict[str, Any] = {"event_count": len(events)}
        if not events:
            return out

        # error_rate
        errors = sum(1 for e in events if _looks_like_error(e.data))
        out["error_rate"] = errors / len(events)

        # latency percentiles — only over events that carry latency_ms
        latencies = sorted(
            float(e.data["latency_ms"])
            for e in events
            if isinstance(e.data.get("latency_ms"), (int, float))
        )
        if latencies:
            out["p50_latency_ms"] = _percentile(latencies, 0.50)
            out["p95_latency_ms"] = _percentile(latencies, 0.95)

        # custom metrics
        for key in custom_metrics:
            value = _aggregate_custom_metric(events, key)
            if value is not None:
                out[key] = value
        return out

    # ── Internal helpers ────────────────────────────────────────────────

    def _load_events_for_window(
        self,
        agent_id: str,
        cutoff: datetime,
    ) -> list[OutcomeEvent]:
        """Return events for ``agent_id`` with timestamp ≥ ``cutoff``.

        Reads from the JSONL file when one is configured; otherwise from
        the in-memory buffer. The file path needs to be readable across
        processes for the watcher CLI to work.
        """
        if self.path is None or not self.path.exists():
            return [
                e for e in self._events
                if e.agent_id == agent_id and _parse_ts(e.timestamp) >= cutoff
            ]

        out: list[OutcomeEvent] = []
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if raw.get("agent_id") != agent_id:
                    continue
                ts = _parse_ts(raw.get("timestamp", ""))
                if ts < cutoff:
                    continue
                out.append(OutcomeEvent(
                    agent_id   = raw.get("agent_id", ""),
                    event_type = raw.get("event_type", ""),
                    data       = raw.get("data", {}) or {},
                    timestamp  = raw.get("timestamp", ""),
                    session_id = raw.get("session_id"),
                ))
        return out


def _parse_ts(ts: str) -> datetime:
    """Parse the ISO timestamp written by ``OutcomeEvent``.

    Returns ``datetime.min`` (naive UTC-replaced) for unparseable strings —
    those events get filtered out by the cutoff comparison.
    """
    if not ts:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def _looks_like_error(data: dict[str, Any]) -> bool:
    """Heuristic: does this event represent an error?

    Two shapes recognised, in order:
      1. Explicit ``data["status"] == "error"`` (or "failure", "failed").
      2. Truthy ``data["error"]``.
    """
    status = data.get("status")
    if isinstance(status, str) and status.lower() in ("error", "failure", "failed"):
        return True
    return bool(data.get("error"))


def _percentile(sorted_values: list[float], q: float) -> float:
    """Linear-interpolation percentile over a pre-sorted list.

    Matches NumPy's default ``linear`` method closely enough for our needs;
    avoids a NumPy dep on the core path.
    """
    if not sorted_values:
        raise ValueError("percentile of empty sequence")
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    pos = q * (len(sorted_values) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = pos - lo
    return float(sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * frac)


def _aggregate_custom_metric(events: list[OutcomeEvent], key: str) -> Any | None:
    """Compute a single custom metric over a window's events.

    Looks up ``key`` (supports dotted nested paths) in each event's
    ``data``. Values that are bool or numeric are aggregated:

      - all bool       → success rate (count(True) / count_seen)
      - all numeric    → mean
      - mixed / other  → None (caller drops the key)

    Returns ``None`` when no event carries the field, rather than 0 — a
    missing metric should be distinguishable from a metric that's actually
    zero. The SLO evaluator handles None / missing as a breach.
    """
    seen: list[Any] = []
    for e in events:
        v = _resolve_dotted(e.data, key)
        if v is not None:
            seen.append(v)
    if not seen:
        return None

    if all(isinstance(v, bool) for v in seen):
        return sum(1 for v in seen if v) / len(seen)
    if all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in seen):
        return sum(float(v) for v in seen) / len(seen)
    return None


def _resolve_dotted(data: dict[str, Any], key: str) -> Any | None:
    """Resolve a dotted key against a nested dict. Missing → None."""
    cur: Any = data
    for part in key.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur
