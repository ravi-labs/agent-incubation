"""Wiring tests — confirm telemetry is actually emitted from arc-core hot paths.

The telemetry emitters are unit-tested in ``test_telemetry.py``. This
file verifies that the *call sites* (BaseAgent.run_effect,
OutcomeTracker.record, Redactor._redact_string) actually invoke the
injected Telemetry. A NoOpTelemetry default keeps these wiring points
opt-in and zero-cost.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from arc.core.agent import BaseAgent
from arc.core.effects import FinancialEffect
from arc.core.lifecycle import LifecycleStage
from arc.core.manifest import AgentManifest, AgentStatus
from arc.core.observability import OutcomeTracker
from arc.core.redactor import Redactor


# ── Spy emitter ────────────────────────────────────────────────────────────────


class SpyTelemetry:
    """Records every call so tests can assert on emit-name + tags."""
    def __init__(self):
        self.calls: list[tuple] = []

    def count(self, name, value=1.0, tags=None):
        self.calls.append(("count", name, value, dict(tags or {})))

    def gauge(self, name, value, tags=None):
        self.calls.append(("gauge", name, value, dict(tags or {})))

    def timing(self, name, value_ms, tags=None):
        self.calls.append(("timing", name, value_ms, dict(tags or {})))

    def names(self) -> list[str]:
        return [c[1] for c in self.calls]


# ── Test fixtures (mirror test_base_agent) ─────────────────────────────────────


def _make_manifest() -> AgentManifest:
    return AgentManifest(
        agent_id="test-agent",
        version="0.1.0",
        owner="test-team",
        description="Test agent",
        lifecycle_stage=LifecycleStage.BUILD,
        allowed_effects=[FinancialEffect.RISK_SCORE_COMPUTE],
        data_access=["participant.data"],
        policy_path="tests/policy.yaml",
        success_metrics=["m1"],
        environment="sandbox",
        status=AgentStatus.ACTIVE,
    )


class _Concrete(BaseAgent):
    async def execute(self, **kwargs):
        return {"ok": True}


# ── 1. BaseAgent.run_effect emits effect.outcome + effect.latency_ms ───────────


class TestBaseAgentTelemetry:
    @pytest.mark.asyncio
    async def test_default_is_noop(self):
        """No telemetry kwarg → no emit calls (NoOp default)."""
        agent = _Concrete(
            manifest=_make_manifest(),
            tower=self._tower(ok=True),
            gateway=MagicMock(),
        )
        # NoOp doesn't track calls — just verify .telemetry isn't None and
        # that run_effect succeeds without error.
        assert agent.telemetry is not None
        await agent.run_effect(
            effect=FinancialEffect.RISK_SCORE_COMPUTE,
            tool="t", action="a", params={},
            intent_action="ia", intent_reason="ir",
        )

    @pytest.mark.asyncio
    async def test_allow_emits_outcome_and_latency(self):
        spy = SpyTelemetry()
        agent = _Concrete(
            manifest=_make_manifest(),
            tower=self._tower(ok=True),
            gateway=MagicMock(),
            telemetry=spy,
        )

        await agent.run_effect(
            effect=FinancialEffect.RISK_SCORE_COMPUTE,
            tool="scorer", action="compute", params={},
            intent_action="score", intent_reason="r",
        )

        # One outcome counter and one latency timing emitted.
        outcome_calls = [c for c in spy.calls if c[1] == "arc.effect.outcome"]
        latency_calls = [c for c in spy.calls if c[1] == "arc.effect.latency_ms"]
        assert len(outcome_calls) == 1
        assert len(latency_calls) == 1

        kind, name, value, tags = outcome_calls[0]
        assert kind == "count"
        assert value == 1.0
        assert tags["agent_id"]  == "test-agent"
        assert tags["effect"]    == FinancialEffect.RISK_SCORE_COMPUTE.value
        assert tags["decision"]  == "ALLOW"

        # Latency value is a non-negative float in milliseconds.
        _, _, ms, ltags = latency_calls[0]
        assert isinstance(ms, float) and ms >= 0
        assert ltags["agent_id"] == "test-agent"

    @pytest.mark.asyncio
    async def test_denied_effect_emits_deny(self):
        # Simulate Tollgate denying the call.
        class FakeTollgateDenied(Exception):
            pass
        FakeTollgateDenied.__name__ = "TollgateDenied"

        spy = SpyTelemetry()
        agent = _Concrete(
            manifest=_make_manifest(),
            tower=self._tower(ok=False, raise_exc=FakeTollgateDenied("policy")),
            gateway=MagicMock(),
            telemetry=spy,
        )

        with pytest.raises(FakeTollgateDenied):
            await agent.run_effect(
                effect=FinancialEffect.RISK_SCORE_COMPUTE,
                tool="t", action="a", params={},
                intent_action="ia", intent_reason="ir",
            )

        outcome_calls = [c for c in spy.calls if c[1] == "arc.effect.outcome"]
        assert len(outcome_calls) == 1
        assert outcome_calls[0][3]["decision"] == "DENY"

    @pytest.mark.asyncio
    async def test_telemetry_failure_does_not_break_run_effect(self):
        """Even if the emitter raises, the agent must succeed."""
        class Broken:
            def count(self, *a, **k): raise RuntimeError("metric backend down")
            def gauge(self, *a, **k): raise RuntimeError("metric backend down")
            def timing(self, *a, **k): raise RuntimeError("metric backend down")

        agent = _Concrete(
            manifest=_make_manifest(),
            tower=self._tower(ok=True),
            gateway=MagicMock(),
            telemetry=Broken(),
        )
        # Should not raise — the agent's primary job is more important
        # than its telemetry.
        result = await agent.run_effect(
            effect=FinancialEffect.RISK_SCORE_COMPUTE,
            tool="t", action="a", params={},
            intent_action="ia", intent_reason="ir",
        )
        assert result == {"result": "ok"}

    # ── helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def _tower(*, ok: bool, raise_exc: Exception | None = None):
        tower = MagicMock()
        if ok:
            tower.execute_async = AsyncMock(return_value={"result": "ok"})
        else:
            tower.execute_async = AsyncMock(side_effect=raise_exc)
        return tower


# ── 2. OutcomeTracker.record emits outcome.event ───────────────────────────────


class TestOutcomeTrackerTelemetry:
    @pytest.mark.asyncio
    async def test_default_no_telemetry(self, tmp_path):
        """No telemetry kwarg → no emit on record()."""
        tracker = OutcomeTracker(path=tmp_path / "outcomes.jsonl")
        await tracker.record(agent_id="a", event_type="ok", data={})
        # Nothing to assert — just no crash.

    @pytest.mark.asyncio
    async def test_record_emits_event_counter(self, tmp_path):
        spy = SpyTelemetry()
        tracker = OutcomeTracker(
            path=tmp_path / "outcomes.jsonl",
            telemetry=spy,
        )
        await tracker.record(
            agent_id="email-triage",
            event_type="ticket_created",
            data={"latency_ms": 42},
        )
        # outcome.event counter + outcome.latency_ms timing.
        names = spy.names()
        assert "arc.outcome.event"      in names
        assert "arc.outcome.latency_ms" in names

        outcome = next(c for c in spy.calls if c[1] == "arc.outcome.event")
        assert outcome[3] == {"agent_id": "email-triage", "event_type": "ticket_created"}

        latency = next(c for c in spy.calls if c[1] == "arc.outcome.latency_ms")
        assert latency[2] == 42.0

    @pytest.mark.asyncio
    async def test_record_without_latency_skips_latency_metric(self, tmp_path):
        spy = SpyTelemetry()
        tracker = OutcomeTracker(
            path=tmp_path / "outcomes.jsonl",
            telemetry=spy,
        )
        await tracker.record(agent_id="a", event_type="ok", data={"any": "thing"})
        assert "arc.outcome.latency_ms" not in spy.names()

    @pytest.mark.asyncio
    async def test_telemetry_failure_does_not_break_record(self, tmp_path):
        class Broken:
            def count(self, *a, **k): raise RuntimeError("down")
            def gauge(self, *a, **k): raise RuntimeError("down")
            def timing(self, *a, **k): raise RuntimeError("down")
        tracker = OutcomeTracker(
            path=tmp_path / "outcomes.jsonl",
            telemetry=Broken(),
        )
        # Must persist + return event despite the broken emitter.
        ev = await tracker.record(agent_id="a", event_type="ok", data={})
        assert ev.event_type == "ok"


# ── 3. Redactor emits arc.redaction.match per-pattern ──────────────────────────


class TestRedactorTelemetry:
    def test_default_no_emit(self):
        r = Redactor()
        r.redact_text("My SSN is 123-45-6789, alice@example.com")
        # No telemetry → nothing to assert beyond no crash.

    def test_match_emits_per_pattern_count(self):
        spy = SpyTelemetry()
        r = Redactor(telemetry=spy)
        r.redact_text("My SSN is 123-45-6789 — also 999-88-7777, mail alice@example.com")

        # Two SSN matches in one string → one count call with value 2.
        ssn_calls = [c for c in spy.calls if c[3].get("pattern") == "SSN"]
        assert len(ssn_calls) == 1
        assert ssn_calls[0][1] == "arc.redaction.match"
        assert ssn_calls[0][2] == 2.0

        email_calls = [c for c in spy.calls if c[3].get("pattern") == "EMAIL"]
        assert len(email_calls) == 1
        assert email_calls[0][2] == 1.0

    def test_no_match_no_emit(self):
        spy = SpyTelemetry()
        r = Redactor(telemetry=spy)
        r.redact_text("Customer requests rollover; balance approximately $4,500")
        assert spy.calls == []

    def test_telemetry_failure_does_not_break_redaction(self):
        class Broken:
            def count(self, *a, **k): raise RuntimeError("down")
            def gauge(self, *a, **k): raise RuntimeError("down")
            def timing(self, *a, **k): raise RuntimeError("down")
        r = Redactor(telemetry=Broken())
        # Must still redact correctly despite the broken emitter.
        out = r.redact_text("ssn 123-45-6789")
        assert "[REDACTED-SSN]" in out
