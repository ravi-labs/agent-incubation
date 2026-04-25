"""
Smoke + behavior tests for arc.harness — native after migration module 5.

Foundry continues to exercise the harness through the shim
(via the example agents in agent-foundry/examples/), so cross-coverage stays.
This file pins arc.harness's own behavior end-to-end without touching
foundry import paths.
"""

import pytest

from arc.harness import (
    DecisionReport,
    FixtureLoader,
    HarnessBuilder,
    SandboxApprover,
    ShadowAuditSink,
)
from foundry.tollgate.types import ApprovalOutcome


# ─── FixtureLoader ────────────────────────────────────────────────────────────

class TestFixtureLoader:
    def test_from_dict_round_trips_sources(self):
        loader = FixtureLoader.from_dict({"a": [1, 2], "b": {"k": "v"}})
        assert sorted(loader.source_names()) == ["a", "b"]
        assert loader.sources["a"] == [1, 2]

    def test_empty_loader_has_no_sources(self):
        loader = FixtureLoader.empty()
        assert loader.source_names() == []

    def test_add_appends_source(self):
        loader = FixtureLoader.empty()
        loader.add("inbox", [{"id": "e1"}])
        assert "inbox" in loader.source_names()

    def test_to_gateway_returns_mock_connector(self):
        loader = FixtureLoader.from_dict({"users": {"alice": {}}})
        gw = loader.to_gateway()
        # MockGatewayConnector keeps its data on _store
        assert "users" in gw._store  # type: ignore[attr-defined]


# ─── ShadowAuditSink ──────────────────────────────────────────────────────────

class TestShadowAuditSink:
    def test_starts_empty(self):
        sink = ShadowAuditSink()
        assert sink.total == 0
        assert sink.summary()["total"] == 0

    def test_summary_keys(self):
        sink = ShadowAuditSink()
        s = sink.summary()
        assert set(s.keys()) == {"total", "allow", "ask", "deny", "success", "errors"}


# ─── SandboxApprover ──────────────────────────────────────────────────────────

class TestSandboxApprover:
    @pytest.mark.asyncio
    async def test_always_approves(self):
        from types import SimpleNamespace
        approver = SandboxApprover()
        outcome = await approver.request_approval_async(
            agent_ctx=SimpleNamespace(),
            intent=SimpleNamespace(action="x"),
            tool_request=SimpleNamespace(
                resource_type="test.x",
                effect=SimpleNamespace(value="test.x"),
            ),
            request_hash="h",
            reason="r",
        )
        assert outcome == ApprovalOutcome.APPROVED
        assert approver.ask_count == 1
        assert approver.ask_log[0]["resource_type"] == "test.x"


# ─── DecisionReport ───────────────────────────────────────────────────────────

class TestDecisionReport:
    def test_to_dict_with_empty_audit(self):
        sink = ShadowAuditSink()
        approver = SandboxApprover()
        report = DecisionReport(audit=sink, approver=approver, agent_id="test-agent")
        d = report.to_dict()
        assert d["agent_id"] == "test-agent"
        assert d["events"] == []
        assert d["summary"]["total"] == 0

    def test_to_json_round_trips(self):
        import json
        sink = ShadowAuditSink()
        approver = SandboxApprover()
        report = DecisionReport(audit=sink, approver=approver, agent_id="agent-x")
        parsed = json.loads(report.to_json())
        assert parsed["agent_id"] == "agent-x"

    def test_to_html_includes_agent_id(self):
        sink = ShadowAuditSink()
        approver = SandboxApprover()
        report = DecisionReport(audit=sink, approver=approver, agent_id="reporter-1")
        html = report.to_html()
        assert "reporter-1" in html
        assert "<table>" in html


# ─── HarnessBuilder construction ──────────────────────────────────────────────

class TestHarnessBuilder:
    def test_fluent_chain_returns_self(self, tmp_path):
        # Builder doesn't validate paths until build() — check chaining
        b = (
            HarnessBuilder(manifest=tmp_path / "m.yaml", policy=tmp_path / "p.yaml")
            .with_fixture_dict({"x": []})
            .with_kwargs(extra=1)
        )
        assert isinstance(b, HarnessBuilder)
        assert b._extra_kwargs == {"extra": 1}

    def test_with_orchestrator_stores_value(self, tmp_path):
        sentinel = object()
        b = HarnessBuilder(manifest=tmp_path / "m.yaml", policy=tmp_path / "p.yaml")
        b.with_orchestrator(sentinel)
        assert b._orchestrator is sentinel
