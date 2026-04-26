"""arc.core.lifecycle — incubation pipeline stages, gates, and promotion service.

DISCOVER → SHAPE → BUILD → VALIDATE → GOVERN → SCALE

Two layers:

  - stages.py   Static definitions: LifecycleStage enum + StageGate
                metadata (entry criteria, exit artifacts, reviewer, environment).

  - pipeline.py Runtime promotion machinery: PromotionService runs registered
                GateChecks for the target stage, records every decision in a
                PromotionAuditLog, and supports demotion as a first-class
                operation for anomaly auto-rollback.
"""

from .approvals import (
    APPROVED,
    InMemoryPendingApprovalStore,
    JsonlPendingApprovalStore,
    PENDING,
    REJECTED,
    PendingApproval,
    PendingApprovalStore,
)
from .pipeline import (
    GateCheck,
    GateCheckResult,
    GateChecker,
    InMemoryPromotionAuditLog,
    JsonlPromotionAuditLog,
    PromotionAuditLog,
    PromotionDecision,
    PromotionOutcome,
    PromotionRequest,
    PromotionService,
    apply_decision,
    artifact_exists_check,
    evidence_field_check,
    predicate_check,
    reviewer_present_check,
    stage_order_check,
)
from .stages import LifecycleStage, StageGate, stage_gate

__all__ = [
    # Stage definitions
    "LifecycleStage", "StageGate", "stage_gate",
    # Promotion pipeline core
    "PromotionRequest", "PromotionDecision", "PromotionOutcome",
    "GateCheck", "GateCheckResult", "GateChecker", "PromotionService",
    "apply_decision",
    # Built-in check primitives
    "stage_order_check", "evidence_field_check", "artifact_exists_check",
    "reviewer_present_check", "predicate_check",
    # Audit log
    "PromotionAuditLog", "InMemoryPromotionAuditLog", "JsonlPromotionAuditLog",
    # Pending-approval store (DEFERRED handoff)
    "PendingApproval", "PendingApprovalStore",
    "InMemoryPendingApprovalStore", "JsonlPendingApprovalStore",
    "PENDING", "APPROVED", "REJECTED",
]
