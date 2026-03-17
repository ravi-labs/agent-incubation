"""
Integration tests — full Foundry pipeline.

Unlike unit tests (which mock the ControlTower), these tests wire up the real
stack end-to-end:

    agent.run_effect()
        → BaseAgent effect guard (kill-switch + effect declaration check)
        → ControlTower.execute_async()
            → YamlPolicyEvaluator reads tests/integration/fixtures/policy.yaml
            → Approver (AllowAllApprover or AutoApprover depending on test)
            → JsonlAuditSink writes to a tmp file
        → exec_fn() runs (if provided)

What we verify:
  1. ALLOW effects run exec_fn and return its result
  2. ASK effects are handled by AllowAllApprover (approved)
  3. ASK effects are rejected by AutoApprover (denied), raising PermissionError
  4. DENY effects raise PermissionError without running exec_fn
  5. Undeclared effects raise PermissionError before reaching ControlTower
  6. Suspended / deprecated agents are blocked at the kill switch
  7. Audit log is written for every decision (ALLOW, ASK-approved, DENY)
  8. Multi-step agent that chains several effects produces correct output
  9. OutcomeTracker accumulates events across the full run
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import pytest


# ── async helper ──────────────────────────────────────────────────────────────

def areturn(value: Any):
    """
    Wrap a plain value in an async callable — the ControlTower awaits exec_fn.

    Usage:
        exec_fn=areturn({"score": 0.78})
    """
    async def _fn():
        return value
    return _fn

from foundry.gateway import MockGatewayConnector
from foundry.observability import OutcomeTracker
from foundry.policy.effects import FinancialEffect
from foundry.scaffold import BaseAgent, load_manifest
from foundry.scaffold.manifest import AgentStatus
from foundry.tollgate import (
    AutoApprover,
    ControlTower,
    JsonlAuditSink,
    YamlPolicyEvaluator,
)
from foundry.tollgate.types import AgentContext, ApprovalOutcome, Intent, ToolRequest

# ── Paths ──────────────────────────────────────────────────────────────────────

FIXTURES = Path(__file__).parent / "fixtures"
POLICY_PATH   = FIXTURES / "policy.yaml"
MANIFEST_PATH = FIXTURES / "manifest.yaml"


# ── Test approvers ────────────────────────────────────────────────────────────


class AllowAllApprover:
    """Test approver that approves every ASK decision — simulates human approval."""

    async def request_approval_async(
        self,
        _agent_ctx: AgentContext,
        _intent: Intent,
        _tool_request: ToolRequest,
        _request_hash: str,
        _reason: str,
    ) -> ApprovalOutcome:
        return ApprovalOutcome.APPROVED


class DenyAllApprover:
    """Test approver that denies every ASK decision — simulates human rejection."""

    async def request_approval_async(
        self,
        _agent_ctx: AgentContext,
        _intent: Intent,
        _tool_request: ToolRequest,
        _request_hash: str,
        _reason: str,
    ) -> ApprovalOutcome:
        return ApprovalOutcome.DENIED


# ── Helpers ───────────────────────────────────────────────────────────────────


def load_audit_events(path: Path) -> list[dict]:
    """Read all JSON-lines from an audit log file."""
    if not path.exists():
        return []
    events = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            events.append(json.loads(line))
    return events


def make_stack(
    *,
    audit_path: Path,
    gateway_data: dict | None = None,
    status: AgentStatus = AgentStatus.ACTIVE,
    approver=None,
) -> tuple[Any, "ControlTower", "MockGatewayConnector", "OutcomeTracker"]:
    """Wire up a real Foundry stack with the integration test manifest + policy."""
    manifest = load_manifest(MANIFEST_PATH)
    manifest.status = status

    policy = YamlPolicyEvaluator(POLICY_PATH)
    _approver = approver if approver is not None else AllowAllApprover()
    audit = JsonlAuditSink(str(audit_path))
    tower = ControlTower(policy=policy, approver=_approver, audit=audit)

    gateway = MockGatewayConnector(gateway_data or {})
    tracker = OutcomeTracker()

    return manifest, tower, gateway, tracker


# ── Concrete agents for testing ────────────────────────────────────────────────


class MultiStepAgent(BaseAgent):
    """
    Exercises a realistic four-step retirement intervention flow:

      1. Read participant data       (ALLOW)
      2. Compute risk score          (ALLOW)
      3. Draft intervention message  (ALLOW)
      4. Send communication          (ASK  → AllowAllApprover in sandbox)
    """

    async def execute(self, participant_id: str = "p-001", **kwargs) -> dict:
        participant = await self.run_effect(
            effect=FinancialEffect.PARTICIPANT_DATA_READ,
            tool="gateway", action="read",
            params={"participant_id": participant_id},
            intent_action="read_participant",
            intent_reason="Load participant retirement data for analysis",
            exec_fn=areturn({"id": participant_id, "balance": 45_000, "deferral_pct": 3}),
        )

        score_result = await self.run_effect(
            effect=FinancialEffect.RISK_SCORE_COMPUTE,
            tool="scorer", action="compute",
            params={"balance": participant["balance"], "deferral_pct": participant["deferral_pct"]},
            intent_action="compute_risk",
            intent_reason="Identify at-risk participants for proactive outreach",
            exec_fn=areturn({"score": 0.78, "at_risk": True}),
        )

        draft = await self.run_effect(
            effect=FinancialEffect.INTERVENTION_DRAFT,
            tool="generator", action="draft",
            params={"score": score_result["score"]},
            intent_action="draft_message",
            intent_reason="Create personalised intervention for at-risk participant",
            exec_fn=areturn({"subject": "Your retirement on track", "body": "Consider increasing your deferral."}),
        )

        send_result = await self.run_effect(
            effect=FinancialEffect.PARTICIPANT_COMMUNICATION_SEND,
            tool="email_gateway", action="send",
            params={"participant_id": participant_id, "content": draft},
            intent_action="send_intervention",
            intent_reason="Deliver personalised retirement intervention message",
            exec_fn=areturn({"sent": True, "message_id": "msg-001"}),
        )

        await self.log_outcome("intervention_complete", {
            "participant_id": participant_id,
            "risk_score": score_result["score"],
            "message_sent": send_result["sent"],
        })

        return {
            "participant_id": participant_id,
            "risk_score": score_result["score"],
            "at_risk": score_result["at_risk"],
            "draft": draft,
            "sent": send_result["sent"],
        }


class DeniedDataWriteAgent(BaseAgent):
    """Agent that attempts a DENY-listed effect (participant.data.write)."""

    async def execute(self, **kwargs) -> dict:
        return await self.run_effect(
            effect=FinancialEffect.PARTICIPANT_DATA_WRITE,
            tool="participant_db", action="write",
            params={"participant_id": "p-001", "change": "overwrite_balance"},
            intent_action="overwrite_participant",
            intent_reason="Attempt a DENY-listed operation",
            exec_fn=areturn({"written": True}),
        )


class DeniedTransactionAgent(BaseAgent):
    """Agent that attempts a DENY-listed effect (account.transaction.execute)."""

    async def execute(self, **kwargs) -> dict:
        return await self.run_effect(
            effect=FinancialEffect.ACCOUNT_TRANSACTION_EXECUTE,
            tool="transaction_service", action="execute",
            params={"amount": 1000, "participant_id": "p-001"},
            intent_action="execute_transaction",
            intent_reason="Attempt a DENY-listed transaction",
            exec_fn=areturn({"executed": True}),
        )


class UndeclaredEffectAgent(BaseAgent):
    """
    Agent that attempts an effect not listed in its manifest.
    AGENT_SUSPEND is not in the test manifest's allowed_effects.
    """

    async def execute(self, **kwargs) -> dict:
        return await self.run_effect(
            effect=FinancialEffect.AGENT_SUSPEND,
            tool="lifecycle", action="suspend",
            params={"target_agent": "some-agent"},
            intent_action="suspend_agent",
            intent_reason="Attempt an undeclared effect",
        )


# ── Test Suite ────────────────────────────────────────────────────────────────


class TestAllowEffects:
    """ALLOW-listed effects should run exec_fn and return its result."""

    @pytest.mark.asyncio
    async def test_allow_effect_returns_exec_fn_result(self, tmp_path):
        manifest, tower, gateway, tracker = make_stack(audit_path=tmp_path / "audit.jsonl")

        class SimpleAgent(BaseAgent):
            async def execute(self, **kwargs):
                return await self.run_effect(
                    effect=FinancialEffect.PARTICIPANT_DATA_READ,
                    tool="gateway", action="read",
                    params={"participant_id": "p-001"},
                    intent_action="read", intent_reason="test",
                    exec_fn=areturn({"id": "p-001", "balance": 50_000}),
                )

        agent = SimpleAgent(manifest=manifest, tower=tower, gateway=gateway, tracker=tracker)
        result = await agent.execute()
        assert result == {"id": "p-001", "balance": 50_000}

    @pytest.mark.asyncio
    async def test_allow_effect_writes_to_audit_log(self, tmp_path):
        audit_path = tmp_path / "audit.jsonl"
        manifest, tower, gateway, _ = make_stack(audit_path=audit_path)

        class SimpleAgent(BaseAgent):
            async def execute(self, **kwargs):
                return await self.run_effect(
                    effect=FinancialEffect.RISK_SCORE_COMPUTE,
                    tool="scorer", action="compute",
                    params={}, intent_action="compute", intent_reason="test",
                    exec_fn=areturn({"score": 0.5}),
                )

        agent = SimpleAgent(manifest=manifest, tower=tower, gateway=gateway)
        await agent.execute()

        events = load_audit_events(audit_path)
        assert len(events) >= 1, f"Expected ≥1 audit event; got {events}"

    @pytest.mark.asyncio
    async def test_multiple_allow_effects_all_succeed(self, tmp_path):
        manifest, tower, gateway, _ = make_stack(audit_path=tmp_path / "audit.jsonl")

        class ReadAgent(BaseAgent):
            async def execute(self, **kwargs):
                perf = await self.run_effect(
                    effect=FinancialEffect.FUND_PERFORMANCE_READ,
                    tool="fund_data", action="read",
                    params={}, intent_action="read", intent_reason="test",
                    exec_fn=areturn({"return_3yr": 7.5}),
                )
                fees = await self.run_effect(
                    effect=FinancialEffect.FUND_FEES_READ,
                    tool="fund_data", action="fees",
                    params={}, intent_action="read", intent_reason="test",
                    exec_fn=areturn({"expense_ratio": 0.45}),
                )
                return {"perf": perf, "fees": fees}

        agent = ReadAgent(manifest=manifest, tower=tower, gateway=gateway)
        result = await agent.execute()
        assert result["perf"]["return_3yr"] == 7.5
        assert result["fees"]["expense_ratio"] == 0.45


class TestAskEffects:
    """ASK effects depend on the approver — approved or denied accordingly."""

    @pytest.mark.asyncio
    async def test_ask_approved_by_all_allow_approver(self, tmp_path):
        manifest, tower, gateway, _ = make_stack(
            audit_path=tmp_path / "audit.jsonl",
            approver=AllowAllApprover(),
        )

        class CommAgent(BaseAgent):
            async def execute(self, **kwargs):
                return await self.run_effect(
                    effect=FinancialEffect.PARTICIPANT_COMMUNICATION_SEND,
                    tool="email", action="send",
                    params={"participant_id": "p-001"},
                    intent_action="send", intent_reason="outreach",
                    exec_fn=areturn({"sent": True}),
                )

        agent = CommAgent(manifest=manifest, tower=tower, gateway=gateway)
        result = await agent.execute()
        assert result["sent"] is True

    @pytest.mark.asyncio
    async def test_ask_denied_by_deny_all_approver_raises(self, tmp_path):
        manifest, tower, gateway, _ = make_stack(
            audit_path=tmp_path / "audit.jsonl",
            approver=DenyAllApprover(),
        )

        class CommAgent(BaseAgent):
            async def execute(self, **kwargs):
                return await self.run_effect(
                    effect=FinancialEffect.PARTICIPANT_COMMUNICATION_SEND,
                    tool="email", action="send",
                    params={"participant_id": "p-001"},
                    intent_action="send", intent_reason="outreach",
                    exec_fn=areturn({"sent": True}),
                )

        agent = CommAgent(manifest=manifest, tower=tower, gateway=gateway)
        with pytest.raises(Exception):  # TollgateApprovalDenied or PermissionError
            await agent.execute()

    @pytest.mark.asyncio
    async def test_high_finding_ask_approved_runs_exec_fn(self, tmp_path):
        manifest, tower, gateway, _ = make_stack(
            audit_path=tmp_path / "audit.jsonl",
            approver=AllowAllApprover(),
        )

        class HighFindingAgent(BaseAgent):
            async def execute(self, **kwargs):
                return await self.run_effect(
                    effect=FinancialEffect.COMPLIANCE_FINDING_EMIT_HIGH,
                    tool="dashboard", action="emit",
                    params={"finding": "excessive-fees", "severity": "HIGH"},
                    intent_action="emit_high_finding", intent_reason="Post-review emission",
                    exec_fn=areturn({"emitted": True, "review_completed": True}),
                )

        agent = HighFindingAgent(manifest=manifest, tower=tower, gateway=gateway)
        result = await agent.execute()
        assert result["emitted"] is True


class TestDenyEffects:
    """DENY-listed effects must raise an exception without running exec_fn."""

    @pytest.mark.asyncio
    async def test_participant_data_write_is_denied(self, tmp_path):
        manifest, tower, gateway, _ = make_stack(audit_path=tmp_path / "audit.jsonl")
        exec_called = []

        async def tracked_exec():
            exec_called.append(True)
            return {"written": True}

        class WriteAgent(BaseAgent):
            async def execute(self, **kwargs):
                return await self.run_effect(
                    effect=FinancialEffect.PARTICIPANT_DATA_WRITE,
                    tool="participant_db", action="write",
                    params={"participant_id": "p-001"},
                    intent_action="write_participant", intent_reason="test",
                    exec_fn=tracked_exec,
                )

        agent = WriteAgent(manifest=manifest, tower=tower, gateway=gateway)
        with pytest.raises(Exception):
            await agent.execute()

        assert not exec_called, "exec_fn must NOT be called for DENY decisions"

    @pytest.mark.asyncio
    async def test_transaction_execute_is_denied(self, tmp_path):
        manifest, tower, gateway, _ = make_stack(audit_path=tmp_path / "audit.jsonl")
        agent = DeniedTransactionAgent(manifest=manifest, tower=tower, gateway=gateway)

        with pytest.raises(Exception):
            await agent.execute()

    @pytest.mark.asyncio
    async def test_deny_decision_written_to_audit(self, tmp_path):
        """DENY decisions must be recorded — absence from the audit log is a compliance gap."""
        audit_path = tmp_path / "audit.jsonl"
        manifest, tower, gateway, _ = make_stack(audit_path=audit_path)
        agent = DeniedDataWriteAgent(manifest=manifest, tower=tower, gateway=gateway)

        with pytest.raises(Exception):
            await agent.execute()

        events = load_audit_events(audit_path)
        assert events, "DENY decisions must be written to the audit log for compliance traceability"


class TestKillSwitch:
    """Suspended and deprecated agents must be blocked immediately."""

    @pytest.mark.asyncio
    async def test_suspended_agent_is_blocked(self, tmp_path):
        manifest, tower, gateway, _ = make_stack(
            audit_path=tmp_path / "audit.jsonl",
            status=AgentStatus.SUSPENDED,
        )

        class SomeAgent(BaseAgent):
            async def execute(self, **kwargs):
                return await self.run_effect(
                    effect=FinancialEffect.PARTICIPANT_DATA_READ,
                    tool="gateway", action="read",
                    params={}, intent_action="read", intent_reason="test",
                )

        agent = SomeAgent(manifest=manifest, tower=tower, gateway=gateway)
        with pytest.raises(PermissionError, match="suspended"):
            await agent.execute()

    @pytest.mark.asyncio
    async def test_deprecated_agent_is_blocked(self, tmp_path):
        manifest, tower, gateway, _ = make_stack(
            audit_path=tmp_path / "audit.jsonl",
            status=AgentStatus.DEPRECATED,
        )

        class SomeAgent(BaseAgent):
            async def execute(self, **kwargs):
                return await self.run_effect(
                    effect=FinancialEffect.PARTICIPANT_DATA_READ,
                    tool="gateway", action="read",
                    params={}, intent_action="read", intent_reason="test",
                )

        agent = SomeAgent(manifest=manifest, tower=tower, gateway=gateway)
        with pytest.raises(PermissionError, match="deprecated"):
            await agent.execute()


class TestUndeclaredEffect:
    """Effects not listed in the manifest must be rejected before ControlTower."""

    @pytest.mark.asyncio
    async def test_undeclared_effect_raises_before_policy_check(self, tmp_path):
        manifest, tower, gateway, _ = make_stack(audit_path=tmp_path / "audit.jsonl")

        agent = UndeclaredEffectAgent(manifest=manifest, tower=tower, gateway=gateway)
        with pytest.raises(PermissionError, match="undeclared effect"):
            await agent.execute()


class TestMultiStepPipeline:
    """Full multi-step agent run: data read → compute → draft → send → audit."""

    @pytest.mark.asyncio
    async def test_multi_step_produces_correct_result(self, tmp_path):
        manifest, tower, gateway, tracker = make_stack(audit_path=tmp_path / "audit.jsonl")
        agent = MultiStepAgent(manifest=manifest, tower=tower, gateway=gateway, tracker=tracker)

        result = await agent.execute(participant_id="p-001")

        assert result["participant_id"] == "p-001"
        assert result["risk_score"] == 0.78
        assert result["at_risk"] is True
        assert result["sent"] is True

    @pytest.mark.asyncio
    async def test_multi_step_writes_audit_events(self, tmp_path):
        audit_path = tmp_path / "audit.jsonl"
        manifest, tower, gateway, tracker = make_stack(audit_path=audit_path)
        agent = MultiStepAgent(manifest=manifest, tower=tower, gateway=gateway, tracker=tracker)

        await agent.execute(participant_id="p-002")

        events = load_audit_events(audit_path)
        # Four effects → at least four audit events
        assert len(events) >= 4, f"Expected ≥4 audit events for 4-step agent; got {len(events)}"

    @pytest.mark.asyncio
    async def test_multi_step_records_outcomes(self, tmp_path):
        manifest, tower, gateway, tracker = make_stack(audit_path=tmp_path / "audit.jsonl")
        agent = MultiStepAgent(manifest=manifest, tower=tower, gateway=gateway, tracker=tracker)

        await agent.execute(participant_id="p-003")

        outcomes = tracker.events(event_type="intervention_complete")
        assert len(outcomes) == 1
        assert outcomes[0].data["participant_id"] == "p-003"
        assert outcomes[0].data["risk_score"] == 0.78
        assert outcomes[0].data["message_sent"] is True

    @pytest.mark.asyncio
    async def test_multi_step_runs_in_batch(self, tmp_path):
        """The same agent instance should handle concurrent participant runs."""
        import asyncio

        manifest, tower, gateway, tracker = make_stack(audit_path=tmp_path / "audit.jsonl")
        agent = MultiStepAgent(manifest=manifest, tower=tower, gateway=gateway, tracker=tracker)

        results = await asyncio.gather(
            agent.execute(participant_id="p-001"),
            agent.execute(participant_id="p-002"),
            agent.execute(participant_id="p-003"),
        )

        assert len(results) == 3
        assert all(r["sent"] for r in results)

        outcomes = tracker.events(event_type="intervention_complete")
        assert len(outcomes) == 3
        participant_ids = {o.data["participant_id"] for o in outcomes}
        assert participant_ids == {"p-001", "p-002", "p-003"}


class TestAuditLogIntegrity:
    """The audit log must capture all decisions with agent context."""

    @pytest.mark.asyncio
    async def test_audit_log_contains_agent_id(self, tmp_path):
        audit_path = tmp_path / "audit.jsonl"
        manifest, tower, gateway, _ = make_stack(audit_path=audit_path)

        class SimpleAgent(BaseAgent):
            async def execute(self, **kwargs):
                return await self.run_effect(
                    effect=FinancialEffect.PARTICIPANT_DATA_READ,
                    tool="gateway", action="read",
                    params={}, intent_action="read", intent_reason="test",
                    exec_fn=areturn({}),
                )

        agent = SimpleAgent(manifest=manifest, tower=tower, gateway=gateway)
        await agent.execute()

        events = load_audit_events(audit_path)
        assert events, "Audit log should not be empty after an ALLOW effect"

        for event in events:
            event_str = json.dumps(event)
            assert "integration-test-agent" in event_str, (
                f"Expected agent_id in audit event: {event}"
            )

    @pytest.mark.asyncio
    async def test_deny_decision_is_audited(self, tmp_path):
        """DENY blocks must be recorded for compliance traceability."""
        audit_path = tmp_path / "audit.jsonl"
        manifest, tower, gateway, _ = make_stack(audit_path=audit_path)
        agent = DeniedDataWriteAgent(manifest=manifest, tower=tower, gateway=gateway)

        with pytest.raises(Exception):
            await agent.execute()

        events = load_audit_events(audit_path)
        assert events, "DENY decisions must appear in the audit log"


class TestRealPolicyYaml:
    """Verify the real policy YAML rules are correctly applied end-to-end."""

    @pytest.mark.asyncio
    async def test_low_finding_emit_is_allow(self, tmp_path):
        manifest, tower, gateway, _ = make_stack(audit_path=tmp_path / "audit.jsonl")

        class LowFindingAgent(BaseAgent):
            async def execute(self, **kwargs):
                return await self.run_effect(
                    effect=FinancialEffect.COMPLIANCE_FINDING_EMIT_LOW,
                    tool="dashboard", action="emit",
                    params={"finding": "low-fee-variance"},
                    intent_action="emit_finding", intent_reason="Low severity",
                    exec_fn=areturn({"emitted": True}),
                )

        agent = LowFindingAgent(manifest=manifest, tower=tower, gateway=gateway)
        result = await agent.execute()
        assert result["emitted"] is True

    @pytest.mark.asyncio
    async def test_high_finding_emit_is_ask_approved(self, tmp_path):
        """compliance.finding.emit.high → ASK → AllowAllApprover approves."""
        manifest, tower, gateway, _ = make_stack(
            audit_path=tmp_path / "audit.jsonl",
            approver=AllowAllApprover(),
        )

        class HighFindingAgent(BaseAgent):
            async def execute(self, **kwargs):
                return await self.run_effect(
                    effect=FinancialEffect.COMPLIANCE_FINDING_EMIT_HIGH,
                    tool="dashboard", action="emit",
                    params={"finding": "excessive-fees"},
                    intent_action="emit_high_finding", intent_reason="High severity",
                    exec_fn=areturn({"emitted": True}),
                )

        agent = HighFindingAgent(manifest=manifest, tower=tower, gateway=gateway)
        result = await agent.execute()
        assert result["emitted"] is True

    @pytest.mark.asyncio
    async def test_full_compliance_workflow(self, tmp_path):
        """
        Simulates a realistic per-fund compliance scan:
          1. read fund performance   (ALLOW)
          2. read fund fees          (ALLOW)
          3. draft finding           (ALLOW)
          4. add to human review     (ALLOW)
          5. emit high-severity      (ASK → AllowAllApprover)
          6. write finding log       (ALLOW)
        """
        manifest, tower, gateway, tracker = make_stack(audit_path=tmp_path / "audit.jsonl")

        class ComplianceAgent(BaseAgent):
            async def execute(self, fund_id: str = "FCNTX", **kwargs) -> dict:
                perf = await self.run_effect(
                    effect=FinancialEffect.FUND_PERFORMANCE_READ,
                    tool="fund_data", action="read",
                    params={"fund_id": fund_id},
                    intent_action="read_performance", intent_reason="Evaluate fund",
                    exec_fn=areturn({"return_3yr": 4.2, "benchmark_return_3yr": 8.5}),
                )

                fees = await self.run_effect(
                    effect=FinancialEffect.FUND_FEES_READ,
                    tool="fund_data", action="fees",
                    params={"fund_id": fund_id},
                    intent_action="read_fees", intent_reason="Compare expense ratio",
                    exec_fn=areturn({"expense_ratio": 1.25, "category_avg": 0.70}),
                )

                finding = await self.run_effect(
                    effect=FinancialEffect.FINDING_DRAFT,
                    tool="compliance_engine", action="draft",
                    params={"perf": perf, "fees": fees, "fund_id": fund_id},
                    intent_action="draft_finding", intent_reason="Document compliance issue",
                    exec_fn=areturn({
                        "severity": "HIGH",
                        "finding_type": "excessive_fees",
                        "detail": f"Fund {fund_id} expense ratio 1.25% vs 0.70% avg",
                    }),
                )

                await self.run_effect(
                    effect=FinancialEffect.HUMAN_REVIEW_QUEUE_ADD,
                    tool="review_queue", action="add",
                    params={**finding, "fund_id": fund_id},
                    intent_action="queue_for_review", intent_reason="High severity requires review",
                    exec_fn=areturn({"queued": True}),
                )

                await self.run_effect(
                    effect=FinancialEffect.COMPLIANCE_FINDING_EMIT_HIGH,
                    tool="dashboard", action="emit",
                    params=finding,
                    intent_action="emit_finding", intent_reason="Post-review emission",
                    exec_fn=areturn({"emitted": True}),
                )

                await self.run_effect(
                    effect=FinancialEffect.FINDING_LOG_WRITE,
                    tool="finding_store", action="write",
                    params={**finding, "fund_id": fund_id},
                    intent_action="log_finding", intent_reason="ERISA §107 audit retention",
                    exec_fn=areturn({"logged": True}),
                )

                await self.log_outcome("compliance_scan_complete", {
                    "fund_id": fund_id,
                    "severity": finding["severity"],
                })

                return finding

        agent = ComplianceAgent(manifest=manifest, tower=tower, gateway=gateway, tracker=tracker)
        result = await agent.execute(fund_id="FCNTX")

        assert result["severity"] == "HIGH"
        assert result["finding_type"] == "excessive_fees"

        outcomes = tracker.events(event_type="compliance_scan_complete")
        assert len(outcomes) == 1
        assert outcomes[0].data["fund_id"] == "FCNTX"

        audit_events = load_audit_events(tmp_path / "audit.jsonl")
        assert len(audit_events) >= 6, (
            f"Expected ≥6 audit events for 6-step compliance agent; got {len(audit_events)}"
        )
