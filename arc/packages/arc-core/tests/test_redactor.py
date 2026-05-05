"""Tests for arc.core.redactor.

Three layers:

  1. Pattern matching — every default pattern catches what it should
     and doesn't catch what it shouldn't.
  2. Recursive structure handling — strings, dicts, lists, dataclasses,
     mixed; sensitive keys redacted by name; structural fields preserved.
  3. RedactingAuditSink integration — wraps any sink, redacts before
     pass-through; preserves the inner sink's other methods.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import pytest

from arc.core.redactor import (
    BARE_SSN_PATTERN,
    DEFAULT_PATTERNS,
    Pattern,
    RedactingAuditSink,
    Redactor,
)


# ── 1. Pattern matching — defaults catch the right shapes ──────────────────


class TestDefaultPatterns:
    @pytest.fixture
    def r(self) -> Redactor:
        return Redactor()

    @pytest.mark.parametrize("text", [
        "My SSN is 123-45-6789, please process",
        "SSN: 999-88-7777",
        "(123-45-6789)",
    ])
    def test_dashed_ssn_redacted(self, r: Redactor, text: str):
        out = r.redact_text(text)
        assert "[REDACTED-SSN]" in out
        assert "123-45-6789" not in out
        assert "999-88-7777" not in out

    def test_bare_ssn_not_redacted_by_default(self, r: Redactor):
        # Bare 9-digit numbers over-redact (zip+4, account suffixes).
        # By default we only catch the dashed form.
        out = r.redact_text("Reference 123456789 for your records")
        assert "123456789" in out

    def test_bare_ssn_redacted_when_opted_in(self):
        r = Redactor(extra=(BARE_SSN_PATTERN,))
        out = r.redact_text("Reference 123456789 for your records")
        assert "[REDACTED-SSN]" in out

    def test_credit_card_redacted(self, r: Redactor):
        out = r.redact_text("Card: 4111 1111 1111 1111 expires 12/26")
        assert "[REDACTED-CREDIT_CARD]" in out
        assert "4111" not in out

    def test_email_redacted(self, r: Redactor):
        out = r.redact_text("Reach me at alice@example.com any time")
        assert "[REDACTED-EMAIL]" in out
        assert "alice@example.com" not in out

    def test_phone_redacted(self, r: Redactor):
        for fmt in ["555-123-4567", "(555) 123-4567", "+1 555.123.4567"]:
            out = r.redact_text(f"Call me at {fmt} tomorrow")
            assert "[REDACTED-PHONE]" in out, f"failed for {fmt!r}"

    def test_routing_number_redacted(self, r: Redactor):
        out = r.redact_text("Routing ABA: 026073150 for the wire")
        assert "[REDACTED-ROUTING]" in out

    def test_clean_text_unchanged(self, r: Redactor):
        clean = "Customer requests rollover; balance approximately $4,500"
        assert r.redact_text(clean) == clean


# ── 2. Recursive structure handling ─────────────────────────────────────────


class TestRecursiveRedaction:
    @pytest.fixture
    def r(self) -> Redactor:
        return Redactor()

    def test_string_value(self, r: Redactor):
        assert r.redact("ssn 123-45-6789") == "ssn [REDACTED-SSN]"

    def test_scalar_passes_through(self, r: Redactor):
        assert r.redact(42)         == 42
        assert r.redact(3.14)       == 3.14
        assert r.redact(True)       is True
        assert r.redact(None)       is None

    def test_dict_recurses_into_values(self, r: Redactor):
        out = r.redact({
            "subject":      "Distribution request",
            "body":         "My SSN is 123-45-6789",
            "amount":       4500,
        })
        assert out["subject"]      == "Distribution request"
        assert "[REDACTED-SSN]" in out["body"]
        assert out["amount"]       == 4500

    def test_dict_redacts_sensitive_keys_wholesale(self, r: Redactor):
        # Even if the value isn't pattern-matchable, sensitive keys get
        # the value replaced wholesale.
        out = r.redact({
            "ssn":            "12345",       # not pattern-shaped
            "password":       "secret123",
            "subject":        "ok",
        })
        assert out["ssn"]          == "[REDACTED-SSN]"
        assert out["password"]     == "[REDACTED-PASSWORD]"
        assert out["subject"]      == "ok"

    def test_nested_dict(self, r: Redactor):
        out = r.redact({
            "claim": {
                "claimant": {
                    "ssn":    "111-22-3333",
                    "name":   "Alice",
                },
                "body": "Wire to ABA: 026073150",
            },
        })
        assert out["claim"]["claimant"]["ssn"]  == "[REDACTED-SSN]"
        assert out["claim"]["claimant"]["name"] == "Alice"
        assert "[REDACTED-ROUTING]" in out["claim"]["body"]

    def test_list_element_wise(self, r: Redactor):
        out = r.redact([
            "ssn 123-45-6789",
            {"ssn": "raw"},
            42,
        ])
        assert "[REDACTED-SSN]" in out[0]
        assert out[1]["ssn"] == "[REDACTED-SSN]"
        assert out[2] == 42

    def test_tuple_preserves_type(self, r: Redactor):
        out = r.redact(("clean", "ssn 123-45-6789"))
        assert isinstance(out, tuple)
        assert out[0] == "clean"
        assert "[REDACTED-SSN]" in out[1]

    def test_redact_never_raises_on_weird_input(self, r: Redactor):
        # Custom objects with no string repr quirks — should pass through.
        class Weird:
            def __repr__(self): raise RuntimeError("broken __repr__")
        w = Weird()
        # Doesn't raise — passes through.
        assert r.redact(w) is w


# ── 3. Custom patterns + extra patterns ─────────────────────────────────────


class TestCustomPatterns:
    def test_extra_patterns_added_alongside_defaults(self):
        # Domain-specific: redact retirement plan IDs in this format.
        plan_id_pat = Pattern("PLAN_ID", re.compile(r"\bPLAN-\d{4,6}\b"))
        r = Redactor(extra=(plan_id_pat,))

        out = r.redact_text("Plan PLAN-99887 + ssn 123-45-6789")
        assert "[REDACTED-PLAN_ID]" in out
        assert "[REDACTED-SSN]"     in out

    def test_replacement_override(self):
        # Custom replacement preserving partial info.
        pat = Pattern(
            "CARD_LAST4",
            re.compile(r"\b(?:\d{4}[\s-]?){3}(\d{4})\b"),
            replacement=r"[REDACTED-CARD-…\1]",
        )
        r = Redactor(patterns=(pat,))
        out = r.redact_text("Card: 4111 1111 1111 1234")
        assert "[REDACTED-CARD-…1234]" in out


# ── 4. RedactingAuditSink integration ───────────────────────────────────────


class _SpyAuditSink:
    """Captures everything passed to .record() so we can assert on it."""
    def __init__(self) -> None:
        self.recorded: list[tuple[tuple, dict]] = []

    async def record(self, *args, **kwargs):
        self.recorded.append((args, kwargs))


@dataclass
class _FakeAuditEvent:
    """Stand-in for Tollgate's AuditEvent — has structural + redactable fields."""
    timestamp: str
    agent_id:  str
    decision:  str
    params:    dict
    reason:    str
    metadata:  dict


