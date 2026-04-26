"""
arc.platform.common.data — shared loaders for the dashboards.

Both dashboards display the same kinds of data: agents (from a manifest
store), audit events (from JSONL audit sinks), and promotion decisions
(from a JsonlPromotionAuditLog). This module is the single typed
data-access layer they both build on.

The dashboards never read JSONL files directly; they go through
``PlatformData``. This keeps the dashboards thin (templates + routing
only) and lets us add caching, indexing, or a real database backend
later without touching either app.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from arc.core import (
    AgentManifest,
    DirectoryManifestStore,
    JsonlPromotionAuditLog,
    LifecycleStage,
    PromotionDecision,
    PromotionOutcome,
)


# ── Lightweight view models the templates render ────────────────────────────


@dataclass
class AgentSummary:
    """Compact view of an agent for inventory listings."""
    agent_id: str
    version: str
    owner: str
    description: str
    lifecycle_stage: str
    status: str
    environment: str
    allowed_effects: list[str]
    tags: list[str] = field(default_factory=list)

    @classmethod
    def from_manifest(cls, m: AgentManifest) -> "AgentSummary":
        return cls(
            agent_id=m.agent_id,
            version=m.version,
            owner=m.owner,
            description=m.description,
            lifecycle_stage=m.lifecycle_stage.value,
            status=m.status.value,
            environment=m.environment,
            allowed_effects=[e.value for e in m.allowed_effects],
            tags=list(m.tags),
        )


@dataclass
class AuditEvent:
    """One ALLOW / ASK / DENY decision row from a JSONL audit log."""
    timestamp: str
    agent_id: str
    effect: str
    decision: str
    reason: str = ""
    tool: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "AuditEvent":
        return cls(
            timestamp=str(d.get("timestamp") or d.get("ts") or ""),
            agent_id=str(d.get("agent_id", "")),
            effect=str(
                d.get("resource_type")
                or d.get("effect")
                or d.get("intent_resource_type")
                or ""
            ),
            decision=str(d.get("decision", "UNKNOWN")).upper(),
            reason=str(d.get("reason") or d.get("intent_reason") or ""),
            tool=str(d.get("tool", "")),
        )


@dataclass
class PendingApproval:
    """A promotion decision currently in DEFERRED state, awaiting human review."""
    agent_id: str
    current_stage: str
    target_stage: str
    requester: str
    justification: str
    requested_at: str
    decided_at: str
    reason: str

    @classmethod
    def from_decision(cls, d: PromotionDecision) -> "PendingApproval":
        return cls(
            agent_id=d.request.agent_id,
            current_stage=d.request.current_stage.value,
            target_stage=d.request.target_stage.value,
            requester=d.request.requester,
            justification=d.request.justification,
            requested_at=d.request.requested_at,
            decided_at=d.decided_at,
            reason=d.reason,
        )


# ── Configuration ───────────────────────────────────────────────────────────


@dataclass
class PlatformDataConfig:
    """Where the dashboards read data from.

    Defaults assume an in-monorepo layout (``arc/agents/`` for manifests,
    ``./audit.jsonl`` for the runtime audit, ``./promotions.jsonl`` for
    the promotion audit). All paths are optional; missing files yield
    empty result sets, not errors — the dashboards stay viewable in a
    cold environment with no traffic yet.
    """
    manifest_root: Path | None = None       # DirectoryManifestStore root
    audit_log_path: Path | None = None      # JsonlAuditSink path
    promotion_log_path: Path | None = None  # JsonlPromotionAuditLog path

    @classmethod
    def default(cls, repo_root: Path | None = None) -> "PlatformDataConfig":
        """Resolve defaults relative to a monorepo checkout root."""
        root = (repo_root or Path.cwd()).resolve()
        return cls(
            manifest_root=root / "arc" / "agents",
            audit_log_path=root / "audit.jsonl",
            promotion_log_path=root / "promotions.jsonl",
        )


# ── The data accessor ───────────────────────────────────────────────────────


class PlatformData:
    """Single typed entry point both dashboards depend on.

    Construction is cheap; reads happen on demand. Pass either an
    explicit ``PlatformDataConfig`` or rely on ``PlatformDataConfig.default()``.

    Methods deliberately return plain view-model dataclasses, not the
    underlying domain objects — this keeps templates simple and lets
    us evolve internals freely.
    """

    def __init__(self, config: PlatformDataConfig | None = None) -> None:
        self.config = config or PlatformDataConfig.default()

    # ── Manifests ───────────────────────────────────────────────────────

    def manifest_store(self) -> DirectoryManifestStore | None:
        """Return a DirectoryManifestStore, or None if no root is configured."""
        root = self.config.manifest_root
        if root is None or not root.exists():
            return None
        return DirectoryManifestStore(root)

    def list_agents(self) -> list[AgentSummary]:
        """Every agent that has a manifest under the configured root."""
        store = self.manifest_store()
        if store is None:
            return []
        out: list[AgentSummary] = []
        for agent_id in store.agent_ids():
            try:
                out.append(AgentSummary.from_manifest(store.load(agent_id)))
            except Exception:
                # Bad manifest → skip rather than break the whole dashboard.
                continue
        return out

    def get_agent(self, agent_id: str) -> AgentSummary | None:
        store = self.manifest_store()
        if store is None or not store.exists(agent_id):
            return None
        return AgentSummary.from_manifest(store.load(agent_id))

    def agents_by_stage(self) -> dict[str, list[AgentSummary]]:
        """Group all agents by their current lifecycle stage."""
        out: dict[str, list[AgentSummary]] = {s.value: [] for s in LifecycleStage}
        for agent in self.list_agents():
            out.setdefault(agent.lifecycle_stage, []).append(agent)
        return out

    # ── Runtime audit (per-tool-call decisions) ─────────────────────────

    def list_audit_events(
        self,
        *,
        limit: int | None = 100,
        agent_id: str | None = None,
    ) -> list[AuditEvent]:
        path = self.config.audit_log_path
        if path is None or not path.exists():
            return []
        events: list[AuditEvent] = []
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ev = AuditEvent.from_dict(raw)
                if agent_id is not None and ev.agent_id != agent_id:
                    continue
                events.append(ev)
        # Newest first — users care about recent decisions.
        events.reverse()
        if limit is not None:
            events = events[:limit]
        return events

    def audit_summary(self) -> dict[str, Any]:
        """Counts that fit on a header card: total / ALLOW / ASK / DENY."""
        all_events = self.list_audit_events(limit=None)
        counts = {"total": len(all_events), "ALLOW": 0, "ASK": 0, "DENY": 0}
        for ev in all_events:
            counts[ev.decision] = counts.get(ev.decision, 0) + 1
        return counts

    # ── Promotion audit (per-stage-change decisions) ────────────────────

    def _promotion_log(self) -> JsonlPromotionAuditLog | None:
        path = self.config.promotion_log_path
        if path is None:
            return None
        # JsonlPromotionAuditLog handles missing files gracefully (empty list).
        return JsonlPromotionAuditLog(path)

    def list_promotions(
        self,
        *,
        agent_id: str | None = None,
    ) -> list[PromotionDecision]:
        log = self._promotion_log()
        if log is None:
            return []
        return log.history(agent_id=agent_id)

    def pending_approvals(self) -> list[PendingApproval]:
        """Every DEFERRED promotion decision currently awaiting human review."""
        return [
            PendingApproval.from_decision(d)
            for d in self.list_promotions()
            if d.outcome == PromotionOutcome.DEFERRED
        ]

    def promotion_summary(self) -> dict[str, int]:
        decisions = self.list_promotions()
        return {
            "total":    len(decisions),
            "APPROVED": sum(1 for d in decisions if d.outcome == PromotionOutcome.APPROVED),
            "REJECTED": sum(1 for d in decisions if d.outcome == PromotionOutcome.REJECTED),
            "DEFERRED": sum(1 for d in decisions if d.outcome == PromotionOutcome.DEFERRED),
        }
