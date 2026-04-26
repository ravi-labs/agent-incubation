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
    GateChecker,
    JsonlPendingApprovalStore,
    JsonlPromotionAuditLog,
    LifecycleStage,
    PromotionDecision,
    PromotionOutcome,
    PromotionService,
    apply_decision,
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
    """A promotion decision currently awaiting human review.

    View-model for the dashboard. Adds ``approval_id`` (the store key
    needed for resolve actions) and ``status`` (PENDING / APPROVED /
    REJECTED) to the underlying decision data.
    """
    approval_id: str
    status: str
    agent_id: str
    current_stage: str
    target_stage: str
    requester: str
    justification: str
    requested_at: str
    decided_at: str
    reason: str
    resolved_at: str = ""
    resolved_by: str = ""
    resolution_reason: str = ""

    @classmethod
    def from_entry(cls, entry) -> "PendingApproval":
        """Build a view model from an arc.core.lifecycle.PendingApproval store entry."""
        d = entry.decision
        return cls(
            approval_id       = entry.approval_id,
            status            = entry.status,
            agent_id          = d.request.agent_id,
            current_stage     = d.request.current_stage.value,
            target_stage      = d.request.target_stage.value,
            requester         = d.request.requester,
            justification     = d.request.justification,
            requested_at      = d.request.requested_at,
            decided_at        = d.decided_at,
            reason            = d.reason,
            resolved_at       = entry.resolved_at,
            resolved_by       = entry.resolved_by,
            resolution_reason = entry.resolution_reason,
        )


# ── Configuration ───────────────────────────────────────────────────────────


@dataclass
class PlatformDataConfig:
    """Where the dashboards read data from.

    Defaults assume an in-monorepo layout (``arc/agents/`` for manifests,
    ``./audit.jsonl`` for the runtime audit, ``./promotions.jsonl`` for
    the promotion audit, ``./pending-approvals.jsonl`` for the approval
    queue). All paths are optional; missing files yield empty result
    sets, not errors — the dashboards stay viewable in a cold environment
    with no traffic yet.
    """
    manifest_root: Path | None = None             # DirectoryManifestStore root
    audit_log_path: Path | None = None            # JsonlAuditSink path
    promotion_log_path: Path | None = None        # JsonlPromotionAuditLog path
    pending_approvals_path: Path | None = None    # JsonlPendingApprovalStore path

    @classmethod
    def default(cls, repo_root: Path | None = None) -> "PlatformDataConfig":
        """Resolve defaults relative to a monorepo checkout root."""
        root = (repo_root or Path.cwd()).resolve()
        return cls(
            manifest_root=root / "arc" / "agents",
            audit_log_path=root / "audit.jsonl",
            promotion_log_path=root / "promotions.jsonl",
            pending_approvals_path=root / "pending-approvals.jsonl",
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

    # ── Pending-approval store (DEFERRED promotion handoff) ─────────────

    def approval_store(self) -> JsonlPendingApprovalStore | None:
        """Return the JSONL pending-approval store, or None if no path is set."""
        path = self.config.pending_approvals_path
        if path is None:
            return None
        return JsonlPendingApprovalStore(path)

    def pending_approvals(self) -> list[PendingApproval]:
        """Every approval still in PENDING state — what the dashboard shows."""
        store = self.approval_store()
        if store is None:
            return []
        return [PendingApproval.from_entry(e) for e in store.list_pending()]

    def all_approvals(self) -> list[PendingApproval]:
        """Every approval entry, pending or resolved. Useful for audit views."""
        store = self.approval_store()
        if store is None:
            return []
        return [PendingApproval.from_entry(e) for e in store.list_all()]

    def resolve_approval(
        self,
        approval_id: str,
        *,
        approve: bool,
        reviewer: str,
        reason: str = "",
    ) -> dict[str, Any]:
        """Apply a reviewer's decision on a pending DEFERRED promotion.

        Pipes the call through ``PromotionService.resolve_approval`` (records
        the audit row, flips the pending entry's status) and, when the
        outcome is APPROVED + a manifest store is configured, applies the
        stage transition to disk via ``apply_decision``.

        Returns a small dict the API endpoint can serialize directly:

            {
              "decision":          <to_dict() of the new APPROVED/REJECTED decision>,
              "applied_to_manifest": True | False,
              "agent_id":          str,
              "new_stage":         str | None,
            }

        Raises:
            RuntimeError: pending_approvals_path or promotion_log_path not configured
            KeyError:     approval_id not found
            ValueError:   approval already resolved
        """
        store = self.approval_store()
        audit = self._promotion_log()
        if store is None:
            raise RuntimeError(
                "PlatformData.resolve_approval requires pending_approvals_path "
                "in the config."
            )
        if audit is None:
            raise RuntimeError(
                "PlatformData.resolve_approval requires promotion_log_path "
                "in the config so the resolution decision is audited."
            )

        service = PromotionService(
            GateChecker(),
            audit_log=audit,
            approval_store=store,
        )
        new_decision = service.resolve_approval(
            approval_id,
            approve=approve,
            reviewer=reviewer,
            reason=reason,
        )

        applied = False
        new_stage: str | None = None
        if new_decision.approved:
            manifest_store = self.manifest_store()
            if manifest_store is not None:
                updated = apply_decision(new_decision, manifest_store)
                if updated is not None:
                    applied = True
                    new_stage = updated.lifecycle_stage.value

        return {
            "decision":             new_decision.to_dict(),
            "applied_to_manifest":  applied,
            "agent_id":             new_decision.request.agent_id,
            "new_stage":            new_stage,
        }

    def promotion_summary(self) -> dict[str, int]:
        decisions = self.list_promotions()
        return {
            "total":    len(decisions),
            "APPROVED": sum(1 for d in decisions if d.outcome == PromotionOutcome.APPROVED),
            "REJECTED": sum(1 for d in decisions if d.outcome == PromotionOutcome.REJECTED),
            "DEFERRED": sum(1 for d in decisions if d.outcome == PromotionOutcome.DEFERRED),
        }
