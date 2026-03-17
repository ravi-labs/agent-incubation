"""Agent Reputation System for Tollgate.

Tracks agent behavior patterns, computes trust scores, and enables
automatic privilege adjustment based on historical behavior.

Features:
- Trust score calculation based on execution history
- Anomaly detection for unusual behavior patterns
- Automatic privilege throttling for low-trust agents
- Reputation decay over time
- Multi-factor scoring (success rate, policy compliance, behavior patterns)

Example:
    from tollgate.reputation import ReputationManager, ReputationConfig

    # Create reputation manager
    manager = ReputationManager(ReputationConfig(
        initial_score=0.5,
        min_score=0.0,
        max_score=1.0,
    ))

    # Record behavior
    manager.record_success("agent-1")
    manager.record_failure("agent-1", "policy_violation")

    # Get current reputation
    score = manager.get_score("agent-1")

    # Check if agent can perform privileged operations
    if manager.can_perform("agent-1", min_score=0.7):
        # Allow privileged operation
        pass
"""

from __future__ import annotations

import logging
import math
import sqlite3
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

logger = logging.getLogger("tollgate.reputation")


class EventType(Enum):
    """Types of reputation-affecting events."""

    SUCCESS = "success"
    FAILURE = "failure"
    POLICY_VIOLATION = "policy_violation"
    APPROVAL_REQUIRED = "approval_required"
    APPROVAL_GRANTED = "approval_granted"
    APPROVAL_DENIED = "approval_denied"
    RATE_LIMITED = "rate_limited"
    ANOMALY_DETECTED = "anomaly_detected"


@dataclass
class ReputationConfig:
    """Configuration for the reputation system."""

    # Score bounds
    initial_score: float = 0.5
    min_score: float = 0.0
    max_score: float = 1.0

    # Score adjustments
    success_bonus: float = 0.01
    failure_penalty: float = 0.05
    violation_penalty: float = 0.15
    approval_denied_penalty: float = 0.1
    rate_limited_penalty: float = 0.02
    anomaly_penalty: float = 0.2

    # Decay settings
    decay_rate: float = 0.001  # Score decays toward initial over time
    decay_interval_hours: float = 24  # Hours between decay calculations

    # Sliding window for behavior analysis
    window_size: int = 100
    time_window_hours: float = 24

    # Thresholds
    low_trust_threshold: float = 0.3
    high_trust_threshold: float = 0.8

    # Rate limiting for low-trust agents
    low_trust_rate_limit: float = 0.5  # Reduce allowed rate by this factor


@dataclass
class ReputationEvent:
    """A single reputation-affecting event."""

    agent_id: str
    event_type: EventType
    timestamp: float
    score_delta: float
    reason: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "agent_id": self.agent_id,
            "event_type": self.event_type.value,
            "timestamp": self.timestamp,
            "score_delta": self.score_delta,
            "reason": self.reason,
            "metadata": self.metadata,
        }


@dataclass
class AgentReputation:
    """Current reputation state for an agent."""

    agent_id: str
    score: float
    event_count: int
    success_count: int
    failure_count: int
    violation_count: int
    last_event_time: float
    last_decay_time: float
    created_at: float

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "agent_id": self.agent_id,
            "score": self.score,
            "event_count": self.event_count,
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "violation_count": self.violation_count,
            "last_event_time": self.last_event_time,
            "last_decay_time": self.last_decay_time,
            "created_at": self.created_at,
        }

    @property
    def success_rate(self) -> float:
        """Calculate success rate."""
        total = self.success_count + self.failure_count
        return self.success_count / total if total > 0 else 0.0


class ReputationStore(Protocol):
    """Protocol for reputation storage backends."""

    async def get_reputation(self, agent_id: str) -> AgentReputation | None:
        """Get current reputation for an agent."""
        ...

    async def update_reputation(self, reputation: AgentReputation) -> None:
        """Update reputation for an agent."""
        ...

    async def record_event(self, event: ReputationEvent) -> None:
        """Record a reputation event."""
        ...

    async def get_events(
        self,
        agent_id: str,
        limit: int = 100,
        since: float | None = None,
    ) -> list[ReputationEvent]:
        """Get recent events for an agent."""
        ...


