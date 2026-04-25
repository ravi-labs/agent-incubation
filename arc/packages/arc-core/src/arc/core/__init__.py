"""
arc.core — governance engine.

The foundation everything else builds on. Typed effects, policy evaluation,
ControlTower pre-execution gating, and the audit trail.

Effects are native (migration module 1, complete). Other surfaces
(BaseAgent, AgentManifest, ControlTower, gateway, observability) are still
re-exported from agent-foundry pending their migration. See
docs/migration-plan.md.

Public API:
    from arc.core import (
        BaseAgent,
        AgentManifest, load_manifest,
        ControlTower,
        YamlPolicyEvaluator,
        JsonlAuditSink,
        ITSMEffect, FinancialEffect, ComplianceEffect,
    )

Implementation note: foundry re-exports are loaded lazily via PEP 562
``__getattr__`` so a partial-import path through ``foundry.policy.*`` (which
shims back to ``arc.core.effects``) cannot trigger a circular dependency at
package init time. Once those modules migrate, the lazy table shrinks and
eventually goes away with the foundry dep itself.
"""

from typing import Any

# ── Native arc-core (already migrated from foundry) ──────────────────────────
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

# ── Foundry-backed re-exports (lazy until each module migrates) ──────────────
# Map from public attribute name → (foundry module path, attribute in that module).
_LAZY_FOUNDRY_EXPORTS: dict[str, tuple[str, str]] = {
    # Manifest & scaffold
    "AgentManifest":        ("foundry.scaffold.manifest", "AgentManifest"),
    "load_manifest":        ("foundry.scaffold.manifest", "load_manifest"),
    "BaseAgent":            ("foundry.scaffold.base", "BaseAgent"),
    # ControlTower
    "ControlTower":          ("foundry.tollgate", "ControlTower"),
    "YamlPolicyEvaluator":   ("foundry.tollgate", "YamlPolicyEvaluator"),
    "JsonlAuditSink":        ("foundry.tollgate", "JsonlAuditSink"),
    "ApprovalOutcome":       ("foundry.tollgate", "ApprovalOutcome"),
    "AutoApprover":          ("foundry.tollgate", "AutoApprover"),
    "CliApprover":           ("foundry.tollgate", "CliApprover"),
    "AsyncQueueApprover":    ("foundry.tollgate", "AsyncQueueApprover"),
    "InMemoryGrantStore":    ("foundry.tollgate", "InMemoryGrantStore"),
    "InMemoryRateLimiter":   ("foundry.tollgate", "InMemoryRateLimiter"),
    "InMemoryCircuitBreaker": ("foundry.tollgate", "InMemoryCircuitBreaker"),
    # Tollgate types
    "AuditEvent":   ("foundry.tollgate.types", "AuditEvent"),
    "Decision":     ("foundry.tollgate.types", "Decision"),
    "DecisionType": ("foundry.tollgate.types", "DecisionType"),
    "Effect":       ("foundry.tollgate.types", "Effect"),
    "Outcome":      ("foundry.tollgate.types", "Outcome"),
    "Intent":       ("foundry.tollgate.types", "Intent"),
    "ToolRequest":  ("foundry.tollgate.types", "ToolRequest"),
    "AgentContext": ("foundry.tollgate.types", "AgentContext"),
    # Observability
    "OutcomeTracker": ("foundry.observability.tracker", "OutcomeTracker"),
    # Gateway
    "GatewayConnector":     ("foundry.gateway.base", "GatewayConnector"),
    "MockGatewayConnector": ("foundry.gateway.base", "MockGatewayConnector"),
    "HttpGateway":          ("foundry.gateway.base", "HttpGateway"),
    "MultiGateway":         ("foundry.gateway.base", "MultiGateway"),
    "DataRequest":          ("foundry.gateway.base", "DataRequest"),
    "DataResponse":         ("foundry.gateway.base", "DataResponse"),
}


def __getattr__(name: str) -> Any:
    """PEP 562 lazy attribute access for foundry-backed re-exports."""
    target = _LAZY_FOUNDRY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_path, attr = target
    from importlib import import_module
    module = import_module(module_path)
    value = getattr(module, attr)
    globals()[name] = value  # cache so subsequent access skips this dispatch
    return value


def __dir__() -> list[str]:
    """Make tab-completion and dir() see the lazy names."""
    return sorted({*globals(), *_LAZY_FOUNDRY_EXPORTS})


__all__ = [
    # Native (migrated)
    "FinancialEffect", "ITSMEffect", "HealthcareEffect", "LegalEffect", "ComplianceEffect",
    "EffectTier", "DefaultDecision", "EffectMeta",
    "EFFECT_METADATA", "ITSM_EFFECT_METADATA", "HEALTHCARE_EFFECT_METADATA",
    "LEGAL_EFFECT_METADATA", "COMPLIANCE_EFFECT_METADATA",
    "effect_meta", "effects_by_tier", "effects_requiring_review",
    "EffectRequestBuilder",
    # Foundry-backed (lazy, awaiting migration)
    *_LAZY_FOUNDRY_EXPORTS,
]
