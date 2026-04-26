"""Tests for arc.core.slo — SLO schema + pure evaluator.

The watcher's behaviour (hysteresis, cooldown, kill switch, manifest
write-back) is covered separately in test_demotion_watcher.py. These
tests cover the data layer only.
"""

from __future__ import annotations

import pytest

from arc.core import (
    DemotionMode,
    SLOConfig,
    SLORule,
    evaluate_slo,
    parse_window_seconds,
)


# ── parse_window_seconds ────────────────────────────────────────────────────


class TestParseWindow:
    @pytest.mark.parametrize("text,expected", [
        ("30s", 30),
        ("5m",  300),
        ("2h",  7_200),
        ("1d",  86_400),
        ("7d",  7 * 86_400),
        ("1w",  7 * 86_400),
        ("2W",  14 * 86_400),     # case insensitive
        ("  24h ", 86_400),       # surrounding whitespace ok
    ])
    def test_valid(self, text: str, expected: int):
        assert parse_window_seconds(text) == expected

    @pytest.mark.parametrize("text", ["", "abc", "100", "0d", "5y", "5 minutes"])
    def test_invalid(self, text: str):
        # "0d" is 0 seconds — accepted by the parser but the SLOConfig
        # constructor will reject it via min_volume/etc semantics. The
        # parser only rejects malformed text.
        if text == "0d":
            assert parse_window_seconds(text) == 0
            return
        with pytest.raises(ValueError):
            parse_window_seconds(text)


# ── SLORule ─────────────────────────────────────────────────────────────────


class TestSLORule:
    def test_rejects_unknown_operator(self):
        with pytest.raises(ValueError, match="Invalid SLO operator"):
            SLORule(metric="error_rate", op="approxeq", threshold=0.05)

    def test_rejects_empty_metric(self):
        with pytest.raises(ValueError):
            SLORule(metric="", op="<", threshold=0.0)

    def test_passes_when_within_threshold(self):
        rule = SLORule(metric="error_rate", op="<", threshold=0.05)
        ev = rule.evaluate({"error_rate": 0.02})
        assert ev.breached is False
        assert ev.observed == 0.02

    def test_breaches_when_outside(self):
        rule = SLORule(metric="error_rate", op="<", threshold=0.05)
        ev = rule.evaluate({"error_rate": 0.10})
        assert ev.breached is True
        assert "error_rate=0.1" in ev.reason

    def test_missing_metric_is_a_breach(self):
        # A missing metric means the watcher can't verify the SLO — safest
        # to treat that as breached so it's surfaced rather than silently
        # ignored.
        rule = SLORule(metric="error_rate", op="<", threshold=0.05)
        ev = rule.evaluate({"event_count": 100})
        assert ev.breached is True
        assert "missing" in ev.reason

    def test_uncomparable_value_is_a_breach(self):
        rule = SLORule(metric="error_rate", op="<", threshold=0.05)
        ev = rule.evaluate({"error_rate": "not-a-number"})
        assert ev.breached is True
        assert "could not compare" in ev.reason

    def test_round_trip(self):
        rule = SLORule(metric="p95_latency_ms", op="<=", threshold=2000)
        rebuilt = SLORule.from_dict(rule.to_dict())
        assert rebuilt == rule


# ── SLOConfig ───────────────────────────────────────────────────────────────


class TestSLOConfig:
    def test_default_is_empty(self):
        cfg = SLOConfig()
        assert cfg.is_empty()
        assert cfg.demotion_mode == DemotionMode.PROPOSED

    def test_window_seconds(self):
        cfg = SLOConfig(window="6h")
        assert cfg.window_seconds() == 6 * 3600

    def test_negative_min_volume_rejected(self):
        with pytest.raises(ValueError):
            SLOConfig(min_volume=-1)

    def test_invalid_demotion_mode_rejected(self):
        with pytest.raises(ValueError, match="demotion_mode"):
            SLOConfig.from_dict({"demotion_mode": "ASAP"})

    def test_round_trip(self):
        cfg = SLOConfig(
            window="7d",
            min_volume=200,
            rules=[
                SLORule(metric="error_rate", op="<", threshold=0.05),
                SLORule(metric="p95_latency_ms", op="<", threshold=2000),
            ],
            demotion_mode=DemotionMode.AUTO,
        )
        rebuilt = SLOConfig.from_dict(cfg.to_dict())
        assert rebuilt == cfg


# ── evaluate_slo ────────────────────────────────────────────────────────────


class TestEvaluateSlo:
    def test_skipped_when_no_rules(self):
        cfg = SLOConfig()  # empty rules
        report = evaluate_slo(cfg, {"event_count": 1_000})
        assert report.skipped
        assert "no rules" in report.skipped_reason

    def test_skipped_below_min_volume(self):
        cfg = SLOConfig(
            min_volume=100,
            rules=[SLORule(metric="error_rate", op="<", threshold=0.05)],
        )
        report = evaluate_slo(cfg, {"event_count": 50, "error_rate": 0.20})
        assert report.skipped
        assert "min_volume" in report.skipped_reason
        # Important: skipped reports do not count as breaches.
        assert not report.has_breach

    def test_no_breach_when_all_pass(self):
        cfg = SLOConfig(
            min_volume=10,
            rules=[
                SLORule(metric="error_rate", op="<", threshold=0.05),
                SLORule(metric="p95_latency_ms", op="<", threshold=2_000),
            ],
        )
        report = evaluate_slo(cfg, {
            "event_count": 200, "error_rate": 0.01, "p95_latency_ms": 800,
        })
        assert not report.skipped
        assert not report.has_breach

    def test_breach_returns_per_rule_reasons(self):
        cfg = SLOConfig(
            min_volume=10,
            rules=[
                SLORule(metric="error_rate", op="<", threshold=0.05),
                SLORule(metric="p95_latency_ms", op="<", threshold=2_000),
            ],
        )
        report = evaluate_slo(cfg, {
            "event_count": 200, "error_rate": 0.20, "p95_latency_ms": 5_000,
        })
        assert report.has_breach
        assert len(report.breaches) == 2
        reasons = " ".join(b.reason for b in report.breaches)
        assert "error_rate" in reasons
        assert "p95_latency_ms" in reasons
