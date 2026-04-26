"""Tests for arc.platform.common.data — the data layer both dashboards depend on."""

from __future__ import annotations

from pathlib import Path

from arc.platform.common import PlatformData, PlatformDataConfig


# ── Manifests ───────────────────────────────────────────────────────────────


class TestAgentLoading:
    def test_list_agents_returns_all_three_fixtures(self, platform_data: PlatformData):
        agents = platform_data.list_agents()
        ids = {a.agent_id for a in agents}
        assert ids == {"alpha", "beta", "gamma"}

    def test_get_agent_returns_summary(self, platform_data: PlatformData):
        a = platform_data.get_agent("alpha")
        assert a is not None
        assert a.agent_id == "alpha"
        assert a.lifecycle_stage == "BUILD"
        assert "participant.data.read" in a.allowed_effects

    def test_get_agent_missing_returns_none(self, platform_data: PlatformData):
        assert platform_data.get_agent("ghost") is None

    def test_agents_by_stage_groups_correctly(self, platform_data: PlatformData):
        groups = platform_data.agents_by_stage()
        assert [a.agent_id for a in groups["BUILD"]]    == ["alpha"]
        assert [a.agent_id for a in groups["VALIDATE"]] == ["beta"]
        assert [a.agent_id for a in groups["SCALE"]]    == ["gamma"]
        # Stages with no agents are present but empty
        assert groups["DISCOVER"] == []


# ── Audit (runtime tool-call decisions) ─────────────────────────────────────


class TestAuditEvents:
    def test_list_returns_all_three_with_newest_first(self, platform_data: PlatformData):
        events = platform_data.list_audit_events()
        # Three rows total
        assert len(events) == 3
        # Reversed → newest first (10:02 then 10:01 then 10:00)
        assert events[0].decision == "DENY"
        assert events[-1].decision == "ALLOW"

    def test_filter_by_agent_id(self, platform_data: PlatformData):
        only_alpha = platform_data.list_audit_events(agent_id="alpha")
        assert len(only_alpha) == 2
        assert all(e.agent_id == "alpha" for e in only_alpha)

    def test_limit_caps_results(self, platform_data: PlatformData):
        capped = platform_data.list_audit_events(limit=1)
        assert len(capped) == 1

    def test_summary_counts_decisions(self, platform_data: PlatformData):
        s = platform_data.audit_summary()
        assert s["total"] == 3
        assert s["ALLOW"] == 1
        assert s["ASK"]   == 1
        assert s["DENY"]  == 1


# ── Promotions + approvals ─────────────────────────────────────────────────


class TestPromotions:
    def test_list_promotions_returns_both(self, platform_data: PlatformData):
        decisions = platform_data.list_promotions()
        assert len(decisions) == 2

    def test_summary_counts_outcomes(self, platform_data: PlatformData):
        s = platform_data.promotion_summary()
        assert s["total"]    == 2
        assert s["APPROVED"] == 1
        assert s["DEFERRED"] == 1
        assert s["REJECTED"] == 0

    def test_pending_approvals_only_returns_deferred(self, platform_data: PlatformData):
        pending = platform_data.pending_approvals()
        assert len(pending) == 1
        assert pending[0].agent_id      == "gamma"
        assert pending[0].current_stage == "GOVERN"
        assert pending[0].target_stage  == "SCALE"


# ── Empty / cold environment ───────────────────────────────────────────────


class TestEmptyEnvironment:
    def test_unset_paths_yield_empty_results(self, tmp_path: Path):
        """A platform with no manifests / no audit / no promotions stays viewable."""
        # Point at a directory that exists but is empty
        (tmp_path / "empty").mkdir()
        config = PlatformDataConfig(
            manifest_root=tmp_path / "empty",
            audit_log_path=tmp_path / "missing-audit.jsonl",
            promotion_log_path=tmp_path / "missing-promotions.jsonl",
        )
        data = PlatformData(config)

        assert data.list_agents() == []
        assert data.list_audit_events() == []
        assert data.audit_summary() == {"total": 0, "ALLOW": 0, "ASK": 0, "DENY": 0}
        assert data.list_promotions() == []
        assert data.pending_approvals() == []

    def test_none_paths_yield_empty_results(self):
        """All-None config still produces a viewable (empty) dashboard."""
        data = PlatformData(PlatformDataConfig())
        assert data.list_agents() == []
        assert data.list_audit_events() == []
        assert data.list_promotions() == []
