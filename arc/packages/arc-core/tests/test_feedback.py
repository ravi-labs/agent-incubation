"""Tests for arc.core.feedback — Correction + JsonlCorrectionsStore.

Three layers:

  1. Correction dataclass — construction validation, round-trip serialisation
  2. JsonlCorrectionsStore — record + list + summary, filters, malformed rows
  3. Pattern summarisation — case_type / team / generic-fallback shapes
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from arc.core.feedback import (
    Correction,
    JsonlCorrectionsStore,
    SEVERITY_LEVELS,
)


# ── 1. Correction dataclass ─────────────────────────────────────────────────


class TestCorrectionConstruction:
    def test_new_auto_assigns_id_and_timestamp(self):
        c = Correction.new(
            agent_id="email-triage",
            audit_row_id="abc-123",
            reviewer="alice@compliance",
            severity="moderate",
            reason="case_type misclassification",
            original_decision={"case_type": "loan_hardship"},
            corrected_decision={"case_type": "distribution"},
        )
        assert c.correction_id.startswith("corr-")
        assert len(c.correction_id) > len("corr-")
        # ISO 8601 with offset
        assert "T" in c.timestamp and (c.timestamp.endswith("+00:00") or c.timestamp.endswith("Z"))
        assert c.agent_id == "email-triage"

    def test_new_rejects_bad_severity(self):
        with pytest.raises(ValueError, match="severity"):
            Correction.new(
                agent_id="x", audit_row_id="y", reviewer="r",
                severity="extreme",  # not in SEVERITY_LEVELS
                reason="", original_decision={}, corrected_decision={},
            )

    def test_new_rejects_anonymous(self):
        with pytest.raises(ValueError, match="reviewer"):
            Correction.new(
                agent_id="x", audit_row_id="y", reviewer="",
                severity="minor", reason="", original_decision={}, corrected_decision={},
            )

    def test_severity_levels_constant_is_three(self):
        # Three is enough — see feedback.py docstring on why.
        assert SEVERITY_LEVELS == ("minor", "moderate", "critical")


class TestCorrectionRoundTrip:
    def test_to_from_dict(self):
        original = Correction.new(
            agent_id="x", audit_row_id="y", reviewer="r",
            severity="critical", reason="bad",
            original_decision={"case_type": "auto"},
            corrected_decision={"case_type": "property"},
            schema_version="0.1.0",
            metadata={"source": "manual-flag"},
        )
        rebuilt = Correction.from_dict(original.to_dict())
        assert rebuilt == original

    def test_from_dict_ignores_unknown_keys(self):
        d = {
            "correction_id":     "corr-1",
            "timestamp":         "2026-05-03T14:00:00+00:00",
            "agent_id":          "x",
            "audit_row_id":      "y",
            "reviewer":          "r",
            "severity":          "minor",
            "reason":            "",
            "original_decision":  {},
            "corrected_decision": {},
            "future_field":      "ignored-cleanly",
        }
        c = Correction.from_dict(d)
        assert c.correction_id == "corr-1"

    def test_from_dict_fills_missing_optionals(self):
        d = {
            "correction_id":     "corr-1",
            "timestamp":         "2026-05-03T14:00:00+00:00",
            "agent_id":          "x",
            "audit_row_id":      "y",
            "reviewer":          "r",
            "severity":          "minor",
            "reason":            "",
            "original_decision":  {},
            "corrected_decision": {},
            # schema_version + metadata both missing
        }
        c = Correction.from_dict(d)
        assert c.schema_version == ""
        assert c.metadata == {}


# ── 2. JsonlCorrectionsStore ────────────────────────────────────────────────


def _make_correction(
    agent_id: str = "email-triage",
    severity: str = "moderate",
    reviewer: str = "alice@compliance",
    original: dict = None,
    corrected: dict = None,
    timestamp: str | None = None,
) -> Correction:
    """Helper — build a correction with optional explicit timestamp."""
    c = Correction.new(
        agent_id           = agent_id,
        audit_row_id       = f"row-{agent_id}",
        reviewer           = reviewer,
        severity           = severity,
        reason             = "test",
        original_decision  = original or {"case_type": "loan_hardship"},
        corrected_decision = corrected or {"case_type": "distribution"},
    )
    if timestamp is not None:
        c.timestamp = timestamp
    return c


class TestRecordAndList:
    def test_record_then_list_round_trip(self, tmp_path: Path):
        store = JsonlCorrectionsStore(tmp_path / "corrections.jsonl")
        c = _make_correction()
        store.record(c)

        rows = store.list()
        assert len(rows) == 1
        assert rows[0].correction_id == c.correction_id
        assert rows[0].agent_id      == "email-triage"

    def test_list_empty_when_no_file(self, tmp_path: Path):
        store = JsonlCorrectionsStore(tmp_path / "missing.jsonl")
        assert store.list() == []
        assert store.summary() == {
            "total": 0, "by_severity": {s: 0 for s in SEVERITY_LEVELS},
            "by_reviewer": {}, "top_patterns": [],
        }

    def test_list_filters_by_agent_id(self, tmp_path: Path):
        store = JsonlCorrectionsStore(tmp_path / "c.jsonl")
        store.record(_make_correction(agent_id="email-triage"))
        store.record(_make_correction(agent_id="claims-triage"))
        store.record(_make_correction(agent_id="email-triage"))

        rows = store.list(agent_id="email-triage")
        assert len(rows) == 2
        assert all(c.agent_id == "email-triage" for c in rows)

    def test_list_filters_by_since(self, tmp_path: Path):
        store = JsonlCorrectionsStore(tmp_path / "c.jsonl")
        old = _make_correction(timestamp="2026-04-01T00:00:00+00:00")
        new = _make_correction(timestamp="2026-05-03T00:00:00+00:00")
        store.record(old)
        store.record(new)

        rows = store.list(since="2026-04-15T00:00:00+00:00")
        assert len(rows) == 1
        assert rows[0].timestamp == "2026-05-03T00:00:00+00:00"

    def test_list_respects_limit_and_newest_first(self, tmp_path: Path):
        store = JsonlCorrectionsStore(tmp_path / "c.jsonl")
        for i in range(5):
            store.record(_make_correction(
                timestamp=f"2026-05-0{i+1}T00:00:00+00:00",
            ))
        rows = store.list(limit=2)
        assert len(rows) == 2
        # Newest first
        assert rows[0].timestamp >= rows[1].timestamp

    def test_list_tolerates_malformed_rows(self, tmp_path: Path):
        path = tmp_path / "c.jsonl"
        path.write_text(
            json.dumps(_make_correction().to_dict()) + "\n"
            "this is not json\n"
            + json.dumps(_make_correction().to_dict()) + "\n"
        )
        store = JsonlCorrectionsStore(path)
        rows = store.list()
        # Bad line skipped; the two good ones survived.
        assert len(rows) == 2


# ── 3. Summary + pattern detection ──────────────────────────────────────────


class TestSummary:
    def test_summary_buckets_by_severity(self, tmp_path: Path):
        store = JsonlCorrectionsStore(tmp_path / "c.jsonl")
        store.record(_make_correction(severity="minor"))
        store.record(_make_correction(severity="moderate"))
        store.record(_make_correction(severity="moderate"))
        store.record(_make_correction(severity="critical"))

        s = store.summary()
        assert s["total"] == 4
        assert s["by_severity"] == {"minor": 1, "moderate": 2, "critical": 1}

    def test_summary_top_reviewers(self, tmp_path: Path):
        store = JsonlCorrectionsStore(tmp_path / "c.jsonl")
        for _ in range(3):
            store.record(_make_correction(reviewer="alice@"))
        for _ in range(2):
            store.record(_make_correction(reviewer="bob@"))
        store.record(_make_correction(reviewer="carol@"))

        s = store.summary()
        assert s["by_reviewer"]["alice@"] == 3
        assert s["by_reviewer"]["bob@"]   == 2
        # carol may or may not be in top-3; alice/bob definitely are.

    def test_summary_pattern_uses_case_type_when_present(self, tmp_path: Path):
        """When original/corrected both have case_type, pattern label
        should use the human-readable transition (not a hash)."""
        store = JsonlCorrectionsStore(tmp_path / "c.jsonl")
        for _ in range(3):
            store.record(_make_correction(
                original={"case_type": "loan_hardship"},
                corrected={"case_type": "distribution"},
            ))
        s = store.summary()
        assert any(
            p["pattern"] == "loan_hardship → distribution" and p["count"] == 3
            for p in s["top_patterns"]
        )

    def test_summary_pattern_falls_back_to_team(self, tmp_path: Path):
        store = JsonlCorrectionsStore(tmp_path / "c.jsonl")
        store.record(_make_correction(
            original={"team": "loans-standard"},
            corrected={"team": "loans-senior"},
        ))
        s = store.summary()
        assert any(
            "loans-standard" in p["pattern"] and "loans-senior" in p["pattern"]
            for p in s["top_patterns"]
        )

    def test_summary_pattern_generic_fallback(self, tmp_path: Path):
        store = JsonlCorrectionsStore(tmp_path / "c.jsonl")
        store.record(_make_correction(
            original={"random_field": "A"},
            corrected={"random_field": "B"},
        ))
        s = store.summary()
        # Pattern should mention the field name + the transition.
        assert any("random_field" in p["pattern"] for p in s["top_patterns"])

    def test_summary_filters_by_agent_id(self, tmp_path: Path):
        store = JsonlCorrectionsStore(tmp_path / "c.jsonl")
        store.record(_make_correction(agent_id="a"))
        store.record(_make_correction(agent_id="a"))
        store.record(_make_correction(agent_id="b"))
        s = store.summary(agent_id="a")
        assert s["total"] == 2
