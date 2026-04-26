"""
arc.core.lifecycle.pipeline — incubation + promotion pipeline.

Built on top of the static stage definitions in `arc.core.lifecycle.stages`.
Adds the runtime machinery for moving agents *through* the pipeline:

  - PromotionRequest    — declarative ask: "move agent X from stage A to stage B"
  - GateCheck           — a callable that returns pass / fail with a reason
  - GateChecker         — a registry of checks per target stage
  - PromotionService    — orchestrates: run gates → audit → return decision
  - PromotionAuditLog   — append-only record of every promotion decision
  - InMemoryPromotionAuditLog / JsonlPromotionAuditLog — built-in implementations

Design goals:
  - Composable. Built-in check primitives cover the common cases; users
    register their own callables for project-specific rules.
  - Reversible. The pipeline records every decision (approved, rejected,
    deferred) so demotion is just another promotion in the opposite direction.
  - Standalone. No dependency on ControlTower or the policy engine — the
    pipeline is an orthogonal governance layer that operates on AgentManifest
    state, not on individual tool calls.

Quick example:

    from arc.core.lifecycle import (
        LifecycleStage, PromotionService, PromotionRequest, GateChecker,
        evidence_field_check, stage_order_check,
    )

    checker = GateChecker()
    checker.register(LifecycleStage.VALIDATE, stage_order_check())
    checker.register(LifecycleStage.VALIDATE, evidence_field_check("test_results"))
    checker.register(LifecycleStage.VALIDATE, evidence_field_check("edge_case_log"))

    service = PromotionService(checker)
    decision = service.promote(PromotionRequest(
        agent_id="email-triage",
        current_stage=LifecycleStage.BUILD,
        target_stage=LifecycleStage.VALIDATE,
        requester="alice@team",
        justification="Sandbox tests green for 7 days",
        evidence={"test_results": "...", "edge_case_log": "..."},
    ))
    if decision.approved:
        manifest.lifecycle_stage = decision.request.target_stage
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Protocol

from .stages import LifecycleStage, stage_gate

if TYPE_CHECKING:
    from arc.core.manifest import AgentManifest, ManifestStore

    from .approvals import PendingApprovalStore


# ── Outcome and result types ─────────────────────────────────────────────────


class PromotionOutcome(str, Enum):
    """Final outcome of a single promotion attempt."""
    APPROVED = "approved"     # all gates passed; agent may move to target stage
    REJECTED = "rejected"     # one or more gates failed; promotion blocked
    DEFERRED = "deferred"     # gates passed but human approval is required


@dataclass(frozen=True)
class GateCheckResult:
    """Outcome of one named gate check."""
    name: str
    passed: bool
    reason: str = ""


@dataclass
class PromotionRequest:
    """Declarative ask to move an agent from one stage to another."""
    agent_id: str
    current_stage: LifecycleStage
    target_stage: LifecycleStage
    requester: str
    justification: str
    evidence: dict[str, Any] = field(default_factory=dict)
    requested_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @property
    def is_demotion(self) -> bool:
        """True if target_stage comes earlier in the pipeline than current_stage."""
        order = list(LifecycleStage)
        return order.index(self.target_stage) < order.index(self.current_stage)


@dataclass
class PromotionDecision:
    """Final decision produced by PromotionService.promote()."""
    request: PromotionRequest
    outcome: PromotionOutcome
    gate_results: list[GateCheckResult]
    reason: str = ""
    decided_by: str = "system"
    decided_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    @property
    def approved(self) -> bool:
        return self.outcome == PromotionOutcome.APPROVED

    @property
    def rejected(self) -> bool:
        return self.outcome == PromotionOutcome.REJECTED

    @property
    def deferred(self) -> bool:
        return self.outcome == PromotionOutcome.DEFERRED

    @property
    def passed_gates(self) -> list[GateCheckResult]:
        return [g for g in self.gate_results if g.passed]

    @property
    def failed_gates(self) -> list[GateCheckResult]:
        return [g for g in self.gate_results if not g.passed]

    def to_dict(self) -> dict:
        return {
            "agent_id":     self.request.agent_id,
            "current_stage": self.request.current_stage.value,
            "target_stage":  self.request.target_stage.value,
            "requester":     self.request.requester,
            "justification": self.request.justification,
            "evidence":      self.request.evidence,
            "outcome":       self.outcome.value,
            "reason":        self.reason,
            "decided_by":    self.decided_by,
            "decided_at":    self.decided_at,
            "gate_results": [
                {"name": g.name, "passed": g.passed, "reason": g.reason}
                for g in self.gate_results
            ],
        }


# ── Gate checks ──────────────────────────────────────────────────────────────


# A GateCheck is a callable that inspects the PromotionRequest and returns
# a GateCheckResult. Pure functions, easily testable, easily composed.
GateCheck = Callable[[PromotionRequest], GateCheckResult]


class GateChecker:
    """A registry of GateChecks keyed by the target stage they guard."""

    def __init__(self) -> None:
        self._checks: dict[LifecycleStage, list[GateCheck]] = {
            s: [] for s in LifecycleStage
        }

    def register(self, target_stage: LifecycleStage, check: GateCheck) -> "GateChecker":
        """Add a check that must pass before promotion to target_stage."""
        self._checks[target_stage].append(check)
        return self

    def checks_for(self, target_stage: LifecycleStage) -> list[GateCheck]:
        """Inspect the registered checks for a stage. Mainly for tests."""
        return list(self._checks[target_stage])

    def evaluate(self, request: PromotionRequest) -> list[GateCheckResult]:
        """Run every registered check for the request's target stage."""
        return [chk(request) for chk in self._checks[request.target_stage]]


