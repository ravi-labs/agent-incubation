"""
Smoke tests for the standalone tollgate package.

These verify the package imports cleanly and the core enforcement pipeline
works end-to-end without requiring any optional backends.
"""

from __future__ import annotations

import pytest

from tollgate import (
    AutoApprover,
    ControlTower,
    JsonlAuditSink,
    YamlPolicyEvaluator,
)
from tollgate.types import (
    AgentContext,
    ApprovalOutcome,
    AuditEvent,
    Decision,
    DecisionType,
    Effect,
    Intent,
    ToolRequest,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_tower(tmp_path, policy_yaml: str) -> ControlTower:
    policy_file = tmp_path / "policy.yaml"
    policy_file.write_text(policy_yaml)
    return ControlTower(
        policy=YamlPolicyEvaluator(str(policy_file)),
        approver=AutoApprover(),
        audit=JsonlAuditSink(str(tmp_path / "audit.jsonl")),
    )


def _make_ctx() -> AgentContext:
    return AgentContext(
        agent_id="smoke-test-agent",
        version="0.1.0",
        owner="test-team",
    )


def _make_request(resource_type: str) -> ToolRequest:
    return ToolRequest(
        tool="test_tool",
        action="test_action",
        resource_type=resource_type,
        effect=Effect.READ,
        params={},
        metadata={"resource_type": resource_type},
    )


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestImports:
    def test_core_imports(self):
        """All core exports must be importable from the top-level package."""
        from tollgate import (
            AnomalyDetector,
            AsyncQueueApprover,
            AuditSink,
            AutoApprover,
            CliApprover,
            CompositeAuditSink,
            ControlTower,
            InMemoryApprovalStore,
            InMemoryGrantStore,
            JsonlAuditSink,
            RateLimiter,
            TollgateDenied,
            TollgateError,
            ToolRegistry,
            YamlPolicyEvaluator,
        )
        assert ControlTower is not None


class TestAllowDecision:
    @pytest.mark.asyncio
    async def test_allow_runs_exec_fn(self, tmp_path):
        tower = _make_tower(tmp_path, """
rules:
  - resource_type: "data.read"
    decision: ALLOW
    reason: "Always allow reads"
""")

        async def exec_fn():
            return {"data": "ok"}

        result = await tower.execute_async(
            agent_ctx=_make_ctx(),
            intent=Intent(action="read", reason="test"),
            tool_request=_make_request("data.read"),
            exec_async=exec_fn,
        )
        assert result == {"data": "ok"}


class TestDenyDecision:
    @pytest.mark.asyncio
    async def test_deny_raises_tollgate_denied(self, tmp_path):
        from tollgate import TollgateDenied

        tower = _make_tower(tmp_path, """
rules:
  - resource_type: "data.write"
    decision: DENY
    reason: "Never allow writes"
""")

        async def exec_fn():
            return {"written": True}

        req = ToolRequest(
            tool="db",
            action="write",
            resource_type="data.write",
            effect=Effect.WRITE,
            params={},
            metadata={"resource_type": "data.write"},
        )

        with pytest.raises((TollgateDenied, PermissionError, Exception)):
            await tower.execute_async(
                agent_ctx=_make_ctx(),
                intent=Intent(action="write", reason="test"),
                tool_request=req,
                exec_async=exec_fn,
            )


class TestAuditLog:
    @pytest.mark.asyncio
    async def test_audit_written_after_allow(self, tmp_path):
        import json

        audit_path = tmp_path / "audit.jsonl"
        tower = _make_tower(tmp_path, """
rules:
  - resource_type: "data.read"
    decision: ALLOW
    reason: "Always allow reads"
""")

        async def exec_fn():
            return {}

        await tower.execute_async(
            agent_ctx=_make_ctx(),
            intent=Intent(action="read", reason="test"),
            tool_request=_make_request("data.read"),
            exec_async=exec_fn,
        )

        events = [json.loads(line) for line in audit_path.read_text().splitlines() if line.strip()]
        assert len(events) >= 1
        assert any("smoke-test-agent" in json.dumps(e) for e in events)