class InMemoryReputationStore:
    """In-memory reputation store for testing."""

    def __init__(self):
        self._reputations: dict[str, AgentReputation] = {}
        self._events: dict[str, deque[ReputationEvent]] = defaultdict(
            lambda: deque(maxlen=1000)
        )

    async def get_reputation(self, agent_id: str) -> AgentReputation | None:
        return self._reputations.get(agent_id)

    async def update_reputation(self, reputation: AgentReputation) -> None:
        self._reputations[reputation.agent_id] = reputation

    async def record_event(self, event: ReputationEvent) -> None:
        self._events[event.agent_id].append(event)

    async def get_events(
        self,
        agent_id: str,
        limit: int = 100,
        since: float | None = None,
    ) -> list[ReputationEvent]:
        events = list(self._events.get(agent_id, []))
        if since:
            events = [e for e in events if e.timestamp >= since]
        return events[-limit:]


class SQLiteReputationStore:
    """SQLite-backed reputation store."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        """Create database schema if needed."""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS agent_reputations (
                    agent_id TEXT PRIMARY KEY,
                    score REAL NOT NULL,
                    event_count INTEGER DEFAULT 0,
                    success_count INTEGER DEFAULT 0,
                    failure_count INTEGER DEFAULT 0,
                    violation_count INTEGER DEFAULT 0,
                    last_event_time REAL,
                    last_decay_time REAL,
                    created_at REAL NOT NULL
                );

                CREATE TABLE IF NOT EXISTS reputation_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    agent_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    timestamp REAL NOT NULL,
                    score_delta REAL NOT NULL,
                    reason TEXT,
                    metadata TEXT DEFAULT '{}',
                    FOREIGN KEY (agent_id) REFERENCES agent_reputations(agent_id)
                );

                CREATE INDEX IF NOT EXISTS idx_events_agent_time
                ON reputation_events(agent_id, timestamp DESC);
            """)
            conn.commit()
        finally:
            conn.close()

    async def get_reputation(self, agent_id: str) -> AgentReputation | None:
        conn = sqlite3.connect(self.db_path)
        try:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM agent_reputations WHERE agent_id = ?",
                (agent_id,)
            )
            row = cursor.fetchone()
            if not row:
                return None

            return AgentReputation(
                agent_id=row["agent_id"],
                score=row["score"],
                event_count=row["event_count"],
                success_count=row["success_count"],
                failure_count=row["failure_count"],
                violation_count=row["violation_count"],
                last_event_time=row["last_event_time"] or 0,
                last_decay_time=row["last_decay_time"] or 0,
                created_at=row["created_at"],
            )
        finally:
            conn.close()

    async def update_reputation(self, reputation: AgentReputation) -> None:
        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("""
                INSERT OR REPLACE INTO agent_reputations
                (agent_id, score, event_count, success_count, failure_count,
                 violation_count, last_event_time, last_decay_time, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                reputation.agent_id,
                reputation.score,
                reputation.event_count,
                reputation.success_count,
                reputation.failure_count,
                reputation.violation_count,
                reputation.last_event_time,
                reputation.last_decay_time,
                reputation.created_at,
            ))
            conn.commit()
        finally:
            conn.close()

    async def record_event(self, event: ReputationEvent) -> None:
        import json

        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("""
                INSERT INTO reputation_events
                (agent_id, event_type, timestamp, score_delta, reason, metadata)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                event.agent_id,
                event.event_type.value,
                event.timestamp,
                event.score_delta,
                event.reason,
                json.dumps(event.metadata),
            ))
            conn.commit()
        finally:
            conn.close()

    async def get_events(
        self,
        agent_id: str,
        limit: int = 100,
        since: float | None = None,
    ) -> list[ReputationEvent]:
        import json

        conn = sqlite3.connect(self.db_path)
        try:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            if since:
                cursor.execute("""
                    SELECT * FROM reputation_events
                    WHERE agent_id = ? AND timestamp >= ?
                    ORDER BY timestamp DESC
                    LIMIT ?
                """, (agent_id, since, limit))
            else:
                cursor.execute("""
                    SELECT * FROM reputation_events
                    WHERE agent_id = ?
                    ORDER BY timestamp DESC
                    LIMIT ?
                """, (agent_id, limit))

            events = []
            for row in cursor.fetchall():
                events.append(ReputationEvent(
                    agent_id=row["agent_id"],
                    event_type=EventType(row["event_type"]),
                    timestamp=row["timestamp"],
                    score_delta=row["score_delta"],
                    reason=row["reason"],
                    metadata=json.loads(row["metadata"]),
                ))

            return events
        finally:
            conn.close()


