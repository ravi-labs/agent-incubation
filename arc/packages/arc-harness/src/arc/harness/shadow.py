"""
ShadowAuditSink — captures every agent decision without side effects.

Collects all AuditEvents emitted by ControlTower during a harness run.
Provides query methods used by DecisionReport to analyse what the agent
decided and why.

In shadow mode the agent's Tier 4+ effects are still *allowed to execute*
(via SandboxApprover) but the ShadowAuditSink records every decision so
the report can show what would have happened in each autonomy level.
"""

from dataclasses import dataclass, field
from tollgate.types import AuditEvent, DecisionType, Outcome


@dataclass
class ShadowAuditSink:
    """
    In-memory audit sink that captures all decisions for harness analysis.

    Drop-in replacement for JsonlAuditSink in harness runs — same
    AuditSink protocol, stores events in memory instead of writing to disk.
    """

    _events: list[AuditEvent] = field(default_factory=list, init=False, repr=False)

    def emit(self, event: AuditEvent) -> None:
        self._events.append(event)

    # ── Query helpers ─────────────────────────────────────────────────────

    @property
    def events(self) -> list[AuditEvent]:
        return list(self._events)

    @property
    def total(self) -> int:
        return len(self._events)

    def by_decision(self, decision: DecisionType) -> list[AuditEvent]:
        return [e for e in self._events if e.decision.decision == decision]

    def by_resource(self, resource_type: str) -> list[AuditEvent]:
        return [e for e in self._events if e.tool_request.resource_type == resource_type]

    @property
    def allow_count(self) -> int:
        return len(self.by_decision(DecisionType.ALLOW))

    @property
    def ask_count(self) -> int:
        return len(self.by_decision(DecisionType.ASK))

    @property
    def deny_count(self) -> int:
        return len(self.by_decision(DecisionType.DENY))

    @property
    def success_count(self) -> int:
        return sum(1 for e in self._events if e.outcome == Outcome.EXECUTED)

    @property
    def error_count(self) -> int:
        return sum(1 for e in self._events if e.outcome == Outcome.FAILED)

    def summary(self) -> dict:
        return {
            "total":   self.total,
            "allow":   self.allow_count,
            "ask":     self.ask_count,
            "deny":    self.deny_count,
            "success": self.success_count,
            "errors":  self.error_count,
        }
