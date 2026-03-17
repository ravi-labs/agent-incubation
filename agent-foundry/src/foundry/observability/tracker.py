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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


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