# ── Built-in check primitives ────────────────────────────────────────────────


def stage_order_check() -> GateCheck:
    """Require target_stage to be the immediate successor of current_stage."""
    def _chk(req: PromotionRequest) -> GateCheckResult:
        nxt = req.current_stage.next_stage()
        ok = nxt is not None and nxt == req.target_stage
        return GateCheckResult(
            name="stage_order",
            passed=bool(ok),
            reason="" if ok else (
                f"{req.target_stage.value} is not the next stage after "
                f"{req.current_stage.value}; expected {nxt.value if nxt else 'none'}"
            ),
        )
    return _chk


def evidence_field_check(
    field_name: str,
    *,
    label: str | None = None,
    required: bool = True,
) -> GateCheck:
    """Require a non-empty value for a key inside request.evidence."""
    def _chk(req: PromotionRequest) -> GateCheckResult:
        value = req.evidence.get(field_name)
        ok = bool(value) if required else True
        return GateCheckResult(
            name=label or f"evidence:{field_name}",
            passed=ok,
            reason="" if ok else f"missing or empty evidence field {field_name!r}",
        )
    return _chk


def artifact_exists_check(
    evidence_field: str,
    *,
    label: str | None = None,
) -> GateCheck:
    """Treat the value at evidence[evidence_field] as a path; require it to exist."""
    def _chk(req: PromotionRequest) -> GateCheckResult:
        path_str = req.evidence.get(evidence_field)
        if not path_str:
            return GateCheckResult(
                name=label or f"artifact:{evidence_field}",
                passed=False,
                reason=f"evidence[{evidence_field!r}] is empty",
            )
        path = Path(str(path_str))
        ok = path.exists()
        return GateCheckResult(
            name=label or f"artifact:{evidence_field}",
            passed=ok,
            reason="" if ok else f"file does not exist: {path}",
        )
    return _chk


def reviewer_present_check() -> GateCheck:
    """Require evidence['reviewer'] to match the stage's required reviewer role."""
    def _chk(req: PromotionRequest) -> GateCheckResult:
        gate = stage_gate(req.target_stage)
        reviewer = req.evidence.get("reviewer", "")
        ok = bool(reviewer)
        return GateCheckResult(
            name=f"reviewer:{gate.reviewer}",
            passed=ok,
            reason="" if ok else (
                f"target stage {req.target_stage.value} requires a reviewer "
                f"(role: {gate.reviewer}); none provided in evidence"
            ),
        )
    return _chk


def predicate_check(
    name: str,
    predicate: Callable[[PromotionRequest], bool],
    *,
    fail_reason: str = "predicate returned False",
) -> GateCheck:
    """Wrap any boolean predicate as a GateCheck — escape hatch for project rules."""
    def _chk(req: PromotionRequest) -> GateCheckResult:
        ok = bool(predicate(req))
        return GateCheckResult(
            name=name,
            passed=ok,
            reason="" if ok else fail_reason,
        )
    return _chk


# ── Audit log ────────────────────────────────────────────────────────────────


class PromotionAuditLog(Protocol):
    """Append-only sink for promotion decisions."""

    def record(self, decision: PromotionDecision) -> None: ...
    def history(self, agent_id: str | None = None) -> list[PromotionDecision]: ...


