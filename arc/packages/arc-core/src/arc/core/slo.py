"""arc.core.slo — Service-Level Objectives for auto-demotion.

A small declarative schema agents can attach to their manifest to drive
the anomaly auto-demotion watcher. Three building blocks:

  - ``SLORule``        one threshold check (metric op threshold)
  - ``SLOConfig``      window + min_volume + a list of rules
  - ``DemotionMode``   ``proposed`` (queue for human review) | ``auto``

The schema is intentionally small. The watcher reads ``manifest.slo`` and
asks ``arc.core.observability.OutcomeTracker`` for a stats dict over the
declared window. Each rule's metric is looked up in that dict (built-in
keys like ``error_rate`` and ``p95_latency_ms`` plus custom dotted keys
agents emit themselves) and compared against ``threshold`` using ``op``.

A breach = ``not (stats[metric] op threshold)``. The watcher then layers
hysteresis (3 consecutive breaches), cooldown (24h after any state
change), and a kill switch on top — none of that lives here. This module
is pure data + a pure evaluator so it stays trivially testable.

Manifest YAML form:

    slo:
      window:     7d
      min_volume: 100
      rules:
        - metric:    error_rate
          op:        "<"
          threshold: 0.05
        - metric:    p95_latency_ms
          op:        "<"
          threshold: 2000
      demotion_mode: proposed   # or "auto"
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ── Modes + operators ───────────────────────────────────────────────────────


class DemotionMode(str, Enum):
    """How a watcher should act on an SLO breach.

    PROPOSED (default)  Write a ``PendingApproval`` so a human can review +
                        approve or reject the demotion. Same plumbing as
                        DEFERRED promotions.
    AUTO                Demote immediately. Audit log records it; no human
                        in the loop. Use when the breach signal is already
                        well-understood and the agent's blast radius is
                        small.
    """
    PROPOSED = "proposed"
    AUTO     = "auto"


_OPERATORS: dict[str, "callable"] = {
    "<":  lambda a, b: a <  b,
    "<=": lambda a, b: a <= b,
    ">":  lambda a, b: a >  b,
    ">=": lambda a, b: a >= b,
    "==": lambda a, b: a == b,
    "!=": lambda a, b: a != b,
}


# ── Window parsing ──────────────────────────────────────────────────────────

_WINDOW_RE = re.compile(r"^\s*(\d+)\s*([smhdw])\s*$", re.IGNORECASE)
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86_400, "w": 7 * 86_400}


def parse_window_seconds(window: str) -> int:
    """Parse a window string like ``7d``, ``24h``, ``30m`` into seconds.

    Accepts a trailing unit suffix: ``s``/``m``/``h``/``d``/``w``. Whitespace
    and mixed case are fine. Plain integers (no unit) are rejected — windows
    without a unit are ambiguous and easy to typo.
    """
    m = _WINDOW_RE.match(window)
    if not m:
        raise ValueError(
            f"Invalid SLO window {window!r}. "
            "Use a number with a unit suffix: s, m, h, d, or w (e.g. '7d')."
        )
    n, unit = int(m.group(1)), m.group(2).lower()
    return n * _UNIT_SECONDS[unit]


# ── Schema ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SLORule:
    """One metric threshold.

    A rule passes when ``stats[metric] op threshold`` is True. Missing
    metrics are treated as a breach (the watcher can't verify the SLO,
    so it's not safe to assume it's met). ``min_volume`` on the parent
    ``SLOConfig`` suppresses evaluation entirely when there isn't enough
    data — that takes precedence over per-rule semantics.
    """
    metric: str
    op: str
    threshold: float

    def __post_init__(self) -> None:
        if self.op not in _OPERATORS:
            raise ValueError(
                f"Invalid SLO operator {self.op!r}. "
                f"Valid: {sorted(_OPERATORS)}"
            )
        if not self.metric:
            raise ValueError("SLO rule.metric must be a non-empty string.")

    def evaluate(self, stats: dict[str, Any]) -> "SLOEvaluation":
        """Evaluate this rule against a stats dict. See module docstring."""
        if self.metric not in stats:
            return SLOEvaluation(
                rule=self,
                observed=None,
                breached=True,
                reason=f"metric {self.metric!r} missing from window stats",
            )
        observed = stats[self.metric]
        try:
            ok = _OPERATORS[self.op](observed, self.threshold)
        except TypeError as exc:
            return SLOEvaluation(
                rule=self,
                observed=observed,
                breached=True,
                reason=f"could not compare {observed!r} {self.op} {self.threshold!r}: {exc}",
            )
        if ok:
            return SLOEvaluation(rule=self, observed=observed, breached=False)
        return SLOEvaluation(
            rule=self,
            observed=observed,
            breached=True,
            reason=(
                f"{self.metric}={observed!r} violates "
                f"{self.metric} {self.op} {self.threshold}"
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"metric": self.metric, "op": self.op, "threshold": self.threshold}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SLORule":
        return cls(
            metric    = str(d["metric"]).strip(),
            op        = str(d["op"]).strip(),
            threshold = float(d["threshold"]),
        )


@dataclass(frozen=True)
class SLOEvaluation:
    """Per-rule outcome from ``evaluate_slo``."""
    rule: SLORule
    observed: Any
    breached: bool
    reason: str = ""


@dataclass
class SLOConfig:
    """Per-agent SLO declaration.

    window         e.g. ``7d`` — how far back the watcher looks.
    min_volume     skip evaluation if window event count is below this.
    rules          one or more ``SLORule``. Empty list = nothing to check.
    demotion_mode  what to do on breach: ``proposed`` (default) or ``auto``.
    """
    window: str = "24h"
    min_volume: int = 100
    rules: list[SLORule] = field(default_factory=list)
    demotion_mode: DemotionMode = DemotionMode.PROPOSED

    def __post_init__(self) -> None:
        # Validate window early — surfacing the error at manifest load time
        # is much easier to debug than at watcher run time.
        parse_window_seconds(self.window)
        if self.min_volume < 0:
            raise ValueError("SLO min_volume must be non-negative.")

    def is_empty(self) -> bool:
        """True when there is nothing to evaluate (no rules)."""
        return not self.rules

    def window_seconds(self) -> int:
        return parse_window_seconds(self.window)

    def to_dict(self) -> dict[str, Any]:
        return {
            "window":        self.window,
            "min_volume":    self.min_volume,
            "rules":         [r.to_dict() for r in self.rules],
            "demotion_mode": self.demotion_mode.value,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "SLOConfig":
        rules = [SLORule.from_dict(r) for r in (d.get("rules") or [])]
        mode_raw = str(d.get("demotion_mode", DemotionMode.PROPOSED.value)).strip().lower()
        try:
            mode = DemotionMode(mode_raw)
        except ValueError:
            valid = [m.value for m in DemotionMode]
            raise ValueError(
                f"Invalid SLO demotion_mode {mode_raw!r}. Must be one of: {valid}"
            ) from None
        return cls(
            window        = str(d.get("window", "24h")),
            min_volume    = int(d.get("min_volume", 100)),
            rules         = rules,
            demotion_mode = mode,
        )


# ── Evaluator ───────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SLOReport:
    """Result of evaluating an ``SLOConfig`` against a stats dict."""
    skipped: bool                       # True when below min_volume — no decision
    skipped_reason: str = ""
    evaluations: list[SLOEvaluation] = field(default_factory=list)

    @property
    def breaches(self) -> list[SLOEvaluation]:
        return [e for e in self.evaluations if e.breached]

    @property
    def has_breach(self) -> bool:
        return bool(self.breaches)


def evaluate_slo(config: SLOConfig, stats: dict[str, Any]) -> SLOReport:
    """Run every rule in ``config`` against ``stats``.

    The ``stats`` dict is whatever ``OutcomeTracker.window_stats`` produced
    for this agent over the configured window. A built-in ``event_count``
    key gates ``min_volume``: if there isn't enough data we return a
    ``skipped`` report so the watcher doesn't act on a hot-from-deploy
    agent that's only seen a handful of events.

    Pure function — easy to unit test, no I/O.
    """
    if config.is_empty():
        return SLOReport(skipped=True, skipped_reason="no rules declared")
    event_count = int(stats.get("event_count", 0))
    if event_count < config.min_volume:
        return SLOReport(
            skipped=True,
            skipped_reason=(
                f"event_count={event_count} below min_volume={config.min_volume}"
            ),
        )
    return SLOReport(
        skipped=False,
        evaluations=[r.evaluate(stats) for r in config.rules],
    )
