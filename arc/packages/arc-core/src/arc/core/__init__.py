"""
arc.core — governance engine.

The foundation everything else builds on. Typed effects, policy evaluation,
ControlTower pre-execution gating, and the audit trail. Tollgate primitives
(ControlTower, YamlPolicyEvaluator, etc.) come from the canonical `tollgate`
package directly.

Public API:
    from arc.core import (
        BaseAgent,
        AgentManifest, load_manifest,
        ControlTower,
        YamlPolicyEvaluator,
        JsonlAuditSink,
        ITSMEffect, FinancialEffect, ComplianceEffect,
    )
"""

# ── Effects, scaffold, gateway, memory, tools, observability, lifecycle ──────
from arc.core.effects import (
    COMPLIANCE_EFFECT_METADATA,
    EFFECT_METADATA,
    HEALTHCARE_EFFECT_METADATA,
    ITSM_EFFECT_METADATA,
    LEGAL_EFFECT_METADATA,
    ComplianceEffect,
    DefaultDecision,
    EffectMeta,
    EffectTier,
    FinancialEffect,
    HealthcareEffect,
    ITSMEffect,
    LegalEffect,
    effect_meta,
    effects_by_tier,
    effects_requiring_review,
)
from arc.core.policy import EffectRequestBuilder
from arc.core.manifest import (
    AgentManifest,
    AgentStatus,
    DirectoryManifestStore,
    LocalFileManifestStore,
    ManifestStore,
    load_manifest,
    save_manifest,
)
from arc.core.llm import LLMClient, LLMConfig, resolve_llm
from arc.core.slo import (
    DemotionMode,
    SLOConfig,
    SLOEvaluation,
    SLOReport,
    SLORule,
    evaluate_slo,
    parse_window_seconds,
)
from arc.core.agent import BaseAgent
from arc.core.gateway import (
    DataRequest,
    DataResponse,
    GatewayConnector,
    HttpGateway,
    MockGatewayConnector,
    MultiGateway,
)
from arc.core.memory import (
    ConversationBuffer,
    DynamoDBMemoryBackend,
    AgentMemoryStore,
    LocalJsonStore,
    MemoryBackend,
    MemoryEntry,
    Message,
)
from arc.core.tools import AgentToolRegistry, GovernedToolDef, ToolRegistry, governed_tool
from arc.core.observability import OutcomeEvent, OutcomeTracker, generate_report
from arc.core.lifecycle import (
    BreachState,
    BreachStateStore,
    DEFAULT_CONSECUTIVE_BREACHES_REQUIRED,
    DEFAULT_COOLDOWN_HOURS,
    DemotionWatcher,
    GateCheck,
    GateCheckResult,
    GateChecker,
    InMemoryBreachStateStore,
    InMemoryPendingApprovalStore,
    InMemoryPromotionAuditLog,
    JsonlBreachStateStore,
    JsonlPendingApprovalStore,
    JsonlPromotionAuditLog,
    KILL_SWITCH_ENV,
    LifecycleStage,
    PendingApproval,
    PendingApprovalStore,
    PromotionAuditLog,
    PromotionDecision,
    PromotionOutcome,
    PromotionRequest,
    PromotionService,
    StageGate,
    WatchResult,
    apply_decision,
    artifact_exists_check,
    evidence_field_check,
    predicate_check,
    reviewer_present_check,
    stage_gate,
    stage_order_check,
)
from arc.core.registry import CatalogEntry, RegistryCatalog, build_catalog

# ── Tollgate (canonical policy engine — sibling package at the repo root) ───
from tollgate import (
    ApprovalOutcome,
    AsyncQueueApprover,
    AutoApprover,
    CliApprover,
    ControlTower,
    InMemoryCircuitBreaker,
    InMemoryGrantStore,
    InMemoryRateLimiter,
    JsonlAuditSink,
    YamlPolicyEvaluator,
)
from tollgate.types import (
    AgentContext,
    AuditEvent,
    Decision,
    DecisionType,
    Effect,
    Intent,
    Outcome,
    ToolRequest,
)


__all__ = [
    # Effects
    "FinancialEffect", "ITSMEffect", "HealthcareEffect", "LegalEffect", "ComplianceEffect",
    "EffectTier", "DefaultDecision", "EffectMeta",
    "EFFECT_METADATA", "ITSM_EFFECT_METADATA", "HEALTHCARE_EFFECT_METADATA",
    "LEGAL_EFFECT_METADATA", "COMPLIANCE_EFFECT_METADATA",
    "effect_meta", "effects_by_tier", "effects_requiring_review",
    # Manifest + scaffold + builder
    "EffectRequestBuilder",
    "AgentManifest", "AgentStatus", "load_manifest", "save_manifest",
    "ManifestStore", "LocalFileManifestStore", "DirectoryManifestStore",
    "BaseAgent",
    "LLMClient", "LLMConfig", "resolve_llm",
    # SLOs (auto-demotion)
    "SLOConfig", "SLORule", "SLOEvaluation", "SLOReport",
    "DemotionMode", "evaluate_slo", "parse_window_seconds",
    # Gateway, memory, tools, observability
    "GatewayConnector", "DataRequest", "DataResponse",
    "MockGatewayConnector", "HttpGateway", "MultiGateway",
    "ConversationBuffer", "Message",
    "AgentMemoryStore", "MemoryEntry", "MemoryBackend",
    "LocalJsonStore", "DynamoDBMemoryBackend",
    "AgentToolRegistry", "ToolRegistry", "governed_tool", "GovernedToolDef",
    "OutcomeTracker", "OutcomeEvent", "generate_report",
    "LifecycleStage", "StageGate", "stage_gate",
    # Promotion pipeline
    "PromotionRequest", "PromotionDecision", "PromotionOutcome",
    "GateCheck", "GateCheckResult", "GateChecker", "PromotionService",
    "apply_decision",
    "stage_order_check", "evidence_field_check", "artifact_exists_check",
    "reviewer_present_check", "predicate_check",
    "PromotionAuditLog", "InMemoryPromotionAuditLog", "JsonlPromotionAuditLog",
    # Pending-approval store
    "PendingApproval", "PendingApprovalStore",
    "InMemoryPendingApprovalStore", "JsonlPendingApprovalStore",
    # Auto-demotion watcher
    "DemotionWatcher", "WatchResult",
    "BreachState", "BreachStateStore",
    "InMemoryBreachStateStore", "JsonlBreachStateStore",
    "DEFAULT_CONSECUTIVE_BREACHES_REQUIRED",
    "DEFAULT_COOLDOWN_HOURS",
    "KILL_SWITCH_ENV",
    "CatalogEntry", "RegistryCatalog", "build_catalog",
    # Tollgate primitives (canonical)
    "ControlTower", "YamlPolicyEvaluator", "JsonlAuditSink",
    "ApprovalOutcome", "AutoApprover", "CliApprover", "AsyncQueueApprover",
    "InMemoryGrantStore", "InMemoryRateLimiter", "InMemoryCircuitBreaker",
    "AuditEvent", "Decision", "DecisionType", "Effect", "Outcome",
    "Intent", "ToolRequest", "AgentContext",
]
