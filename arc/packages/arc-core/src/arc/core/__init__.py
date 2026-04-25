"""
arc.core — governance engine.

The foundation everything else builds on. Typed effects, policy evaluation,
ControlTower pre-execution gating, and the audit trail.

Re-exports from foundry (the internal implementation package) under the
arc.core namespace. Migrate module by module as the arc codebase matures.

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

# ── Effects ───────────────────────────────────────────────────────────────────
from foundry.policy.effects import (
    FinancialEffect,
    EffectTier,
    DefaultDecision,
    EffectMeta,
    effect_meta,
)
from foundry.policy.itsm_effects import ITSMEffect, ITSM_EFFECT_METADATA
from foundry.policy.healthcare_effects import HealthcareEffect, HEALTHCARE_EFFECT_METADATA
from foundry.policy.legal_effects import LegalEffect, LEGAL_EFFECT_METADATA
from foundry.policy.compliance_effects import ComplianceEffect, COMPLIANCE_EFFECT_METADATA

# ── Policy & Manifest ─────────────────────────────────────────────────────────
from foundry.policy.builder import EffectRequestBuilder
from foundry.scaffold.manifest import AgentManifest, load_manifest
from foundry.scaffold.base import BaseAgent
from foundry.scaffold import load_manifest  # noqa: F811 — convenience re-export

# ── ControlTower ──────────────────────────────────────────────────────────────
from foundry.tollgate import (
    ControlTower,
    YamlPolicyEvaluator,
    JsonlAuditSink,
    ApprovalOutcome,
    AutoApprover,
    CliApprover,
    AsyncQueueApprover,
    InMemoryGrantStore,
    InMemoryRateLimiter,
    InMemoryCircuitBreaker,
)
from foundry.tollgate.types import (
    AuditEvent,
    Decision,
    DecisionType,
    Effect,
    Outcome,
    Intent,
    ToolRequest,
    AgentContext,
)

# ── Observability ─────────────────────────────────────────────────────────────
from foundry.observability.tracker import OutcomeTracker

# ── Gateway ───────────────────────────────────────────────────────────────────
from foundry.gateway.base import (
    GatewayConnector,
    MockGatewayConnector,
    HttpGateway,
    MultiGateway,
    DataRequest,
    DataResponse,
)

__all__ = [
    # Effects
    "FinancialEffect", "ITSMEffect", "HealthcareEffect", "LegalEffect", "ComplianceEffect",
    "EffectTier", "DefaultDecision", "EffectMeta", "effect_meta",
    "ITSM_EFFECT_METADATA", "HEALTHCARE_EFFECT_METADATA", "LEGAL_EFFECT_METADATA",
    "COMPLIANCE_EFFECT_METADATA",
    # Agent
    "BaseAgent",
    "AgentManifest", "load_manifest",
    "EffectRequestBuilder",
    # ControlTower
    "ControlTower", "YamlPolicyEvaluator", "JsonlAuditSink",
    "ApprovalOutcome", "AutoApprover", "CliApprover", "AsyncQueueApprover",
    "InMemoryGrantStore", "InMemoryRateLimiter", "InMemoryCircuitBreaker",
    # Types
    "AuditEvent", "Decision", "DecisionType", "Effect", "Outcome",
    "Intent", "ToolRequest", "AgentContext",
    # Observability
    "OutcomeTracker",
    # Gateway
    "GatewayConnector", "MockGatewayConnector", "HttpGateway",
    "MultiGateway", "DataRequest", "DataResponse",
]
