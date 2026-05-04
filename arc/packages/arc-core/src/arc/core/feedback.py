"""arc.core.feedback — capture human corrections on past agent decisions.

Reviewers + adjusters disagree with the agent sometimes. Capturing those
disagreements as structured ``Correction`` records is the first layer
of the feedback loop. Later layers (in-context few-shot injection,
auto-policy proposals) all read from the same store this module writes
to.

Design

  Correction          A single structured record — what was decided,
                      what should have been decided, who flagged it,
                      why, and how severely wrong it was.

  CorrectionsStore    Protocol — anyone can plug a custom backend
                      (DynamoDB, Postgres, S3-with-Athena) without
                      changing the agent code.

  JsonlCorrectionsStore  Default file-backed implementation. Append-only,
                      one record per line. Same shape as the audit log
                      so corrections + audit trail can be correlated by
                      ``audit_row_id`` joins.

Where it's used

  - Frontend ``/agents/<id>/live`` "Flag as wrong" button writes here.
  - Layer 3 (in-context injection) reads recent rows here.
  - Health view roll-ups aggregate here.
  - Pega → arc feedback loop (HITL touchpoint #3) writes here when a
    downstream adjuster reclassifies the case.

PII concerns

  Correction.original_decision and corrected_decision can carry
  domain-specific fields (case_type, amount, participant_id). The
  ``reason`` field is free-text and should be PII-redacted at the sink
  layer (Datadog Sensitive Data Scanner) — *not* in this module, which
  must stay domain-agnostic.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Protocol


# ── Severity scale ──────────────────────────────────────────────────────────
#
# Keep this small. Three levels is enough for the human reporting it; the
# downstream ML pipeline (Layer 3+) can derive finer-grained signals from
# the original/corrected diff if needed.

SEVERITY_LEVELS: tuple[str, ...] = ("minor", "moderate", "critical")


# ── Correction record ──────────────────────────────────────────────────────


@dataclass
class Correction:
    """A single human flagging an agent decision as wrong.

    The shape is intentionally domain-agnostic: ``original_decision`` and
    ``corrected_decision`` are dicts so any agent can stuff its own
    structured fields in (case_type, routing.team, severity, …).
    """

    correction_id:       str
    timestamp:           str        # ISO 8601 UTC
    agent_id:            str
    audit_row_id:        str        # links back to the original audit row
    reviewer:            str        # email / username / SSO subject
    severity:            str        # "minor" | "moderate" | "critical"
    reason:              str        # free-text rationale
    original_decision:   dict[str, Any]   # what the agent did
    corrected_decision:  dict[str, Any]   # what it should have done
    schema_version:      str = ""         # of the agent at the time
    metadata:            dict[str, Any] = field(default_factory=dict)

    # ── Construction helpers ────────────────────────────────────────────

    @classmethod
    def new(
        cls,
        *,
        agent_id: str,
        audit_row_id: str,
        reviewer: str,
        severity: str,
        reason: str,
        original_decision: dict[str, Any],
        corrected_decision: dict[str, Any],
        schema_version: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> "Correction":
        """Construct a Correction with auto-generated id + timestamp."""
        if severity not in SEVERITY_LEVELS:
            raise ValueError(
                f"severity must be one of {SEVERITY_LEVELS}, got {severity!r}"
            )
        if not reviewer:
            raise ValueError("reviewer is required (don't accept anonymous corrections)")
        return cls(
            correction_id     = f"corr-{uuid.uuid4().hex[:12]}",
            timestamp         = datetime.now(timezone.utc).isoformat(timespec="seconds"),
            agent_id          = agent_id,
            audit_row_id      = audit_row_id,
            reviewer          = reviewer,
            severity          = severity,
            reason            = reason,
            original_decision = dict(original_decision),
            corrected_decision= dict(corrected_decision),
            schema_version    = schema_version,
            metadata          = dict(metadata) if metadata else {},
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Correction":
        # Forward-compat: ignore unknown keys, fill missing with defaults.
        known = {
            "correction_id", "timestamp", "agent_id", "audit_row_id",
            "reviewer", "severity", "reason",
            "original_decision", "corrected_decision",
            "schema_version", "metadata",
        }
        kept = {k: v for k, v in d.items() if k in known}
        kept.setdefault("schema_version", "")
        kept.setdefault("metadata", {})
        return cls(**kept)


# ── Store Protocol ──────────────────────────────────────────────────────────


class CorrectionsStore(Protocol):
    """Append-only store of corrections.

    Implementations:
      - ``JsonlCorrectionsStore`` — file-backed, default for dev + sandbox
      - (future) ``DynamoDbCorrectionsStore`` — production
      - (future) ``S3CorrectionsStore`` — compliance retention with
        Object Lock
    """

    def record(self, correction: Correction) -> None: ...

    def list(
        self,
        *,
        agent_id: str | None = None,
        limit: int | None = None,
        since: str | None = None,    # ISO 8601 — corrections strictly after this
    ) -> list[Correction]: ...

    def summary(
        self,
        *,
        agent_id: str | None = None,
        since: str | None = None,
    ) -> dict[str, Any]: ...


# ── JSONL implementation ────────────────────────────────────────────────────


class JsonlCorrectionsStore:
    """File-backed corrections store. Append-only, one JSON object per line.

    Mirrors the JSONL shape of the audit log on purpose so the two streams
    can be joined downstream by ``audit_row_id`` in Datadog / Athena / etc.

    Thread-safety: file appends are atomic on POSIX for reasonable line
    sizes; for high-concurrency workloads, switch to a backend with
    real append-only semantics (DynamoDB, S3 with versioned objects).
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, correction: Correction) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(correction.to_dict()) + "\n")

    def list(
        self,
        *,
        agent_id: str | None = None,
        limit: int | None = None,
        since: str | None = None,
    ) -> list[Correction]:
        if not self.path.exists():
            return []
        out: list[Correction] = []
        for raw in self._iter_rows():
            if agent_id is not None and raw.get("agent_id") != agent_id:
                continue
            if since is not None and (raw.get("timestamp") or "") <= since:
                continue
            try:
                out.append(Correction.from_dict(raw))
            except Exception:
                # Tolerate bad rows — never break a dashboard read on
                # one malformed line.
                continue
        # Newest first (most recent corrections matter most).
        out.reverse()
        if limit is not None:
            out = out[:limit]
        return out

    def summary(
        self,
        *,
        agent_id: str | None = None,
        since: str | None = None,
    ) -> dict[str, Any]:
        """Aggregate counts for the dashboard's "corrections" panel."""
        rows = self.list(agent_id=agent_id, since=since, limit=None)
        by_severity: dict[str, int] = {s: 0 for s in SEVERITY_LEVELS}
        by_reviewer: dict[str, int] = {}
        # Most-corrected pattern: hash(original_decision) → count.
        # Cheap N^1 — small volumes OK; switch to a counter index if it grows.
        by_pattern: dict[str, int] = {}

        for c in rows:
            by_severity[c.severity] = by_severity.get(c.severity, 0) + 1
            by_reviewer[c.reviewer] = by_reviewer.get(c.reviewer, 0) + 1
            pattern = self._summarise_pattern(c)
            by_pattern[pattern] = by_pattern.get(pattern, 0) + 1

        # Top 3 patterns and reviewers (sorted desc by count)
        top_patterns = sorted(by_pattern.items(), key=lambda kv: kv[1], reverse=True)[:3]
        top_reviewers = sorted(by_reviewer.items(), key=lambda kv: kv[1], reverse=True)[:3]

        return {
            "total":         len(rows),
            "by_severity":   by_severity,
            "by_reviewer":   dict(top_reviewers),
            "top_patterns":  [{"pattern": k, "count": v} for k, v in top_patterns],
        }

    # ── internals ──────────────────────────────────────────────────────

    def _iter_rows(self) -> Iterable[dict[str, Any]]:
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue

    @staticmethod
    def _summarise_pattern(c: Correction) -> str:
        """Compact signature of *what kind* of mistake this was.

        Used to identify recurring failure patterns in the rollup. We
        prefer the agent's domain-specific shape (e.g. case_type) over a
        generic field hash because compliance reviewers want readable
        labels, not opaque digests.
        """
        orig = c.original_decision or {}
        corr = c.corrected_decision or {}

        # Common shape: case_type / case_subtype (retirement email triage).
        if "case_type" in orig and "case_type" in corr:
            return f"{orig['case_type']} → {corr['case_type']}"

        # Routing-team mistake (any agent that does routing).
        if "team" in orig and "team" in corr:
            return f"team: {orig['team']} → {corr['team']}"

        # Generic fallback — first differing key.
        for k in (set(orig) | set(corr)):
            if orig.get(k) != corr.get(k):
                return f"{k}: {orig.get(k)} → {corr.get(k)}"

        return "(unspecified)"


__all__ = [
    "Correction",
    "CorrectionsStore",
    "JsonlCorrectionsStore",
    "SEVERITY_LEVELS",
]
