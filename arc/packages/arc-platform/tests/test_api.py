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
