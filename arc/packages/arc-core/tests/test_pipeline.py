"""
Tests for arc.core.lifecycle.pipeline — the promotion service, gates, and
audit log primitives. Native arc-core feature added as part of Phase 3.
"""

import json
from pathlib import Path

import pytest

from arc.core.lifecycle import (
    GateChecker,
    GateCheckResult,
    InMemoryPromotionAuditLog,
    JsonlPromotionAuditLog,
    LifecycleStage,
    PromotionDecision,
    PromotionOutcome,
    PromotionRequest,
    PromotionService,
    artifact_exists_check,
    evidence_field_check,
    predicate_check,
    reviewer_present_check,
    stage_order_check,
)


# ─── PromotionRequest ─────────────────────────────────────────────────────────


class TestPromotionRequest:
    def test_is_demotion_detects_backwards_move(self):
        req = PromotionRequest(
            agent_id="x", current_stage=LifecycleStage.SCALE,
            target_stage=LifecycleStage.GOVERN,
            requester="r", justification="rollback",
        )
        assert req.is_demotion is True

    def test_is_demotion_false_for_forward_move(self):
        req = PromotionRequest(
            agent_id="x", current_stage=LifecycleStage.BUILD,
            target_stage=LifecycleStage.VALIDATE,
            requester="r", justification="advance",
        )
        assert req.is_demotion is False

    def test_requested_at_auto_populated(self):
        req = PromotionRequest(
            agent_id="x", current_stage=LifecycleStage.BUILD,
            target_stage=LifecycleStage.VALIDATE,
            requester="r", justification="j",
        )
        assert req.requested_at  # ISO timestamp string


# ─── Gate primitives ──────────────────────────────────────────────────────────


def _req(target=LifecycleStage.VALIDATE, current=LifecycleStage.BUILD, **evidence):
    return PromotionRequest(
        agent_id="agent-x",
        current_stage=current,
        target_stage=target,
        requester="user@team",
        justification="test",
        evidence=evidence,
    )


class TestStageOrderCheck:
    def test_passes_when_target_is_next(self):
        chk = stage_order_check()
        result = chk(_req(target=LifecycleStage.VALIDATE, current=LifecycleStage.BUILD))
        assert result.passed
        assert result.name == "stage_order"

    def test_fails_when_target_skips_a_stage(self):
        chk = stage_order_check()
        result = chk(_req(target=LifecycleStage.SCALE, current=LifecycleStage.BUILD))
        assert not result.passed
        assert "not the next stage" in result.reason


class TestEvidenceFieldCheck:
    def test_passes_when_field_present(self):
        chk = evidence_field_check("test_results")
        assert chk(_req(test_results="OK")).passed

    def test_fails_when_field_missing(self):
        chk = evidence_field_check("test_results")
        result = chk(_req())
        assert not result.passed
        assert "test_results" in result.reason

    def test_fails_when_field_empty_string(self):
        chk = evidence_field_check("test_results")
        assert not chk(_req(test_results="")).passed

    def test_custom_label_used_in_result(self):
        chk = evidence_field_check("x", label="my-custom-name")
        assert chk(_req()).name == "my-custom-name"


class TestArtifactExistsCheck:
    def test_passes_for_existing_path(self, tmp_path):
        f = tmp_path / "report.txt"
        f.write_text("ok")
        chk = artifact_exists_check("report_path")
        assert chk(_req(report_path=str(f))).passed

    def test_fails_for_missing_path(self, tmp_path):
        chk = artifact_exists_check("report_path")
        result = chk(_req(report_path=str(tmp_path / "nope.txt")))
        assert not result.passed
        assert "does not exist" in result.reason

    def test_fails_when_evidence_field_missing(self):
        chk = artifact_exists_check("report_path")
        assert not chk(_req()).passed


class TestReviewerPresentCheck:
    def test_passes_when_reviewer_provided(self):
        chk = reviewer_present_check()
        assert chk(_req(reviewer="alice@team")).passed

    def test_fails_when_reviewer_missing(self):
        chk = reviewer_present_check()
        result = chk(_req())
        assert not result.passed


class TestPredicateCheck:
    def test_passes_when_predicate_returns_true(self):
        chk = predicate_check("custom", lambda req: req.agent_id.startswith("agent-"))
        assert chk(_req()).passed

    def test_fails_when_predicate_returns_false(self):
        chk = predicate_check(
            "custom", lambda req: False, fail_reason="always fail",
        )
        result = chk(_req())
        assert not result.passed
        assert result.reason == "always fail"


# ─── GateChecker ──────────────────────────────────────────────────────────────


class TestGateChecker:
    def test_register_returns_self_for_chaining(self):
        c = GateChecker()
        assert c.register(LifecycleStage.VALIDATE, stage_order_check()) is c

    def test_evaluate_runs_all_registered_checks(self):
        c = GateChecker()
        c.register(LifecycleStage.VALIDATE, stage_order_check())
        c.register(LifecycleStage.VALIDATE, evidence_field_check("x"))
        results = c.evaluate(_req(x="ok"))
        assert len(results) == 2

    def test_evaluate_returns_empty_for_unregistered_stage(self):
        c = GateChecker()
        assert c.evaluate(_req()) == []

    def test_checks_for_returns_registered_list(self):
        c = GateChecker()
        c.register(LifecycleStage.SCALE, stage_order_check())
        assert len(c.checks_for(LifecycleStage.SCALE)) == 1
        assert c.checks_for(LifecycleStage.BUILD) == []