class TestRedactingAuditSink:
    @pytest.mark.asyncio
    async def test_redacts_dict_args(self):
        spy = _SpyAuditSink()
        sink = RedactingAuditSink(spy)

        await sink.record({
            "agent_id":     "email-triage",
            "params":       {"body": "ssn 123-45-6789"},
            "reason":       "Contact alice@example.com for review",
        })

        ((arg,), _) = spy.recorded[0]
        assert arg["agent_id"] == "email-triage"   # structural — preserved
        assert "[REDACTED-SSN]"   in arg["params"]["body"]
        assert "[REDACTED-EMAIL]" in arg["reason"]

    @pytest.mark.asyncio
    async def test_redacts_dataclass_arg_preserving_structural_fields(self):
        spy = _SpyAuditSink()
        sink = RedactingAuditSink(spy)

        ev = _FakeAuditEvent(
            timestamp = "2026-05-04T12:00:00Z",
            agent_id  = "email-triage",
            decision  = "ALLOW",
            params    = {"body": "ssn 123-45-6789"},
            reason    = "Email from bob@example.com",
            metadata  = {"prompt_chars": 1234},
        )
        await sink.record(ev)

        ((out,), _) = spy.recorded[0]
        # Structural fields untouched
        assert out.timestamp == "2026-05-04T12:00:00Z"
        assert out.agent_id  == "email-triage"
        assert out.decision  == "ALLOW"
        # Free-text + dict fields redacted
        assert "[REDACTED-SSN]"   in out.params["body"]
        assert "[REDACTED-EMAIL]" in out.reason
        # Numeric metadata untouched
        assert out.metadata["prompt_chars"] == 1234

    @pytest.mark.asyncio
    async def test_passes_through_kwargs(self):
        spy = _SpyAuditSink()
        sink = RedactingAuditSink(spy)
        await sink.record(extra="ssn 123-45-6789")

        (_, kwargs) = spy.recorded[0]
        assert "[REDACTED-SSN]" in kwargs["extra"]

    @pytest.mark.asyncio
    async def test_inner_sync_record_supported(self):
        """Some audit sinks have a sync .record() — wrapper handles both."""
        class SyncSink:
            def __init__(self): self.last = None
            def record(self, payload):
                self.last = payload
                return "sync-ack"

        spy = SyncSink()
        sink = RedactingAuditSink(spy)
        result = await sink.record({"reason": "ssn 123-45-6789"})

        assert result == "sync-ack"
        assert "[REDACTED-SSN]" in spy.last["reason"]

    def test_passthrough_for_other_methods(self):
        """`.close()`, `.flush()` etc. delegate to inner without interference."""
        class MultiMethodSink:
            def __init__(self): self.closed = False
            async def record(self, *a, **k): pass
            def close(self): self.closed = True

        spy = MultiMethodSink()
        sink = RedactingAuditSink(spy)
        sink.close()
        assert spy.closed is True

    def test_missing_record_raises(self):
        class NoRecord:
            pass
        sink = RedactingAuditSink(NoRecord())
        # Synchronous AttributeError — caller didn't honour the protocol.
        with pytest.raises(AttributeError, match="record"):
            import asyncio
            asyncio.run(sink.record({"x": 1}))
