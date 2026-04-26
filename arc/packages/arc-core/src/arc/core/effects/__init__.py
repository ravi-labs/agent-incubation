"""
arc.core.effects — typed effect taxonomies for every supported domain.

Effects are the atomic actions an agent can take. Every agent tool call must
declare an effect, which determines the default ALLOW/ASK/DENY decision and
how it appears in the audit trail.

Five domain taxonomies are bundled:

  - FinancialEffect    ERISA / financial-services
  - HealthcareEffect   HIPAA / clinical
  - LegalEffect        legal-services (contracts, redlines, discovery)
  - ITSMEffect         IT service management (Pega, ServiceNow, Jira)
  - ComplianceEffect   regulatory (filings, audits, violations)

Each domain ships its own enum and a metadata dict. Use `effect_meta(effect)`
to look up metadata for any effect across any domain — it dispatches by enum
type so values that happen to collide between domains stay safely separated.
"""

from enum import Enum

from .base import DefaultDecision, EffectMeta, EffectTier
from .compliance import COMPLIANCE_EFFECT_METADATA, ComplianceEffect
from .financial import EFFECT_METADATA, FinancialEffect
from .healthcare import HEALTHCARE_EFFECT_METADATA, HealthcareEffect
from .itsm import ITSM_EFFECT_METADATA, ITSMEffect
from .legal import LEGAL_EFFECT_METADATA, LegalEffect

# Registry indexed by enum class name. New domain taxonomies are added here.
_REGISTRIES: dict[str, dict] = {
    "FinancialEffect":  EFFECT_METADATA,
    "HealthcareEffect": HEALTHCARE_EFFECT_METADATA,
    "LegalEffect":      LEGAL_EFFECT_METADATA,
    "ITSMEffect":       ITSM_EFFECT_METADATA,
    "ComplianceEffect": COMPLIANCE_EFFECT_METADATA,
}


def effect_meta(effect: Enum) -> EffectMeta:
    """Return metadata for an effect from any registered domain taxonomy.

    Lookup is type-aware: two enums in different domains can share a string
    value without colliding, because we match on `type(effect).__name__` AND
    enum identity.
    """
    cls_name = type(effect).__name__
    registry = _REGISTRIES.get(cls_name)
    if registry is None:
        raise KeyError(
            f"Unknown effect type {cls_name!r}. Register its metadata dict "
            f"in arc.core.effects._REGISTRIES."
        )
    for k, v in registry.items():
        if type(k).__name__ == cls_name and k == effect:
            return v
    raise KeyError(
        f"No EffectMeta registered for {effect!r} (type {cls_name}). "
        f"Add it to {cls_name}'s metadata dict."
    )


def effects_by_tier(tier: EffectTier) -> list[Enum]:
    """Return every effect in the given tier across every registered domain.

    Iterates `_REGISTRIES` so the result includes FinancialEffect,
    HealthcareEffect, LegalEffect, ITSMEffect, and ComplianceEffect values
    that match the tier — not just one domain.
    """
    out: list[Enum] = []
    for registry in _REGISTRIES.values():
        out.extend(e for e, m in registry.items() if m.tier == tier)
    return out


def effects_requiring_review() -> list[Enum]:
    """Return every effect whose default is to require human review.

    Cross-domain — see ``effects_by_tier`` for the same iteration pattern.
    """
    out: list[Enum] = []
    for registry in _REGISTRIES.values():
        out.extend(e for e, m in registry.items() if m.requires_human_review)
    return out


__all__ = [
    # Shared primitives
    "EffectTier", "DefaultDecision", "EffectMeta",
    # Per-domain enums + metadata
    "FinancialEffect",  "EFFECT_METADATA",
    "HealthcareEffect", "HEALTHCARE_EFFECT_METADATA",
    "LegalEffect",      "LEGAL_EFFECT_METADATA",
    "ITSMEffect",       "ITSM_EFFECT_METADATA",
    "ComplianceEffect", "COMPLIANCE_EFFECT_METADATA",
    # Helpers
    "effect_meta", "effects_by_tier", "effects_requiring_review",
]