# ─── PromotionService ─────────────────────────────────────────────────────────


class TestPromotionService:
    def _service(self, **kwargs):
        c = GateChecker()
        c.register(LifecycleStage.VALIDATE, stage_order_check())
        c.register(LifecycleStage.VALIDATE, evidence_field_check("test_results"))
        return PromotionService(c, **kwargs)

    def test_approves_when_all_gates_pass(self):
        s = self._service()
        d = s.promote(_req(test_results="OK"))
        assert d.approved
        assert d.outcome == PromotionOutcome.APPROVED
        assert all(g.passed for g in d.gate_results)

    def test_rejects_when_any_gate_fails(self):
        s = self._service()
        d = s.promote(_req())  # missing test_results
        assert d.rejected
        assert "test_results" in d.reason or "evidence" in d.reason

    def test_failed_gates_property_filters(self):
        s = self._service()
        d = s.promote(_req())
        assert len(d.failed_gates) == 1
        assert len(d.passed_gates) == 1

    def test_defers_when_target_in_require_human(self):
        c = GateChecker()
        # Register a check for SCALE that always passes
        c.register(LifecycleStage.SCALE, predicate_check("ok", lambda r: True))
        s = PromotionService(c, require_human={LifecycleStage.SCALE})
        d = s.promote(_req(target=LifecycleStage.SCALE, current=LifecycleStage.GOVERN))
        assert d.deferred
        assert "human approval" in d.reason

    def test_decision_records_decided_by(self):
        s = self._service()
        d = s.promote(_req(test_results="OK"), decided_by="alice@team")
        assert d.decided_by == "alice@team"

    def test_demote_records_decision_without_gates(self):
        s = self._service()
        d = s.demote(
            agent_id="agent-x",
            from_stage=LifecycleStage.SCALE,
            to_stage=LifecycleStage.GOVERN,
            requester="oncall",
            reason="latency anomaly",
        )
        assert d.approved
        assert d.gate_results == []
        assert "anomaly" in d.reason
        assert d.request.is_demotion

    def test_audit_log_accumulates_decisions(self):
        s = self._service()
        s.promote(_req(test_results="OK"))
        s.promote(_req(test_results="OK"))
        assert len(s.audit_log.history()) == 2


# ─── Audit log implementations ────────────────────────────────────────────────


class TestInMemoryAuditLog:
    def test_history_filters_by_agent_id(self):
        log = InMemoryPromotionAuditLog()
        d1 = PromotionDecision(
            request=PromotionRequest(
                agent_id="a", current_stage=LifecycleStage.BUILD,
                target_stage=LifecycleStage.VALIDATE,
                requester="r", justification="j",
            ),
            outcome=PromotionOutcome.APPROVED, gate_results=[],
        )
        d2 = PromotionDecision(
            request=PromotionRequest(
                agent_id="b", current_stage=LifecycleStage.BUILD,
                target_stage=LifecycleStage.VALIDATE,
                requester="r", justification="j",
            ),
            outcome=PromotionOutcome.REJECTED, gate_results=[],
        )
        log.record(d1)
        log.record(d2)
        assert len(log.history()) == 2
        assert len(log.history(agent_id="a")) == 1
        assert len(log.history(agent_id="b")) == 1
        assert len(log.history(agent_id="c")) == 0


class TestJsonlAuditLog:
    def test_round_trip_via_file(self, tmp_path):
        log_path = tmp_path / "promotions.jsonl"
        log = JsonlPromotionAuditLog(log_path)

        c = GateChecker()
        c.register(LifecycleStage.VALIDATE, stage_order_check())
        s = PromotionService(c, audit_log=log)

        s.promote(_req(test_results="OK"), decided_by="alice")
        s.promote(_req(test_results="OK"), decided_by="bob")

        # File contains 2 newline-delimited JSON objects
        assert log_path.exists()
        lines = log_path.read_text().strip().split("\n")
        assert len(lines) == 2

        # Each line is valid JSON with the expected keys
        for line in lines:
            d = json.loads(line)
            assert d["agent_id"] == "agent-x"
            assert d["target_stage"] == "VALIDATE"

        # Reload via a fresh log instance
        log2 = JsonlPromotionAuditLog(log_path)
        history = log2.history()
        assert len(history) == 2
        assert history[0].decided_by == "alice"
        assert history[1].decided_by == "bob"

    def test_creates_parent_dir_if_missing(self, tmp_path):
        log_path = tmp_path / "nested" / "deep" / "promotions.jsonl"
        log = JsonlPromotionAuditLog(log_path)
        d = PromotionDecision(
            request=PromotionRequest(
                agent_id="a", current_stage=LifecycleStage.BUILD,
                target_stage=LifecycleStage.VALIDATE,
                requester="r", justification="j",
            ),
            outcome=PromotionOutcome.APPROVED, gate_results=[],
        )
        log.record(d)
        assert log_path.exists()
