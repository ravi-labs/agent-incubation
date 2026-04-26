"""End-to-end tests for the DemotionWatcher.

Covers the orchestration logic that ties SLOs, the breach state store,
the audit log (cooldown), the kill switch, and either the approval
queue (proposed mode) or PromotionService.demote (auto mode) together.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from arc.core import (
    AgentManifest,
    DemotionMode,
    DemotionWatcher,
    DirectoryManifestStore,
    GateChecker,
    InMemoryBreachStateStore,
    InMemoryPendingApprovalStore,
    InMemoryPromotionAuditLog,
    KILL_SWITCH_ENV,
    LifecycleStage,
    OutcomeTracker,
    PromotionService,
    SLOConfig,
    SLORule,
    apply_decision,
)
from arc.core.effects import FinancialEffect


# ── Helpers ─────────────────────────────────────────────────────────────────


def _manifest(
    *,
    agent_id: str,
    stage: LifecycleStage = LifecycleStage.SCALE,
    rules: list[SLORule] | None = None,
    mode: DemotionMode = DemotionMode.PROPOSED,
    min_volume: int = 10,
    window: str = "1h",
) -> AgentManifest:
    return AgentManifest(
        agent_id=agent_id,
        version="0.1.0",
        owner="team",
        description="test agent",
        lifecycle_stage=stage,
        allowed_effects=[FinancialEffect.RISK_SCORE_COMPUTE],
        data_access=[],
        policy_path="x.yaml",
        success_metrics=["test"],
        slo=SLOConfig(
            window=window,
            min_volume=min_volume,
            rules=rules or [SLORule(metric="error_rate", op="<", threshold=0.05)],
            demotion_mode=mode,
        ),
    )


def _seed_outcomes(path: Path, agent_id: str, events: list[dict]) -> None:
    """Append events to an outcomes JSONL file. Each dict is the OutcomeEvent shape."""
    path.parent.mkdir(parents=True, exist_ok=True)
    now_iso = datetime.now(timezone.utc).isoformat()
    with path.open("a", encoding="utf-8") as f:
        for e in events:
            line = {
                "agent_id":   agent_id,
                "event_type": e.get("event_type", "run"),
                "data":       e.get("data", {}),
                "timestamp":  e.get("timestamp", now_iso),
                "session_id": None,
            }
            f.write(json.dumps(line) + "\n")


def _make_watcher(
    *,
    tmp_path: Path,
    agent_count: int = 1,
    mode: DemotionMode = DemotionMode.PROPOSED,
    consecutive: int = 3,
    cooldown_hours: int = 24,
    stage: LifecycleStage = LifecycleStage.SCALE,
    rules: list[SLORule] | None = None,
) -> tuple[DemotionWatcher, dict[str, AgentManifest], DirectoryManifestStore,
           OutcomeTracker, InMemoryPromotionAuditLog,
           InMemoryPendingApprovalStore, InMemoryBreachStateStore]:
    """Wire up a watcher with all in-memory dependencies for fast tests."""
    registry_root = tmp_path / "registry"
    outcomes_path = tmp_path / "outcomes.jsonl"
    store = DirectoryManifestStore(registry_root)
    tracker = OutcomeTracker(path=outcomes_path)
    audit = InMemoryPromotionAuditLog()
    approvals = InMemoryPendingApprovalStore()
    breaches = InMemoryBreachStateStore()
    service = PromotionService(
        checker=GateChecker(),
        audit_log=audit,
        approval_store=approvals,
    )

    manifests: dict[str, AgentManifest] = {}
    for i in range(agent_count):
        m = _manifest(
            agent_id=f"agent-{i}",
            stage=stage,
            mode=mode,
            rules=rules,
        )
        store.save(m)
        manifests[m.agent_id] = m

    watcher = DemotionWatcher(
        manifest_store    = store,
        outcome_tracker   = tracker,
        breach_state      = breaches,
        promotion_service = service,
        approval_store    = approvals,
        consecutive_breaches_required = consecutive,
        cooldown_hours    = cooldown_hours,
    )
    return watcher, manifests, store, tracker, audit, approvals, breaches


# ── Skip paths ──────────────────────────────────────────────────────────────


class TestSkipPaths:
    def test_no_slo_block_skips(self, tmp_path: Path):
        """An agent without a slo: block must never be touched."""
        watcher, manifests, store, *_ = _make_watcher(tmp_path=tmp_path)
        m = manifests["agent-0"]
        m.slo = None
        store.save(m)

        results = watcher.run(["agent-0"])
        assert results[0].action == "skipped:no-slo"

    def test_pre_scale_stage_skipped(self, tmp_path: Path):
        """BUILD-stage agents aren't watched — only VALIDATE/GOVERN/SCALE."""
        watcher, *_ = _make_watcher(tmp_path=tmp_path, stage=LifecycleStage.BUILD)
        results = watcher.run(["agent-0"])
        assert results[0].action == "skipped:not-eligible"

    def test_below_min_volume_skipped(self, tmp_path: Path):
        watcher, _, _, tracker, audit, *_ = _make_watcher(tmp_path=tmp_path)
        # min_volume defaults to 10; only emit 5 events.
        _seed_outcomes(tracker.path, "agent-0",
                       [{"data": {"status": "ok"}}] * 5)
        results = watcher.run(["agent-0"])
        assert results[0].action == "skipped:no-data"
        # Below-volume runs must NOT touch the breach counter.
        assert results[0].consecutive_breaches == 0