class InMemoryPromotionAuditLog:
    """Default audit log — keeps decisions in memory. Good for tests + harness."""

    def __init__(self) -> None:
        self._entries: list[PromotionDecision] = []

    def record(self, decision: PromotionDecision) -> None:
        self._entries.append(decision)

    def history(self, agent_id: str | None = None) -> list[PromotionDecision]:
        if agent_id is None:
            return list(self._entries)
        return [d for d in self._entries if d.request.agent_id == agent_id]

    def __len__(self) -> int:
        return len(self._entries)


class JsonlPromotionAuditLog:
    """Append-only JSONL audit log persisted to disk.

    Each promotion decision becomes one line in the file. Reload by reading
    every line; new appends never rewrite history. Compatible with the
    existing JsonlAuditSink pattern in arc.core.observability.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, decision: PromotionDecision) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(decision.to_dict(), ensure_ascii=False))
            f.write("\n")

    def history(self, agent_id: str | None = None) -> list[PromotionDecision]:
        if not self.path.exists():
            return []
        out: list[PromotionDecision] = []
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                d = json.loads(line)
                if agent_id is not None and d.get("agent_id") != agent_id:
                    continue
                out.append(_decision_from_dict(d))
        return out


def _decision_from_dict(d: dict) -> PromotionDecision:
    """Reconstruct a PromotionDecision from a JSONL audit entry."""
    request = PromotionRequest(
        agent_id=d["agent_id"],
        current_stage=LifecycleStage(d["current_stage"]),
        target_stage=LifecycleStage(d["target_stage"]),
        requester=d["requester"],
        justification=d["justification"],
        evidence=d.get("evidence", {}),
        requested_at=d.get("requested_at", ""),
    )
    return PromotionDecision(
        request=request,
        outcome=PromotionOutcome(d["outcome"]),
        gate_results=[
            GateCheckResult(name=g["name"], passed=g["passed"], reason=g.get("reason", ""))
            for g in d.get("gate_results", [])
        ],
        reason=d.get("reason", ""),
        decided_by=d.get("decided_by", "system"),
        decided_at=d.get("decided_at", ""),
    )


# ── Service ──────────────────────────────────────────────────────────────────


class PromotionService:
    """Orchestrate promotion attempts: check gates → record audit → return decision.

    The service is intentionally synchronous and side-effect-light: it does
    not mutate any AgentManifest itself, so callers can decide whether to
    apply the decision (`manifest.lifecycle_stage = decision.request.target_stage`)
    immediately or after additional out-of-band approval.

    Args:
        checker:        GateChecker registered with stage-specific checks.
        audit_log:      Where to record every decision. Defaults to an
                        in-memory log; pass JsonlPromotionAuditLog for prod.
        require_human:  Set of LifecycleStages that always defer to a human
                        even when all gates pass — e.g. {LifecycleStage.SCALE}
                        forces compliance officer review for production
                        promotion regardless of automated checks.
    """

    def __init__(
        self,
        checker: GateChecker,
        audit_log: PromotionAuditLog | None = None,
        *,
        require_human: set[LifecycleStage] | None = None,
        approval_store: "PendingApprovalStore | None" = None,
    ) -> None:
        self.checker = checker
        # `is None` rather than truthy: InMemoryPromotionAuditLog defines
        # __len__ so an empty log evaluates falsy and would otherwise be
        # silently replaced with a fresh (also empty) instance.
        self.audit_log = audit_log if audit_log is not None else InMemoryPromotionAuditLog()
        self.require_human = require_human or set()
        # Optional: when set, DEFERRED decisions are also enqueued here so
        # a reviewer can resolve them later via ``resolve_approval``.
        self.approval_store = approval_store

    def promote(
        self,
        request: PromotionRequest,
        *,
        decided_by: str = "system",
    ) -> PromotionDecision:
        """Run gate checks, decide outcome, record the decision, return it.

        If the outcome is DEFERRED and an ``approval_store`` is configured,
        the decision is also enqueued there. Use ``resolve_approval`` to
        process the human's eventual decision.
        """
        gate_results = self.checker.evaluate(request)
        all_passed = all(g.passed for g in gate_results)

        if not all_passed:
            outcome = PromotionOutcome.REJECTED
            failed = [g.name for g in gate_results if not g.passed]
            reason = f"failed gates: {', '.join(failed)}"
        elif request.target_stage in self.require_human:
            outcome = PromotionOutcome.DEFERRED
            reason = (
                f"all gates passed; awaiting human approval for "
                f"{request.target_stage.value} (require_human policy)"
            )
        else:
            outcome = PromotionOutcome.APPROVED
            reason = "all gates passed"

        decision = PromotionDecision(
            request=request,
            outcome=outcome,
            gate_results=gate_results,
            reason=reason,
            decided_by=decided_by,
        )
        self.audit_log.record(decision)

        # Enqueue DEFERRED decisions for human review (if a store is wired).
        if outcome == PromotionOutcome.DEFERRED and self.approval_store is not None:
            self.approval_store.enqueue(decision)

        return decision

    def resolve_approval(
        self,
        approval_id: str,
        *,
        approve: bool,
        reviewer: str,
        reason: str = "",
    ) -> PromotionDecision:
        """Process a human's decision on a previously DEFERRED promotion.

        Looks up the pending approval, marks it resolved in the store, and
        records a fresh APPROVED or REJECTED decision in the audit log.
        Returns the new decision so callers can chain ``apply_decision``
        to actually update the manifest.

        Raises:
            RuntimeError: no ``approval_store`` was wired at construction.
            KeyError:     ``approval_id`` not found in the store.
            ValueError:   the entry was already resolved.
        """
        if self.approval_store is None:
            raise RuntimeError(
                "PromotionService.resolve_approval requires an approval_store "
                "at construction time."
            )

        # Mark resolved in the pending store first — readers see the new
        # state immediately. Raises if missing or already resolved.
        entry = self.approval_store.resolve(
            approval_id,
            approved=approve,
            reviewer=reviewer,
            reason=reason,
        )

        # Build and audit the resolution decision. We carry the original
        # request + gate results forward so the audit row includes the
        # full evidence the reviewer was looking at.
        outcome = PromotionOutcome.APPROVED if approve else PromotionOutcome.REJECTED
        prefix  = "human review approved" if approve else "human review rejected"
        new_reason = f"{prefix}: {reason}" if reason else prefix
        new_decision = PromotionDecision(
            request      = entry.decision.request,
            outcome      = outcome,
            gate_results = list(entry.decision.gate_results),
            reason       = new_reason,
            decided_by   = reviewer,
        )
        self.audit_log.record(new_decision)
        return new_decision

    def demote(
        self,
        agent_id: str,
        from_stage: LifecycleStage,
        to_stage: LifecycleStage,
        *,
        requester: str,
        reason: str,
        decided_by: str = "system",
    ) -> PromotionDecision:
        """Move an agent backward through the pipeline (e.g., anomaly auto-demote).

        Demotion bypasses gate checks — the whole point is to roll back when
        something goes wrong. Always recorded in the audit log so the trail
        of every state change stays complete.
        """
        request = PromotionRequest(
            agent_id=agent_id,
            current_stage=from_stage,
            target_stage=to_stage,
            requester=requester,
            justification=reason,
        )
        decision = PromotionDecision(
            request=request,
            outcome=PromotionOutcome.APPROVED,
            gate_results=[],
            reason=f"demotion: {reason}",
            decided_by=decided_by,
        )
        self.audit_log.record(decision)
        return decision


# ── Manifest write-back ──────────────────────────────────────────────────────


def apply_decision(
    decision: PromotionDecision,
    store: "ManifestStore",
) -> "AgentManifest | None":
    """Persist a promotion decision to a ``ManifestStore``.

    For an APPROVED decision: load the agent's manifest, set its
    ``lifecycle_stage`` to ``decision.request.target_stage``, save it back,
    and return the updated manifest.

    For REJECTED or DEFERRED decisions: no-op. Returns ``None``. The audit
    log already records the decision; the manifest stays at its current
    stage until a human resolves the deferral or a new request succeeds.

    This closes the loop on the promotion pipeline: ``service.promote()``
    decides, ``apply_decision()`` writes the result, and the audit log keeps
    every step. Splitting "decide" from "apply" keeps the pipeline pure and
    lets callers gate the apply step on additional out-of-band approval
    when needed.

    Args:
        decision: The decision returned by ``PromotionService.promote()``
                  or ``.demote()``.
        store:    Where to persist the new manifest state.

    Returns:
        The updated AgentManifest if the decision was APPROVED;
        ``None`` for REJECTED or DEFERRED.
    """
    if not decision.approved:
        return None
    manifest = store.load(decision.request.agent_id)
    manifest.lifecycle_stage = decision.request.target_stage
    store.save(manifest)
    return manifest
