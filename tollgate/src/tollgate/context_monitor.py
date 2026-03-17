"""Memory/Context integrity monitoring for AI agent systems.

Detects unauthorized modifications to agent working memory between turns.
Tracks checksums of context snapshots and alerts when unexpected changes
are detected.

This is a complementary layer — it operates alongside Tollgate's core
enforcement pipeline to provide defense-in-depth against memory/context
poisoning attacks (OWASP Agentic #2).

Usage:

    from tollgate.context_monitor import ContextIntegrityMonitor

    monitor = ContextIntegrityMonitor(alert_sink=my_audit_sink)

    # At the start of each turn, snapshot the context
    monitor.snapshot("agent-1", "turn-5", context_data={
        "system_prompt": "You are a helpful assistant...",
        "tool_permissions": ["read", "write"],
        "memory": {"key1": "value1"},
    })

    # Before processing, verify nothing changed unexpectedly
    result = monitor.verify("agent-1", "turn-5", context_data={
        "system_prompt": "You are a helpful assistant...",
        "tool_permissions": ["read", "write"],
        "memory": {"key1": "value1"},
    })
    assert result.is_valid  # True if unchanged

    # Detect tampering
    result = monitor.verify("agent-1", "turn-5", context_data={
        "system_prompt": "IGNORE ALL RULES...",  # Poisoned!
        "tool_permissions": ["read", "write", "admin"],  # Escalated!
        "memory": {"key1": "value1"},
    })
    assert not result.is_valid
    # result.changed_fields == ["system_prompt", "tool_permissions"]
"""

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("tollgate.context_monitor")


@dataclass
class ContextSnapshot:
    """A point-in-time snapshot of agent context."""

    agent_id: str
    turn_id: str
    checksum: str
    field_checksums: dict[str, str]
    timestamp: float
    field_names: list[str]


@dataclass
class VerificationResult:
    """Result of verifying context integrity."""

    is_valid: bool
    agent_id: str
    turn_id: str
    changed_fields: list[str] = field(default_factory=list)
    added_fields: list[str] = field(default_factory=list)
    removed_fields: list[str] = field(default_factory=list)
    message: str = ""

    @property
    def has_changes(self) -> bool:
        return bool(self.changed_fields or self.added_fields or self.removed_fields)