# ── Healthy path ────────────────────────────────────────────────────────────


class TestHealthyPath:
    def test_no_breach_returns_ok(self, tmp_path: Path):
        watcher, _, _, tracker, *_ = _make_watcher(tmp_path=tmp_path)
        _seed_outcomes(tracker.path, "agent-0",
                       [{"data": {"status": "ok"}}] * 100)
        results = watcher.run(["agent-0"])
        assert results[0].action == "ok"

    def test_pass_after_breach_resets_counter(self, tmp_path: Path):
        watcher, _, _, tracker, *_ = _make_watcher(tmp_path=tmp_path)

        # First run: half errors → breach.
        _seed_outcomes(tracker.path, "agent-0",
                       [{"data": {"status": "error"}}] * 5
                     + [{"data": {"status": "ok"}}] * 5)
        r1 = watcher.run(["agent-0"])[0]
        assert r1.action == "breach-pending"
        assert r1.consecutive_breaches == 1

        # Add 100 healthy events on top — error_rate now well under 5%.
        _seed_outcomes(tracker.path, "agent-0",
                       [{"data": {"status": "ok"}}] * 100)
        r2 = watcher.run(["agent-0"])[0]
        assert r2.action == "ok"
        assert r2.consecutive_breaches == 0


# ── Hysteresis ──────────────────────────────────────────────────────────────


class TestHysteresis:
    def test_single_breach_does_not_fire(self, tmp_path: Path):
        watcher, *_, tracker, audit, approvals, _ = _make_watcher(
            tmp_path=tmp_path, consecutive=3,
        )
        _seed_outcomes(tracker.path, "agent-0",
                       [{"data": {"status": "error"}}] * 50)
        r = watcher.run(["agent-0"])[0]
        assert r.action == "breach-pending"
        assert r.consecutive_breaches == 1
        assert approvals.list_pending() == []

    def test_three_consecutive_breaches_fire_proposed(self, tmp_path: Path):
        watcher, _, _, tracker, audit, approvals, _ = _make_watcher(
            tmp_path=tmp_path, consecutive=3, mode=DemotionMode.PROPOSED,
        )
        _seed_outcomes(tracker.path, "agent-0",
                       [{"data": {"status": "error"}}] * 50)

        actions = []
        for _ in range(3):
            r = watcher.run(["agent-0"])[0]
            actions.append(r.action)
        assert actions == ["breach-pending", "breach-pending", "proposed"]

        # PendingApproval enqueued with kind=demotion
        pending = approvals.list_pending()
        assert len(pending) == 1
        assert pending[0].decision.request.kind == "demotion"
        assert pending[0].decision.request.target_stage == LifecycleStage.GOVERN
        assert pending[0].decision.request.current_stage == LifecycleStage.SCALE


# ── Modes ───────────────────────────────────────────────────────────────────


class TestModes:
    def test_auto_mode_calls_demote_directly(self, tmp_path: Path):
        watcher, _, _, tracker, audit, approvals, breaches = _make_watcher(
            tmp_path=tmp_path, consecutive=2, mode=DemotionMode.AUTO,
        )
        _seed_outcomes(tracker.path, "agent-0",
                       [{"data": {"status": "error"}}] * 50)

        results = []
        for _ in range(2):
            results.append(watcher.run(["agent-0"])[0])
        assert results[0].action == "breach-pending"
        assert results[1].action == "demoted"
        # Approval queue stays empty in auto mode.
        assert approvals.list_pending() == []
        # Audit log has the demotion APPROVED entry.
        history = audit.history(agent_id="agent-0")
        assert any(d.approved and "demotion" in d.reason for d in history)
        # Breach counter is reset post-demotion so the new stage starts fresh.
        assert breaches.get("agent-0").consecutive_breaches == 0

    def test_auto_mode_apply_decision_writes_back_to_manifest(self, tmp_path: Path):
        """The CLI's --apply path: AUTO + apply_decision flips the manifest stage."""
        watcher, _, store, tracker, *_ = _make_watcher(
            tmp_path=tmp_path, consecutive=1, mode=DemotionMode.AUTO,
        )
        _seed_outcomes(tracker.path, "agent-0",
                       [{"data": {"status": "error"}}] * 50)
        r = watcher.run(["agent-0"])[0]
        assert r.action == "demoted"
        assert r.decision is not None
        apply_decision(r.decision, store)
        reloaded = store.load("agent-0")
        assert reloaded.lifecycle_stage == LifecycleStage.GOVERN

    def test_proposed_mode_does_not_touch_manifest_stage(self, tmp_path: Path):
        """Until a human resolves the PendingApproval, the stage stays put."""
        watcher, _, store, tracker, *_ = _make_watcher(
            tmp_path=tmp_path, consecutive=1, mode=DemotionMode.PROPOSED,
        )
        _seed_outcomes(tracker.path, "agent-0",
                       [{"data": {"status": "error"}}] * 50)
        watcher.run(["agent-0"])
        assert store.load("agent-0").lifecycle_stage == LifecycleStage.SCALE