class ReputationManager:
    """Manages agent reputation scores and behavior tracking.

    The reputation system uses a multi-factor scoring model:
    1. Base score from success/failure ratio
    2. Penalties for policy violations and anomalies
    3. Time-based decay toward initial score
    """

    def __init__(
        self,
        config: ReputationConfig | None = None,
        store: ReputationStore | None = None,
    ):
        """Initialize the reputation manager.

        Args:
            config: Reputation configuration.
            store: Reputation storage backend.
        """
        self.config = config or ReputationConfig()
        self.store = store or InMemoryReputationStore()

    async def get_score(self, agent_id: str) -> float:
        """Get the current reputation score for an agent.

        Applies time-based decay before returning.
        """
        reputation = await self.store.get_reputation(agent_id)
        if not reputation:
            return self.config.initial_score

        # Apply decay
        reputation = await self._apply_decay(reputation)
        return reputation.score

    async def get_reputation(self, agent_id: str) -> AgentReputation | None:
        """Get full reputation data for an agent."""
        reputation = await self.store.get_reputation(agent_id)
        if reputation:
            reputation = await self._apply_decay(reputation)
        return reputation

    async def can_perform(
        self,
        agent_id: str,
        min_score: float | None = None,
    ) -> bool:
        """Check if an agent can perform an operation based on reputation.

        Args:
            agent_id: Agent identifier.
            min_score: Minimum required score. Defaults to high_trust_threshold.

        Returns:
            True if agent has sufficient reputation.
        """
        if min_score is None:
            min_score = self.config.high_trust_threshold

        score = await self.get_score(agent_id)
        return score >= min_score

    async def is_low_trust(self, agent_id: str) -> bool:
        """Check if an agent is in low-trust state."""
        score = await self.get_score(agent_id)
        return score < self.config.low_trust_threshold

    async def record_success(
        self,
        agent_id: str,
        reason: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> float:
        """Record a successful execution.

        Returns the new score.
        """
        return await self._record_event(
            agent_id,
            EventType.SUCCESS,
            self.config.success_bonus,
            reason,
            metadata,
        )

    async def record_failure(
        self,
        agent_id: str,
        reason: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> float:
        """Record a failed execution.

        Returns the new score.
        """
        return await self._record_event(
            agent_id,
            EventType.FAILURE,
            -self.config.failure_penalty,
            reason,
            metadata,
        )

    async def record_policy_violation(
        self,
        agent_id: str,
        reason: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> float:
        """Record a policy violation.

        Returns the new score.
        """
        return await self._record_event(
            agent_id,
            EventType.POLICY_VIOLATION,
            -self.config.violation_penalty,
            reason,
            metadata,
        )

    async def record_approval_denied(
        self,
        agent_id: str,
        reason: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> float:
        """Record a denied approval request.

        Returns the new score.
        """
        return await self._record_event(
            agent_id,
            EventType.APPROVAL_DENIED,
            -self.config.approval_denied_penalty,
            reason,
            metadata,
        )

    async def record_rate_limited(
        self,
        agent_id: str,
        reason: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> float:
        """Record a rate limiting event.

        Returns the new score.
        """
        return await self._record_event(
            agent_id,
            EventType.RATE_LIMITED,
            -self.config.rate_limited_penalty,
            reason,
            metadata,
        )

    async def record_anomaly(
        self,
        agent_id: str,
        reason: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> float:
        """Record an anomaly detection event.

        Returns the new score.
        """
        return await self._record_event(
            agent_id,
            EventType.ANOMALY_DETECTED,
            -self.config.anomaly_penalty,
            reason,
            metadata,
        )

    async def _record_event(
        self,
        agent_id: str,
        event_type: EventType,
        score_delta: float,
        reason: str | None,
        metadata: dict[str, Any] | None,
    ) -> float:
        """Internal method to record an event and update reputation."""
        now = time.time()

        # Get or create reputation
        reputation = await self.store.get_reputation(agent_id)
        if not reputation:
            reputation = AgentReputation(
                agent_id=agent_id,
                score=self.config.initial_score,
                event_count=0,
                success_count=0,
                failure_count=0,
                violation_count=0,
                last_event_time=now,
                last_decay_time=now,
                created_at=now,
            )

        # Apply decay first
        reputation = await self._apply_decay(reputation)

        # Update score
        new_score = reputation.score + score_delta
        new_score = max(self.config.min_score, min(self.config.max_score, new_score))

        # Update counts
        event_count = reputation.event_count + 1
        success_count = reputation.success_count
        failure_count = reputation.failure_count
        violation_count = reputation.violation_count

        if event_type == EventType.SUCCESS:
            success_count += 1
        elif event_type in (EventType.FAILURE, EventType.APPROVAL_DENIED):
            failure_count += 1
        elif event_type == EventType.POLICY_VIOLATION:
            violation_count += 1

        # Create updated reputation
        updated = AgentReputation(
            agent_id=agent_id,
            score=new_score,
            event_count=event_count,
            success_count=success_count,
            failure_count=failure_count,
            violation_count=violation_count,
            last_event_time=now,
            last_decay_time=reputation.last_decay_time,
            created_at=reputation.created_at,
        )

        # Persist
        await self.store.update_reputation(updated)

        # Record event
        event = ReputationEvent(
            agent_id=agent_id,
            event_type=event_type,
            timestamp=now,
            score_delta=score_delta,
            reason=reason,
            metadata=metadata or {},
        )
        await self.store.record_event(event)

        return new_score

    async def _apply_decay(self, reputation: AgentReputation) -> AgentReputation:
        """Apply time-based decay to reputation score."""
        now = time.time()
        hours_since_decay = (now - reputation.last_decay_time) / 3600

        if hours_since_decay < self.config.decay_interval_hours:
            return reputation  # Not enough time passed

        # Calculate decay
        decay_periods = hours_since_decay / self.config.decay_interval_hours
        decay_factor = math.exp(-self.config.decay_rate * decay_periods)

        # Decay toward initial score
        target = self.config.initial_score
        current = reputation.score
        new_score = target + (current - target) * decay_factor

        # Clamp
        new_score = max(self.config.min_score, min(self.config.max_score, new_score))

        # Update reputation
        updated = AgentReputation(
            agent_id=reputation.agent_id,
            score=new_score,
            event_count=reputation.event_count,
            success_count=reputation.success_count,
            failure_count=reputation.failure_count,
            violation_count=reputation.violation_count,
            last_event_time=reputation.last_event_time,
            last_decay_time=now,
            created_at=reputation.created_at,
        )

        await self.store.update_reputation(updated)
        return updated

    async def get_rate_limit_factor(self, agent_id: str) -> float:
        """Get rate limit adjustment factor based on reputation.

        Returns a factor between 0.0 and 1.0 that can be multiplied
        with the base rate limit to get the adjusted limit.

        Low-trust agents get reduced rate limits.
        """
        score = await self.get_score(agent_id)

        if score < self.config.low_trust_threshold:
            return self.config.low_trust_rate_limit
        if score >= self.config.high_trust_threshold:
            return 1.0  # Full rate limit for high-trust agents
        # Linear interpolation between low and high trust
        range_size = (
            self.config.high_trust_threshold - self.config.low_trust_threshold
        )
        position = (score - self.config.low_trust_threshold) / range_size
        return self.config.low_trust_rate_limit + (
            1.0 - self.config.low_trust_rate_limit
        ) * position


class ReputationAuditSink:
    """AuditSink that updates reputation based on audit events.

    Example:
        manager = ReputationManager()
        sink = ReputationAuditSink(manager)

        # Add to ControlTower
        tower = ControlTower(
            ...,
            audit=CompositeAuditSink([JsonlAuditSink("audit.jsonl"), sink]),
        )
    """

    def __init__(self, manager: ReputationManager):
        self._manager = manager

    def emit(self, event) -> None:
        """Process an audit event and update reputation."""
        import asyncio

        try:
            asyncio.run(self._process_event(event))
        except RuntimeError:
            # Already in async context
            asyncio.create_task(self._process_event(event))

    async def _process_event(self, event) -> None:
        """Internal async method to process event."""
        agent_id = event.agent.agent_id
        outcome = event.outcome.value
        decision = event.decision.decision.value

        # Map outcome to reputation event
        if outcome == "executed":
            await self._manager.record_success(
                agent_id,
                reason=f"Successful execution of {event.tool_request.tool}",
                metadata={"tool": event.tool_request.tool},
            )
        elif outcome == "blocked":
            if decision == "DENY":
                await self._manager.record_policy_violation(
                    agent_id,
                    reason=event.decision.reason,
                    metadata={"tool": event.tool_request.tool},
                )
            else:
                await self._manager.record_failure(
                    agent_id,
                    reason=f"Blocked: {outcome}",
                    metadata={"tool": event.tool_request.tool},
                )
        elif outcome == "approval_denied":
            await self._manager.record_approval_denied(
                agent_id,
                reason="Human denied approval",
                metadata={"tool": event.tool_request.tool},
            )
        elif outcome in ("failed", "timeout"):
            await self._manager.record_failure(
                agent_id,
                reason=f"Execution {outcome}",
                metadata={"tool": event.tool_request.tool},
            )
