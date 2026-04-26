"""arc.core.lifecycle.watcher — anomaly auto-demotion watcher.

Runs once per cron tick. For each agent in a ``DirectoryManifestStore``:

  1. Read the agent's ``slo`` block. Skip if absent.
  2. Compute window stats from the agent's outcome JSONL.
  3. Evaluate the SLOs (pure function in ``arc.core.slo``).
  4. Apply hysteresis — bump or reset a ``BreachStateStore`` counter.
  5. If the counter has reached ``consecutive_breaches_required``,
     enforce the cooldown (no demotion within N hours of any prior
     promotion or demotion in the audit log).
  6. Honour the kill switch (``ARC_AUTO_DEMOTE_DISABLED=1``).
  7. Drop one stage. ``proposed`` mode enqueues a ``PendingApproval``
     so a human resolves; ``auto`` mode calls ``PromotionService.demote``
     directly.

The watcher itself is stateless — every run reads cooldown state from
the audit log and breach state from the breach store. That's what makes
it cron-safe; restarting mid-run loses nothing.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable

from ..slo import DemotionMode, SLOReport, evaluate_slo

if False:  # pragma: no cover — typing only
    from ..manifest import AgentManifest, ManifestStore
    from ..observability import OutcomeTracker
    from .approvals import PendingApprovalStore
    from .breach_state import BreachStateStore
    from .pipeline import PromotionAuditLog, PromotionService

from .pipeline import PromotionDecision, PromotionRequest, PromotionOutcome
from .stages import LifecycleStage


logger = logging.getLogger(__name__)


# ── Defaults exposed as constants so tests + callers can override ───────────

DEFAULT_CONSECUTIVE_BREACHES_REQUIRED = 3
DEFAULT_COOLDOWN_HOURS                = 24
KILL_SWITCH_ENV                       = "ARC_AUTO_DEMOTE_DISABLED"


# ── Result types ────────────────────────────────────────────────────────────


@dataclass
class WatchResult:
    """One agent's outcome from a single watcher pass.

    The watcher emits one of these per agent it considered. ``action`` is
    the verb the watcher actually took:

      ``"skipped:no-slo"``        agent has no SLO block
      ``"skipped:not-eligible"``  agent isn't at SCALE/GOVERN
      ``"skipped:disabled"``      kill switch is on
      ``"skipped:no-data"``       window had < min_volume events
      ``"ok"``                    all rules passed
      ``"breach-pending"``        breach observed; not yet at threshold
      ``"cooldown"``              would have demoted but cooldown blocks it
      ``"proposed"``              PendingApproval enqueued for human review
      ``"demoted"``               agent's manifest stage dropped
    """
    agent_id: str
    action: str
    detail: str = ""
    consecutive_breaches: int = 0
    decision: PromotionDecision | None = None
    approval_id: str | None = None
    breaches: list[str] = field(default_factory=list)


# ── The watcher ─────────────────────────────────────────────────────────────


class DemotionWatcher:
    """Stateless coordinator for the auto-demotion pass.

    Construct once with the persistent stores it needs (manifest store,
    audit log, breach state, optional approval store, the promotion
    service) and call ``run`` to process every agent in the manifest store.
    """

    def __init__(
        self,
        *,
        manifest_store: "ManifestStore",
        outcome_tracker: "OutcomeTracker",
        breach_state: "BreachStateStore",
        promotion_service: "PromotionService",
        approval_store: "PendingApprovalStore | None" = None,
        consecutive_breaches_required: int = DEFAULT_CONSECUTIVE_BREACHES_REQUIRED,
        cooldown_hours: int = DEFAULT_COOLDOWN_HOURS,
        eligible_stages: tuple[LifecycleStage, ...] = (
            LifecycleStage.SCALE,
            LifecycleStage.GOVERN,
            LifecycleStage.VALIDATE,
        ),
    ) -> None:
        self.manifest_store               = manifest_store
        self.outcome_tracker              = outcome_tracker
        self.breach_state                 = breach_state
        self.promotion_service            = promotion_service
        self.approval_store               = approval_store
        self.consecutive_breaches_required = consecutive_breaches_required
        self.cooldown_hours               = cooldown_hours
        self.eligible_stages              = eligible_stages

    # ── Entry point ─────────────────────────────────────────────────────

    def run(self, agent_ids: Iterable[str], *, now: datetime | None = None) -> list[WatchResult]:
        """Evaluate every agent and return per-agent results.

        ``now`` is the evaluation reference time — wired in for
        deterministic tests. Defaults to ``datetime.now(timezone.utc)``.
        """
        ref = now or datetime.now(timezone.utc)
        results: list[WatchResult] = []

        # Honour kill switch — short-circuit before any I/O for fast no-op runs.
        if _kill_switch_engaged():
            for agent_id in agent_ids:
                results.append(WatchResult(
                    agent_id=agent_id,
                    action="skipped:disabled",
                    detail=f"{KILL_SWITCH_ENV}=1",
                ))
            logger.info("auto-demotion kill switch on (%s); %d agents skipped",
                        KILL_SWITCH_ENV, len(results))
            return results

        for agent_id in agent_ids:
            try:
                results.append(self._evaluate_agent(agent_id, ref))
            except Exception as exc:
                # One bad agent must not break the whole pass.
                logger.exception("watcher error on agent_id=%s", agent_id)
                results.append(WatchResult(
                    agent_id=agent_id,
                    action="error",
                    detail=str(exc),
                ))
        return results

    # ── Per-agent pass ──────────────────────────────────────────────────

    def _evaluate_agent(self, agent_id: str, now: datetime) -> WatchResult:
        manifest = self.manifest_store.load(agent_id)

        if manifest.slo is None or manifest.slo.is_empty():
            return WatchResult(agent_id=agent_id, action="skipped:no-slo")

        if manifest.lifecycle_stage not in self.eligible_stages:
            return WatchResult(
                agent_id=agent_id,
                action="skipped:not-eligible",
                detail=(
                    f"stage={manifest.lifecycle_stage.value} "
                    f"not in {[s.value for s in self.eligible_stages]}"
                ),
            )

        # Custom metrics declared in the SLO are passed through to the
        # tracker's stats computation so the rules can reference them.
        custom_metrics = [r.metric for r in manifest.slo.rules]
        stats = self.outcome_tracker.window_stats(
            agent_id        = agent_id,
            window_seconds  = manifest.slo.window_seconds(),
            custom_metrics  = custom_metrics,
            now             = now,
        )
        report: SLOReport = evaluate_slo(manifest.slo, stats)

        if report.skipped:
            # Don't touch the breach counter on insufficient data — we
            # neither saw a breach nor confirmed health.
            return WatchResult(
                agent_id=agent_id,
                action="skipped:no-data",
                detail=report.skipped_reason,
            )

        if not report.has_breach:
            state = self.breach_state.record(agent_id, breached=False)
            return WatchResult(
                agent_id=agent_id,
                action="ok",
                consecutive_breaches=state.consecutive_breaches,
            )

        # Record the breach, possibly hitting the hysteresis threshold.
        state = self.breach_state.record(agent_id, breached=True)
        breach_reasons = [e.reason for e in report.breaches]

        if state.consecutive_breaches < self.consecutive_breaches_required:
            return WatchResult(
                agent_id=agent_id,
                action="breach-pending",
                detail=(
                    f"{state.consecutive_breaches}/"
                    f"{self.consecutive_breaches_required} consecutive breaches"
                ),
                consecutive_breaches=state.consecutive_breaches,
                breaches=breach_reasons,
            )

        # Threshold reached — check cooldown.
        if self._in_cooldown(agent_id, now):
            return WatchResult(
                agent_id=agent_id,
                action="cooldown",
                detail=f"within {self.cooldown_hours}h of last state change",
                consecutive_breaches=state.consecutive_breaches,
                breaches=breach_reasons,
            )

        # Drop one stage. SCALE → GOVERN, GOVERN → VALIDATE, etc.
        target_stage = _previous_stage(manifest.lifecycle_stage)
        if target_stage is None:
            # Already at the bottom; nothing to demote to.
            return WatchResult(
                agent_id=agent_id,
                action="skipped:no-prior-stage",
                detail=f"stage={manifest.lifecycle_stage.value} has no predecessor",
            )

        reason = "; ".join(breach_reasons) or "SLO breach"

        if manifest.slo.demotion_mode == DemotionMode.AUTO:
            decision = self.promotion_service.demote(
                agent_id    = agent_id,
                from_stage  = manifest.lifecycle_stage,
                to_stage    = target_stage,
                requester   = "auto-demotion-watcher",
                reason      = reason,
                decided_by  = "auto-demotion-watcher",
            )
            # Reset the counter — the agent has been demoted; further
            # breaches at the new stage are independent of these ones.
            self.breach_state.reset(agent_id)
            return WatchResult(
                agent_id=agent_id,
                action="demoted",
                detail=f"{manifest.lifecycle_stage.value} → {target_stage.value}",
                consecutive_breaches=0,
                decision=decision,
                breaches=breach_reasons,
            )

        # PROPOSED mode — enqueue for human review. The watcher does NOT
        # touch the manifest stage; the human approving moves it.
        if self.approval_store is None:
            return WatchResult(
                agent_id=agent_id,
                action="error",
                detail=(
                    "demotion_mode=proposed but no approval_store configured; "
                    "construct DemotionWatcher with approval_store=..."
                ),
                consecutive_breaches=state.consecutive_breaches,
                breaches=breach_reasons,
            )

        request = PromotionRequest(
            agent_id      = agent_id,
            current_stage = manifest.lifecycle_stage,
            target_stage  = target_stage,
            requester     = "auto-demotion-watcher",
            justification = reason,
            evidence      = {"breaches": breach_reasons, "stats": stats},
            kind          = "demotion",
        )
        deferred = PromotionDecision(
            request      = request,
            outcome      = PromotionOutcome.DEFERRED,
            gate_results = [],
            reason       = f"auto-demotion proposed: {reason}",
            decided_by   = "auto-demotion-watcher",
        )
        # Audit the proposal so the trail is complete even before a human acts.
        self.promotion_service.audit_log.record(deferred)
        approval_id = self.approval_store.enqueue(deferred)

        return WatchResult(
            agent_id=agent_id,
            action="proposed",
            detail=f"{manifest.lifecycle_stage.value} → {target_stage.value}",
            consecutive_breaches=state.consecutive_breaches,
            decision=deferred,
            approval_id=approval_id,
            breaches=breach_reasons,
        )

    # ── Cooldown ────────────────────────────────────────────────────────

    def _in_cooldown(self, agent_id: str, now: datetime) -> bool:
        """True if any state change happened within ``cooldown_hours``.

        State change = any APPROVED entry in the audit log for this agent
        (covers both promotion and demotion). DEFERRED proposals don't
        count — they haven't moved the agent.
        """
        history = self.promotion_service.audit_log.history(agent_id=agent_id)
        if not history:
            return False
        cutoff = now.timestamp() - (self.cooldown_hours * 3600)
        for d in history:
            if d.outcome != PromotionOutcome.APPROVED:
                continue
            ts = _parse_ts(d.decided_at)
            if ts is None:
                continue
            if ts.timestamp() >= cutoff:
                return True
        return False


# ── Helpers ─────────────────────────────────────────────────────────────────


def _kill_switch_engaged() -> bool:
    val = os.environ.get(KILL_SWITCH_ENV, "").strip().lower()
    return val in ("1", "true", "yes", "on")


def _previous_stage(stage: LifecycleStage) -> LifecycleStage | None:
    """The stage immediately before ``stage`` in pipeline order, or None."""
    order = list(LifecycleStage)
    idx = order.index(stage)
    return order[idx - 1] if idx > 0 else None


def _parse_ts(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None