# ── Cooldown ────────────────────────────────────────────────────────────────


class TestCooldown:
    def test_recent_demotion_blocks_another(self, tmp_path: Path):
        watcher, _, _, tracker, audit, *_ = _make_watcher(
            tmp_path=tmp_path, consecutive=1, mode=DemotionMode.AUTO,
            cooldown_hours=24,
        )
        # Pre-load an APPROVED audit entry "just now" — same shape the
        # service would have written.
        from arc.core.lifecycle.pipeline import (
            PromotionDecision, PromotionOutcome, PromotionRequest,
        )
        recent = PromotionDecision(
            request=PromotionRequest(
                agent_id="agent-0",
                current_stage=LifecycleStage.GOVERN,
                target_stage=LifecycleStage.SCALE,
                requester="alice",
                justification="initial promotion",
            ),
            outcome=PromotionOutcome.APPROVED,
            gate_results=[],
            reason="all gates passed",
        )
        audit.record(recent)

        # Now seed errors and run — should be cooldown'd, not demoted.
        _seed_outcomes(tracker.path, "agent-0",
                       [{"data": {"status": "error"}}] * 50)
        r = watcher.run(["agent-0"])[0]
        assert r.action == "cooldown"

    def test_cooldown_expires(self, tmp_path: Path):
        watcher, _, _, tracker, audit, *_ = _make_watcher(
            tmp_path=tmp_path, consecutive=1, mode=DemotionMode.AUTO,
            cooldown_hours=1,
        )
        # Audit entry from 3 hours ago — outside the 1h cooldown.
        from arc.core.lifecycle.pipeline import (
            PromotionDecision, PromotionOutcome, PromotionRequest,
        )
        old = PromotionDecision(
            request=PromotionRequest(
                agent_id="agent-0",
                current_stage=LifecycleStage.GOVERN,
                target_stage=LifecycleStage.SCALE,
                requester="alice",
                justification="initial promotion",
            ),
            outcome=PromotionOutcome.APPROVED,
            gate_results=[],
            reason="all gates passed",
            decided_at=(datetime.now(timezone.utc) - timedelta(hours=3)).isoformat(),
        )
        audit.record(old)

        _seed_outcomes(tracker.path, "agent-0",
                       [{"data": {"status": "error"}}] * 50)
        r = watcher.run(["agent-0"])[0]
        assert r.action == "demoted"


# ── Kill switch ─────────────────────────────────────────────────────────────


class TestKillSwitch:
    def test_kill_switch_skips_everything(self, tmp_path: Path,
                                          monkeypatch: pytest.MonkeyPatch):
        watcher, _, _, tracker, *_ = _make_watcher(tmp_path=tmp_path)
        _seed_outcomes(tracker.path, "agent-0",
                       [{"data": {"status": "error"}}] * 50)
        monkeypatch.setenv(KILL_SWITCH_ENV, "1")
        results = watcher.run(["agent-0"])
        assert results[0].action == "skipped:disabled"

    def test_kill_switch_off_runs_normally(self, tmp_path: Path,
                                           monkeypatch: pytest.MonkeyPatch):
        watcher, *_ = _make_watcher(tmp_path=tmp_path)
        monkeypatch.setenv(KILL_SWITCH_ENV, "0")
        # No outcomes seeded → skipped:no-data, NOT skipped:disabled.
        results = watcher.run(["agent-0"])
        assert results[0].action.startswith("skipped:")
        assert results[0].action != "skipped:disabled"


# ── One-stage drop / GOVERN handling ────────────────────────────────────────


class TestStageDropping:
    def test_govern_demotes_to_validate(self, tmp_path: Path):
        watcher, _, _, tracker, *_ = _make_watcher(
            tmp_path=tmp_path, consecutive=1, mode=DemotionMode.AUTO,
            stage=LifecycleStage.GOVERN,
        )
        _seed_outcomes(tracker.path, "agent-0",
                       [{"data": {"status": "error"}}] * 50)
        r = watcher.run(["agent-0"])[0]
        assert r.action == "demoted"
        assert r.decision.request.target_stage == LifecycleStage.VALIDATE


# ── Robustness ──────────────────────────────────────────────────────────────


class TestRobustness:
    def test_one_bad_agent_does_not_break_pass(self, tmp_path: Path):
        """A FileNotFoundError on one agent_id shouldn't stop the others."""
        watcher, _, _, tracker, *_ = _make_watcher(tmp_path=tmp_path)
        _seed_outcomes(tracker.path, "agent-0",
                       [{"data": {"status": "ok"}}] * 100)
        # 'ghost' has no manifest in the store.
        results = watcher.run(["agent-0", "ghost"])
        actions = {r.agent_id: r.action for r in results}
        assert actions["agent-0"] == "ok"
        assert actions["ghost"] == "error"
