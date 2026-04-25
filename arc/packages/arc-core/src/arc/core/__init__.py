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
from arc.core.manifest import AgentManifest, AgentStatus, load_manifest
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
    FoundryMemoryStore,
    LocalJsonStore,
    MemoryBackend,
    MemoryEntry,
    Message,
)
from arc.core.tools import AgentToolRegistry, GovernedToolDef, ToolRegistry, governed_tool
from arc.core.observability import OutcomeEvent, OutcomeTracker, generate_report
from arc.core.lifecycle import LifecycleStage, StageGate, stage_gate
from arc.core.registry import CatalogEntry, RegistryCatalog, build_catalog

# ── Tollgate (canonical package, vendored copy in foundry/ now shimmed) ──────
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
    "AgentManifest", "AgentStatus", "load_manifest",
    "BaseAgent",
    # Gateway, memory, tools, observability
    "GatewayConnector", "DataRequest", "DataResponse",
    "MockGatewayConnector", "HttpGateway", "MultiGateway",
    "ConversationBuffer", "Message",
    "FoundryMemoryStore", "MemoryEntry", "MemoryBackend",
    "LocalJsonStore", "DynamoDBMemoryBackend",
    "AgentToolRegistry", "ToolRegistry", "governed_tool", "GovernedToolDef",
    "OutcomeTracker", "OutcomeEvent", "generate_report",
    "LifecycleStage", "StageGate", "stage_gate",
    "CatalogEntry", "RegistryCatalog", "build_catalog",
    # Tollgate primitives (canonical)
    "ControlTower", "YamlPolicyEvaluator", "JsonlAuditSink",
    "ApprovalOutcome", "AutoApprover", "CliApprover", "AsyncQueueApprover",
    "InMemoryGrantStore", "InMemoryRateLimiter", "InMemoryCircuitBreaker",
    "AuditEvent", "Decision", "DecisionType", "Effect", "Outcome",
    "Intent", "ToolRequest", "AgentContext",
]
