"""
arc.core.lifecycle.approvals — pending-approval store for DEFERRED promotions.

When ``PromotionService.promote()`` returns a ``DEFERRED`` outcome (typically
because the target stage is in ``require_human``), the decision is enqueued
into a ``PendingApprovalStore``. A reviewer later resolves the entry —
approve or reject — via ``PromotionService.resolve_approval()``.

The store is a separate concern from the promotion audit log:
  - The audit log records every decision (including the DEFERRED itself).
    It's append-only, immutable, useful for compliance review.
  - The pending-approval store tracks *current state*: which decisions are
    still waiting on a human, who eventually resolved them, and how.

Two implementations ship:

  - InMemoryPendingApprovalStore — for tests + harness.

  - JsonlPendingApprovalStore   — file-backed. Each entry is one JSON line;
                                  resolution writes a new line with the same
                                  approval_id. ``list_pending`` reads the file
                                  and keeps the latest entry per approval_id.
                                  Append-only on disk, idempotent on reload.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from .pipeline import (
    GateCheckResult,
    PromotionDecision,
    PromotionOutcome,
    PromotionRequest,
)
from .stages import LifecycleStage


# ── Status of a pending entry ───────────────────────────────────────────────


PENDING  = "pending"
APPROVED = "approved"
REJECTED = "rejected"


# ── Pending-approval entry ──────────────────────────────────────────────────


@dataclass
class PendingApproval:
    """One DEFERRED promotion waiting on (or recently resolved by) a human.

    The ``decision`` field carries the original DEFERRED ``PromotionDecision``
    so the dashboard has full context: gate results, requester, justification.
    """
    approval_id: str
    created_at: str                       # ISO when enqueued
    decision: PromotionDecision           # the original DEFERRED decision
    status: str = PENDING                 # PENDING | APPROVED | REJECTED
    resolved_at: str = ""                 # ISO when resolved (empty if still pending)
    resolved_by: str = ""                 # reviewer username
    resolution_reason: str = ""

    @property
    def is_pending(self) -> bool:
        return self.status == PENDING

    def to_dict(self) -> dict:
        return {
            "approval_id":       self.approval_id,
            "created_at":        self.created_at,
            "status":            self.status,
            "resolved_at":       self.resolved_at,
            "resolved_by":       self.resolved_by,
            "resolution_reason": self.resolution_reason,
            "decision":          self.decision.to_dict(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "PendingApproval":
        return cls(
            approval_id       = d["approval_id"],
            created_at        = d.get("created_at", ""),
            decision          = _decision_from_dict(d["decision"]),
            status            = d.get("status", PENDING),
            resolved_at       = d.get("resolved_at", ""),
            resolved_by       = d.get("resolved_by", ""),
            resolution_reason = d.get("resolution_reason", ""),
        )


def _decision_from_dict(d: dict) -> PromotionDecision:
    """Reconstruct a PromotionDecision from its to_dict() form.

    Mirrors the reconstruction in pipeline.py — kept local so this module
    doesn't depend on a private helper across files.
    """
    request = PromotionRequest(
        agent_id      = d["agent_id"],
        current_stage = LifecycleStage(d["current_stage"]),
        target_stage  = LifecycleStage(d["target_stage"]),
        requester     = d["requester"],
        justification = d["justification"],
        evidence      = d.get("evidence", {}),
        requested_at  = d.get("requested_at", ""),
    )
    return PromotionDecision(
        request      = request,
        outcome      = PromotionOutcome(d["outcome"]),
        gate_results = [
            GateCheckResult(
                name   = g["name"],
                passed = g["passed"],
                reason = g.get("reason", ""),
            )
            for g in d.get("gate_results", [])
        ],
        reason     = d.get("reason", ""),
        decided_by = d.get("decided_by", "system"),
        decided_at = d.get("decided_at", ""),
    )


# ── Store protocol ──────────────────────────────────────────────────────────


class PendingApprovalStore(Protocol):
    """Persistent store for pending + resolved approvals."""

    def enqueue(self, decision: PromotionDecision) -> str:
        """Record a DEFERRED decision and return its assigned approval_id."""
        ...

    def get(self, approval_id: str) -> PendingApproval | None: ...

    def list_pending(self) -> list[PendingApproval]:
        """All entries with status = PENDING."""
        ...

    def list_all(self) -> list[PendingApproval]:
        """All entries (pending + resolved). Useful for audit."""
        ...

    def resolve(
        self,
        approval_id: str,
        *,
        approved: bool,
        reviewer: str,
        reason: str = "",
    ) -> PendingApproval:
        """Mark an entry as APPROVED or REJECTED. Returns the updated entry."""
        ...


# ── In-memory implementation ────────────────────────────────────────────────


class InMemoryPendingApprovalStore:
    """Default in-memory store. Good for tests + harness.

    Loses state on process restart — use ``JsonlPendingApprovalStore`` for
    anything you want to survive a deploy.
    """

    def __init__(self) -> None:
        self._entries: dict[str, PendingApproval] = {}

    def enqueue(self, decision: PromotionDecision) -> str:
        approval_id = str(uuid.uuid4())
        self._entries[approval_id] = PendingApproval(
            approval_id = approval_id,
            created_at  = _now(),
            decision    = decision,
        )
        return approval_id

    def get(self, approval_id: str) -> PendingApproval | None:
        return self._entries.get(approval_id)

    def list_pending(self) -> list[PendingApproval]:
        return [e for e in self._entries.values() if e.is_pending]

    def list_all(self) -> list[PendingApproval]:
        return list(self._entries.values())

    def resolve(
        self,
        approval_id: str,
        *,
        approved: bool,
        reviewer: str,
        reason: str = "",
    ) -> PendingApproval:
        entry = self._entries.get(approval_id)
        if entry is None:
            raise KeyError(f"no pending approval: {approval_id}")
        if not entry.is_pending:
            raise ValueError(
                f"approval {approval_id} already resolved as {entry.status}"
            )
        entry.status            = APPROVED if approved else REJECTED
        entry.resolved_at       = _now()
        entry.resolved_by       = reviewer
        entry.resolution_reason = reason
        return entry


# ── JSONL-backed implementation ─────────────────────────────────────────────


class JsonlPendingApprovalStore:
    """Append-only JSONL store of pending + resolved approvals.

    Layout: one JSON object per line. Re-resolving writes a new line with
    the same ``approval_id``; readers keep the latest entry per id. This
    gives us:

      - Atomic writes (one append per state change; no rewrite-in-place).
      - A natural audit trail of who resolved what when (latest wins on
        reconstruction; the prior lines are retained as history).
      - Crash-safety: a partial write is just a torn JSON line that the
        reader skips.

    Use ``list_history(approval_id)`` to see every state an entry passed
    through (e.g., enqueued at T0, approved at T1).
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # ── Internal: read every line, dedup by approval_id, latest wins ────

    def _read_all(self) -> dict[str, PendingApproval]:
        if not self.path.exists():
            return {}
        out: dict[str, PendingApproval] = {}
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                try:
                    entry = PendingApproval.from_dict(data)
                except Exception:
                    continue
                out[entry.approval_id] = entry  # latest line wins
        return out

    def _append(self, entry: PendingApproval) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry.to_dict(), ensure_ascii=False))
            f.write("\n")

    # ── Protocol methods ────────────────────────────────────────────────

    def enqueue(self, decision: PromotionDecision) -> str:
        approval_id = str(uuid.uuid4())
        entry = PendingApproval(
            approval_id = approval_id,
            created_at  = _now(),
            decision    = decision,
        )
        self._append(entry)
        return approval_id

    def get(self, approval_id: str) -> PendingApproval | None:
        return self._read_all().get(approval_id)

    def list_pending(self) -> list[PendingApproval]:
        return [e for e in self._read_all().values() if e.is_pending]

    def list_all(self) -> list[PendingApproval]:
        return list(self._read_all().values())

    def resolve(
        self,
        approval_id: str,
        *,
        approved: bool,
        reviewer: str,
        reason: str = "",
    ) -> PendingApproval:
        entries = self._read_all()
        entry = entries.get(approval_id)
        if entry is None:
            raise KeyError(f"no pending approval: {approval_id}")
        if not entry.is_pending:
            raise ValueError(
                f"approval {approval_id} already resolved as {entry.status}"
            )
        # Build the new state and append it. Old lines stay on disk as
        # history — readers see the latest state for this approval_id.
        entry.status            = APPROVED if approved else REJECTED
        entry.resolved_at       = _now()
        entry.resolved_by       = reviewer
        entry.resolution_reason = reason
        self._append(entry)
        return entry

    # ── Bonus: history for one entry (every state line on disk) ─────────

    def list_history(self, approval_id: str) -> list[PendingApproval]:
        """Return every line on disk for a given approval_id, oldest first.

        Useful for audit reviews — shows the full trajectory (enqueued at
        T0, approved at T1, etc.) rather than just the final state.
        """
        if not self.path.exists():
            return []
        out: list[PendingApproval] = []
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    if data.get("approval_id") != approval_id:
                        continue
                    out.append(PendingApproval.from_dict(data))
                except (json.JSONDecodeError, KeyError, ValueError):
                    continue
        return out


# ── Helpers ─────────────────────────────────────────────────────────────────


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
