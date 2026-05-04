"""Tests for arc.platform.api — exercises the FastAPI app via httpx.

Uses ``build_app(data)`` to inject a per-test PlatformData, so the
endpoints run against the conftest fixtures, not the monorepo's
actual state.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from arc.platform.api import build_app
from arc.platform.common import PlatformData


def _client(data: PlatformData) -> TestClient:
    return TestClient(build_app(data))


# ── Health ─────────────────────────────────────────────────────────────────


class TestHealth:
    def test_health_returns_ok(self, platform_data: PlatformData):
        r = _client(platform_data).get("/api/health")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}


# ── Agents ─────────────────────────────────────────────────────────────────


class TestAgentsEndpoint:
    def test_list_returns_three_agents(self, platform_data: PlatformData):
        r = _client(platform_data).get("/api/agents")
        assert r.status_code == 200
        body = r.json()
        assert {a["agent_id"] for a in body} == {"alpha", "beta", "gamma"}

    def test_get_existing(self, platform_data: PlatformData):
        r = _client(platform_data).get("/api/agents/alpha")
        assert r.status_code == 200
        assert r.json()["agent_id"] == "alpha"
        assert r.json()["lifecycle_stage"] == "BUILD"

    def test_get_missing_404(self, platform_data: PlatformData):
        r = _client(platform_data).get("/api/agents/ghost")
        assert r.status_code == 404
        assert "not found" in r.json()["detail"]

    def test_by_stage_groups_correctly(self, platform_data: PlatformData):
        r = _client(platform_data).get("/api/agents/by-stage")
        assert r.status_code == 200
        body = r.json()
        assert [a["agent_id"] for a in body["BUILD"]]    == ["alpha"]
        assert [a["agent_id"] for a in body["VALIDATE"]] == ["beta"]
        assert [a["agent_id"] for a in body["SCALE"]]    == ["gamma"]


# ── Audit ──────────────────────────────────────────────────────────────────


class TestAuditEndpoint:
    def test_list_returns_three_events(self, platform_data: PlatformData):
        r = _client(platform_data).get("/api/audit")
        assert r.status_code == 200
        assert len(r.json()) == 3

    def test_list_filtered_by_agent(self, platform_data: PlatformData):
        r = _client(platform_data).get("/api/audit?agent_id=alpha")
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 2
        assert all(e["agent_id"] == "alpha" for e in body)

    def test_list_respects_limit(self, platform_data: PlatformData):
        r = _client(platform_data).get("/api/audit?limit=1")
        assert r.status_code == 200
        assert len(r.json()) == 1

    def test_summary_counts(self, platform_data: PlatformData):
        r = _client(platform_data).get("/api/audit/summary")
        assert r.status_code == 200
        assert r.json() == {"total": 3, "ALLOW": 1, "ASK": 1, "DENY": 1}


# ── Promotions + approvals ─────────────────────────────────────────────────


class TestPromotionsEndpoint:
    def test_list(self, platform_data: PlatformData):
        r = _client(platform_data).get("/api/promotions")
        assert r.status_code == 200
        assert len(r.json()) == 2

    def test_summary(self, platform_data: PlatformData):
        r = _client(platform_data).get("/api/promotions/summary")
        assert r.status_code == 200
        body = r.json()
        assert body["total"]    == 2
        assert body["APPROVED"] == 1
        assert body["DEFERRED"] == 1
        assert body["REJECTED"] == 0


class TestApprovalsEndpoint:
    def test_pending_returns_only_deferred(self, platform_data: PlatformData):
        r = _client(platform_data).get("/api/approvals")
        assert r.status_code == 200
        body = r.json()
        assert len(body) == 1
        assert body[0]["agent_id"]      == "gamma"
        assert body[0]["target_stage"]  == "SCALE"
        assert body[0]["status"]        == "pending"
        assert body[0]["approval_id"]   != ""

    def test_all_includes_resolved(self, platform_data: PlatformData):
        client = _client(platform_data)
        approval_id = client.get("/api/approvals").json()[0]["approval_id"]

        # Approve it
        r = client.post(
            f"/api/approvals/{approval_id}/decide",
            json={"approve": True, "reviewer": "carol@compliance", "reason": "ROI verified"},
        )
        assert r.status_code == 200

        # /api/approvals (PENDING only) is now empty
        assert client.get("/api/approvals").json() == []

        # /api/approvals/all retains the resolved entry
        all_entries = client.get("/api/approvals/all").json()
        assert len(all_entries) == 1
        assert all_entries[0]["status"]      == "approved"
        assert all_entries[0]["resolved_by"] == "carol@compliance"


class TestDecideEndpoint:
    def test_approve_records_decision_and_applies_to_manifest(
        self, platform_data: PlatformData
    ):
        client = _client(platform_data)
        approval_id = client.get("/api/approvals").json()[0]["approval_id"]

        r = client.post(
            f"/api/approvals/{approval_id}/decide",
            json={"approve": True, "reviewer": "carol@compliance", "reason": "ok"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["agent_id"]              == "gamma"
        assert body["applied_to_manifest"]   is True
        assert body["new_stage"]             == "SCALE"
        # Decision dict carries the new outcome
        assert body["decision"]["outcome"]   == "approved"
        assert body["decision"]["decided_by"] == "carol@compliance"

        # Verify the manifest store actually flipped to SCALE
        agent = client.get("/api/agents/gamma").json()
        assert agent["lifecycle_stage"] == "SCALE"

    def test_reject_does_not_advance_stage(self, platform_data: PlatformData):
        client = _client(platform_data)
        before = client.get("/api/agents/gamma").json()
        approval_id = client.get("/api/approvals").json()[0]["approval_id"]

        r = client.post(
            f"/api/approvals/{approval_id}/decide",
            json={"approve": False, "reviewer": "carol@compliance", "reason": "risk"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["applied_to_manifest"] is False
        assert body["new_stage"]           is None
        assert body["decision"]["outcome"] == "rejected"

        # Manifest stage unchanged
        after = client.get("/api/agents/gamma").json()
        assert after["lifecycle_stage"] == before["lifecycle_stage"]

    def test_unknown_id_returns_404(self, platform_data: PlatformData):
        r = _client(platform_data).post(
            "/api/approvals/ghost-id/decide",
            json={"approve": True, "reviewer": "carol"},
        )
        assert r.status_code == 404

    def test_resolving_already_resolved_returns_409(self, platform_data: PlatformData):
        client = _client(platform_data)
        approval_id = client.get("/api/approvals").json()[0]["approval_id"]
        client.post(
            f"/api/approvals/{approval_id}/decide",
            json={"approve": True, "reviewer": "carol"},
        )

        # Second resolve fails with 409 Conflict
        r = client.post(
            f"/api/approvals/{approval_id}/decide",
            json={"approve": False, "reviewer": "dave"},
        )
        assert r.status_code == 409
        assert "already resolved" in r.json()["detail"].lower()

    def test_missing_reviewer_returns_422(self, platform_data: PlatformData):
        approval_id = _client(platform_data).get("/api/approvals").json()[0]["approval_id"]
        r = _client(platform_data).post(
            f"/api/approvals/{approval_id}/decide",
            json={"approve": True},  # reviewer missing
        )
        assert r.status_code == 422


# ── Suspend / Resume — kill switches via dashboard ─────────────────────────


class TestSuspendResume:
    def test_suspend_unknown_agent_404(self, platform_data: PlatformData):
        r = _client(platform_data).post(
            "/api/agents/ghost/suspend",
            json={"reviewer": "ops@", "reason": "test"},
        )
        assert r.status_code == 404

    def test_suspend_requires_reason(self, platform_data: PlatformData):
        # Pydantic min_length=1 → 422 before the data layer runs.
        r = _client(platform_data).post(
            "/api/agents/alpha/suspend",
            json={"reviewer": "ops@", "reason": ""},
        )
        assert r.status_code == 422

    def test_suspend_then_resume_audit_trail(self, platform_data: PlatformData):
        client = _client(platform_data)

        r1 = client.post(
            "/api/agents/alpha/suspend",
            json={"reviewer": "ops@", "reason": "incident-1234"},
        )
        assert r1.status_code == 200, r1.text
        body = r1.json()
        assert body["status"]   == "suspended"
        assert body["actor"]    == "ops@"
        assert body["reason"]   == "incident-1234"

        # Verify the manifest's status now says suspended
        r2 = client.get("/api/agents/alpha")
        assert r2.json()["status"] == "suspended"

        # Re-suspend → 409 (already suspended)
        r3 = client.post(
            "/api/agents/alpha/suspend",
            json={"reviewer": "ops@", "reason": "again"},
        )
        assert r3.status_code == 409

        # Resume → 200 + status flips back
        r4 = client.post(
            "/api/agents/alpha/resume",
            json={"reviewer": "ops@", "reason": "incident closed"},
        )
        assert r4.status_code == 200
        assert r4.json()["status"] == "active"

        # Re-resume → 409
        r5 = client.post(
            "/api/agents/alpha/resume",
            json={"reviewer": "ops@"},
        )
        assert r5.status_code == 409


# ── Stats endpoint ─────────────────────────────────────────────────────────


class TestStatsEndpoint:
    def test_stats_unknown_agent_404(self, platform_data: PlatformData):
        r = _client(platform_data).get("/api/agents/ghost/stats")
        assert r.status_code == 404

    def test_stats_returns_decisions_breakdown(self, platform_data: PlatformData):
        # Use a wide window so the 2026-04 fixture audit rows are included.
        # 1 month back = ~43,200 minutes.
        r = _client(platform_data).get("/api/agents/alpha/stats?window_minutes=43200")
        assert r.status_code == 200
        body = r.json()
        # alpha has 2 audit rows in conftest (1 ALLOW, 1 ASK)
        assert body["agent_id"] == "alpha"
        assert "decisions" in body
        assert set(body["decisions"]) == {"ALLOW", "ASK", "DENY"}
        assert body["total"] >= 1


# ── Corrections endpoints (feedback layer 1+2) ─────────────────────────────


class TestCorrectionsEndpoints:
    def _record_one(self, client: TestClient, agent: str = "alpha", **overrides) -> dict:
        body = {
            "audit_row_id":      "row-abc",
            "reviewer":          "alice@compliance",
            "severity":          "moderate",
            "reason":            "wrong case_type",
            "original_decision":  {"case_type": "loan_hardship"},
            "corrected_decision": {"case_type": "distribution"},
            **overrides,
        }
        r = client.post(f"/api/agents/{agent}/corrections", json=body)
        return r.json() | {"_status": r.status_code}

    def test_record_round_trip(self, platform_data: PlatformData):
        client = _client(platform_data)
        recorded = self._record_one(client)
        assert recorded["_status"] == 200
        assert recorded["correction_id"].startswith("corr-")

        # Fetch back
        rows = client.get("/api/agents/alpha/corrections").json()
        assert len(rows) == 1
        assert rows[0]["correction_id"] == recorded["correction_id"]
        assert rows[0]["original_decision"] == {"case_type": "loan_hardship"}

    def test_record_unknown_agent_404(self, platform_data: PlatformData):
        client = _client(platform_data)
        r = client.post(
            "/api/agents/ghost/corrections",
            json={
                "audit_row_id": "x", "reviewer": "r", "severity": "minor",
                "reason": "", "original_decision": {}, "corrected_decision": {},
            },
        )
        assert r.status_code == 404

    def test_record_bad_severity_422(self, platform_data: PlatformData):
        client = _client(platform_data)
        result = self._record_one(client, severity="extreme")
        assert result["_status"] == 422

    def test_record_anonymous_reviewer_422(self, platform_data: PlatformData):
        client = _client(platform_data)
        result = self._record_one(client, reviewer="")
        assert result["_status"] == 422

    def test_summary_returns_buckets(self, platform_data: PlatformData):
        client = _client(platform_data)
        for sev in ("minor", "moderate", "moderate", "critical"):
            self._record_one(client, severity=sev)
        s = client.get("/api/agents/alpha/corrections/summary").json()
        assert s["total"] == 4
        assert s["by_severity"]["moderate"] == 2
        assert s["by_severity"]["critical"] == 1
        assert s["by_severity"]["minor"]    == 1

    def test_filter_by_agent(self, platform_data: PlatformData):
        client = _client(platform_data)
        self._record_one(client, agent="alpha")
        self._record_one(client, agent="beta")
        self._record_one(client, agent="alpha")

        alpha_rows = client.get("/api/agents/alpha/corrections").json()
        beta_rows  = client.get("/api/agents/beta/corrections").json()
        assert len(alpha_rows) == 2
        assert len(beta_rows)  == 1
