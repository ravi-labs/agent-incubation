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