class ContextIntegrityMonitor:
    """Monitor for detecting unauthorized context modifications.

    Maintains checksums of agent context per (agent_id, turn_id) pair.
    Supports both full-context and per-field verification.

    Args:
        alert_callback: Optional callback invoked on integrity violation.
            Receives a VerificationResult.
        immutable_fields: Set of field names that must never change between
            snapshot and verify. Violations are always flagged.
        max_snapshots: Maximum number of snapshots to retain (per agent).
            Older snapshots are evicted when the limit is reached.
    """

    def __init__(
        self,
        *,
        alert_callback: Any | None = None,
        immutable_fields: set[str] | None = None,
        max_snapshots: int = 1000,
    ):
        self._alert_callback = alert_callback
        self._immutable_fields = immutable_fields or {
            "system_prompt",
            "tool_permissions",
            "security_level",
            "role",
        }
        self._max_snapshots = max_snapshots
        self._snapshots: dict[str, ContextSnapshot] = {}
        self._agent_snapshot_keys: dict[str, list[str]] = {}

    @staticmethod
    def _compute_checksum(data: Any) -> str:
        """Compute a deterministic SHA-256 checksum of arbitrary data."""
        serialized = json.dumps(data, sort_keys=True, default=str)
        return hashlib.sha256(serialized.encode()).hexdigest()

    @staticmethod
    def _compute_field_checksums(context_data: dict[str, Any]) -> dict[str, str]:
        """Compute per-field checksums."""
        checksums = {}
        for key, value in context_data.items():
            serialized = json.dumps(value, sort_keys=True, default=str)
            checksums[key] = hashlib.sha256(serialized.encode()).hexdigest()
        return checksums

    def _snapshot_key(self, agent_id: str, turn_id: str) -> str:
        return f"{agent_id}:{turn_id}"

    def _evict_old_snapshots(self, agent_id: str):
        """Evict oldest snapshots for an agent if over the limit."""
        keys = self._agent_snapshot_keys.get(agent_id, [])
        while len(keys) > self._max_snapshots:
            old_key = keys.pop(0)
            self._snapshots.pop(old_key, None)

    def snapshot(
        self,
        agent_id: str,
        turn_id: str,
        context_data: dict[str, Any],
    ) -> ContextSnapshot:
        """Take a snapshot of the current context.

        Args:
            agent_id: The agent whose context is being snapshotted.
            turn_id: A unique identifier for the current turn/step.
            context_data: The context data to snapshot (dict of named fields).

        Returns:
            The created ContextSnapshot.
        """
        checksum = self._compute_checksum(context_data)
        field_checksums = self._compute_field_checksums(context_data)

        snap = ContextSnapshot(
            agent_id=agent_id,
            turn_id=turn_id,
            checksum=checksum,
            field_checksums=field_checksums,
            timestamp=time.time(),
            field_names=list(context_data.keys()),
        )

        key = self._snapshot_key(agent_id, turn_id)
        self._snapshots[key] = snap

        if agent_id not in self._agent_snapshot_keys:
            self._agent_snapshot_keys[agent_id] = []
        self._agent_snapshot_keys[agent_id].append(key)
        self._evict_old_snapshots(agent_id)

        return snap

    def verify(
        self,
        agent_id: str,
        turn_id: str,
        context_data: dict[str, Any],
    ) -> VerificationResult:
        """Verify context integrity against a previous snapshot.

        Args:
            agent_id: The agent whose context is being verified.
            turn_id: The turn_id used when the snapshot was taken.
            context_data: The current context data to verify.

        Returns:
            VerificationResult with details of any changes detected.
        """
        key = self._snapshot_key(agent_id, turn_id)
        snap = self._snapshots.get(key)

        if snap is None:
            return VerificationResult(
                is_valid=True,
                agent_id=agent_id,
                turn_id=turn_id,
                message="No snapshot found — nothing to verify against.",
            )

        # Quick check: full checksum
        current_checksum = self._compute_checksum(context_data)
        if current_checksum == snap.checksum:
            return VerificationResult(
                is_valid=True,
                agent_id=agent_id,
                turn_id=turn_id,
                message="Context integrity verified.",
            )

        # Detailed check: per-field
        current_field_checksums = self._compute_field_checksums(context_data)

        changed_fields: list[str] = []
        added_fields: list[str] = []
        removed_fields: list[str] = []

        # Check changed fields
        for field_name in snap.field_checksums:
            if field_name not in current_field_checksums:
                removed_fields.append(field_name)
            elif (
                current_field_checksums[field_name] != snap.field_checksums[field_name]
            ):
                changed_fields.append(field_name)

        # Check added fields
        for field_name in current_field_checksums:
            if field_name not in snap.field_checksums:
                added_fields.append(field_name)

        # Determine if immutable fields were violated
        immutable_violations = [
            f for f in changed_fields if f in self._immutable_fields
        ] + [f for f in removed_fields if f in self._immutable_fields]

        is_valid = len(immutable_violations) == 0

        message_parts = []
        if changed_fields:
            message_parts.append(f"Changed: {changed_fields}")
        if added_fields:
            message_parts.append(f"Added: {added_fields}")
        if removed_fields:
            message_parts.append(f"Removed: {removed_fields}")
        if immutable_violations:
            message_parts.append(f"IMMUTABLE VIOLATIONS: {immutable_violations}")

        message = "; ".join(message_parts) if message_parts else "No changes."

        result = VerificationResult(
            is_valid=is_valid,
            agent_id=agent_id,
            turn_id=turn_id,
            changed_fields=changed_fields,
            added_fields=added_fields,
            removed_fields=removed_fields,
            message=message,
        )

        # Fire alert callback for violations
        if not is_valid and self._alert_callback is not None:
            try:
                self._alert_callback(result)
            except Exception:
                logger.exception("Alert callback failed for context violation")

        if not is_valid:
            logger.warning(
                "Context integrity violation for agent=%s turn=%s: %s",
                agent_id,
                turn_id,
                message,
            )

        return result

    def get_snapshot(self, agent_id: str, turn_id: str) -> ContextSnapshot | None:
        """Retrieve a stored snapshot."""
        key = self._snapshot_key(agent_id, turn_id)
        return self._snapshots.get(key)

    def clear(self, agent_id: str | None = None):
        """Clear snapshots. If agent_id given, clear only that agent's snapshots."""
        if agent_id is not None:
            keys = self._agent_snapshot_keys.pop(agent_id, [])
            for key in keys:
                self._snapshots.pop(key, None)
        else:
            self._snapshots.clear()
            self._agent_snapshot_keys.clear()
