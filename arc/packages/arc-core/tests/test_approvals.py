"""
Tests for arc.core.lifecycle.approvals + the resolve_approval flow on
PromotionService. Covers both store implementations and the end-to-end
DEFERRED → enqueue → resolve → audit handoff.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from arc.core import (
    GateChecker,
    InMemoryPendingApprovalStore,
    InMemoryPromotionAuditLog,
    JsonlPendingApprovalStore,
    LifecycleStage,
    PromotionDecision,
    PromotionOutcome,
    PromotionRequest,
    PromotionService,
)
from arc.core.lifecycle import APPROVED, PENDING, REJECTED


def _deferred_decision(agent_id: str = "test-agent") -> PromotionDecision:
    """Synthetic DEFERRED decision (skip going through PromotionService)."""
    return PromotionDecision(
        request=PromotionRequest(
            agent_id=agent_id,
            current_stage=LifecycleStage.GOVERN,
            target_stage=LifecycleStage.SCALE,
            requester="alice@team",
            justification="ROI signed off",
        ),
        outcome=PromotionOutcome.DEFERRED,
        gate_results=[],
        reason="awaiting human approval (require_human policy)",
    )


# ── InMemoryPendingApprovalStore ────────────────────────────────────────────


class TestInMemoryStore:
    def test_enqueue_returns_id_and_persists_pending(self):
        store = InMemoryPendingApprovalStore()
        approval_id = store.enqueue(_deferred_decision())

        assert isinstance(approval_id, str) and approval_id
        entry = store.get(approval_id)
        assert entry is not None
        assert entry.is_pending
        assert entry.status == PENDING
        assert entry.decision.request.agent_id == "test-agent"

    def test_list_pending_excludes_resolved(self):
        store = InMemoryPendingApprovalStore()
        a = store.enqueue(_deferred_decision("alpha"))
        b = store.enqueue(_deferred_decision("beta"))

        assert {e.approval_id for e in store.list_pending()} == {a, b}

        store.resolve(a, approved=True, reviewer="bob@team", reason="ok")
        pending = store.list_pending()
        assert {e.approval_id for e in pending} == {b}

    def test_resolve_approve_marks_approved(self):
        store = InMemoryPendingApprovalStore()
        approval_id = store.enqueue(_deferred_decision())

        entry = store.resolve(approval_id, approved=True, reviewer="bob@team", reason="ok")
        assert entry.status == APPROVED
        assert entry.resolved_by == "bob@team"
        assert entry.resolved_at != ""

    def test_resolve_reject_marks_rejected(self):
        store = InMemoryPendingApprovalStore()
        approval_id = store.enqueue(_deferred_decision())

        entry = store.resolve(approval_id, approved=False, reviewer="bob@team", reason="risk")
        assert entry.status == REJECTED
        assert entry.resolution_reason == "risk"

    def test_resolve_missing_raises_keyerror(self):
        store = InMemoryPendingApprovalStore()
        with pytest.raises(KeyError, match="ghost"):
            store.resolve("ghost", approved=True, reviewer="bob")

    def test_resolve_already_resolved_raises_valueerror(self):
        store = InMemoryPendingApprovalStore()
        approval_id = store.enqueue(_deferred_decision())
        store.resolve(approval_id, approved=True, reviewer="bob")
        with pytest.raises(ValueError, match="already resolved"):
            store.resolve(approval_id, approved=False, reviewer="alice")


# ── JsonlPendingApprovalStore ───────────────────────────────────────────────


class TestJsonlStore:
    def test_round_trip_through_disk(self, tmp_path: Path):
        path = tmp_path / "pending.jsonl"
        store = JsonlPendingApprovalStore(path)

        approval_id = store.enqueue(_deferred_decision())

        # Re-open: state survives.
        reopened = JsonlPendingApprovalStore(path)
        entry = reopened.get(approval_id)
        assert entry is not None
        assert entry.is_pending
        assert entry.decision.request.agent_id == "test-agent"

    def test_resolve_appends_new_line_and_latest_wins(self, tmp_path: Path):
        path = tmp_path / "pending.jsonl"
        store = JsonlPendingApprovalStore(path)
        approval_id = store.enqueue(_deferred_decision())

        # Resolve, then re-open and verify status flipped.
        store.resolve(approval_id, approved=True, reviewer="bob@team", reason="ok")
        reopened = JsonlPendingApprovalStore(path)
        entry = reopened.get(approval_id)
        assert entry is not None
        assert entry.status == APPROVED
        assert entry.resolved_by == "bob@team"

        # On disk: two lines (one append per state change).
        lines = [ln for ln in path.read_text().splitlines() if ln.strip()]
        assert len(lines) == 2

    def test_history_returns_all_states(self, tmp_path: Path):
        path = tmp_path / "pending.jsonl"
        store = JsonlPendingApprovalStore(path)
        approval_id = store.enqueue(_deferred_decision())
        store.resolve(approval_id, approved=True, reviewer="bob")

        history = store.list_history(approval_id)
        assert len(history) == 2
        assert history[0].status == PENDING    # initial
        assert history[1].status == APPROVED   # final

    def test_torn_lines_skipped(self, tmp_path: Path):
        path = tmp_path / "pending.jsonl"
        store = JsonlPendingApprovalStore(path)
        store.enqueue(_deferred_decision())

        # Simulate a partial write at the end of the file
        with path.open("a") as f:
            f.write('{"approval_id": "broken')

        # Reading still works; the broken line is skipped
        reopened = JsonlPendingApprovalStore(path)
        assert len(reopened.list_pending()) == 1

    def test_list_pending_separates_pending_from_resolved(self, tmp_path: Path):
        path = tmp_path / "pending.jsonl"
        store = JsonlPendingApprovalStore(path)
        a = store.enqueue(_deferred_decision("alpha"))
        b = store.enqueue(_deferred_decision("beta"))
        store.resolve(a, approved=True, reviewer="bob")

        # b is still pending; a moved to APPROVED
        pending = store.list_pending()
        assert {e.approval_id for e in pending} == {b}

        # list_all sees both
        all_entries = store.list_all()
        assert {e.approval_id for e in all_entries} == {a, b}


# ── PromotionService.resolve_approval (end-to-end handoff) ──────────────────


class TestPromotionServiceApprovalFlow:
    def _build_service_and_store(self):
        store = InMemoryPendingApprovalStore()
        audit = InMemoryPromotionAuditLog()
        service = PromotionService(
            GateChecker(),
            audit_log=audit,
            require_human={LifecycleStage.SCALE},
            approval_store=store,
        )
        return service, store, audit

    def test_promote_to_scale_enqueues_and_returns_deferred(self):
        service, store, audit = self._build_service_and_store()

        decision = service.promote(PromotionRequest(
            agent_id="alpha",
            current_stage=LifecycleStage.GOVERN,
            target_stage=LifecycleStage.SCALE,
            requester="alice@team",
            justification="ROI signed off",
        ))

        assert decision.deferred
        # One DEFERRED row in the audit log
        assert len(audit.history()) == 1
        # And one pending entry in the store
        pending = store.list_pending()
        assert len(pending) == 1
        assert pending[0].decision.request.agent_id == "alpha"

    def test_resolve_approval_approved_writes_audit_row(self):
        service, store, audit = self._build_service_and_store()
        service.promote(PromotionRequest(
            agent_id="alpha",
            current_stage=LifecycleStage.GOVERN,
            target_stage=LifecycleStage.SCALE,
            requester="alice@team",
            justification="ROI signed off",
        ))
        approval_id = store.list_pending()[0].approval_id

        new_decision = service.resolve_approval(
            approval_id,
            approve=True,
            reviewer="bob@compliance",
            reason="reviewed and approved",
        )

        assert new_decision.approved
        assert new_decision.decided_by == "bob@compliance"
        assert "approved" in new_decision.reason.lower()
        # Pending entry is now resolved
        assert store.get(approval_id).status == APPROVED
        # Audit log: original DEFERRED + new APPROVED
        assert len(audit.history()) == 2
        assert audit.history()[-1].outcome == PromotionOutcome.APPROVED

    def test_resolve_approval_rejected_records_rejection(self):
        service, store, audit = self._build_service_and_store()
        service.promote(PromotionRequest(
            agent_id="alpha",
            current_stage=LifecycleStage.GOVERN,
            target_stage=LifecycleStage.SCALE,
            requester="alice@team",
            justification="ROI signed off",
        ))
        approval_id = store.list_pending()[0].approval_id

        new_decision = service.resolve_approval(
            approval_id,
            approve=False,
            reviewer="bob@compliance",
            reason="risk too high",
        )

        assert new_decision.rejected
        assert "risk too high" in new_decision.reason
        assert store.get(approval_id).status == REJECTED
        assert audit.history()[-1].outcome == PromotionOutcome.REJECTED

    def test_resolve_approval_with_no_store_raises(self):
        # Service constructed without an approval_store
        service = PromotionService(GateChecker())
        with pytest.raises(RuntimeError, match="approval_store"):
            service.resolve_approval("anything", approve=True, reviewer="bob")

    def test_resolve_unknown_id_raises_keyerror(self):
        service, _, _ = self._build_service_and_store()
        with pytest.raises(KeyError, match="ghost"):
            service.resolve_approval("ghost", approve=True, reviewer="bob")

    def test_resolve_already_resolved_raises(self):
        service, store, _ = self._build_service_and_store()
        service.promote(PromotionRequest(
            agent_id="alpha",
            current_stage=LifecycleStage.GOVERN,
            target_stage=LifecycleStage.SCALE,
            requester="alice@team",
            justification="ROI signed off",
        ))
        approval_id = store.list_pending()[0].approval_id
        service.resolve_approval(approval_id, approve=True, reviewer="bob")

        with pytest.raises(ValueError, match="already resolved"):
            service.resolve_approval(approval_id, approve=False, reviewer="carol")

    def test_promote_without_store_skips_enqueue(self):
        # If require_human triggers but no approval_store is wired,
        # the decision still happens; nothing is enqueued.
        service = PromotionService(
            GateChecker(),
            require_human={LifecycleStage.SCALE},
        )
        decision = service.promote(PromotionRequest(
            agent_id="alpha",
            current_stage=LifecycleStage.GOVERN,
            target_stage=LifecycleStage.SCALE,
            requester="alice@team",
            justification="ROI signed off",
        ))
        assert decision.deferred  # still flagged as DEFERRED
