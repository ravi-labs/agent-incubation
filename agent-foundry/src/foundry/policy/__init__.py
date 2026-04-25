"""
Foundry Policy Layer — extends Tollgate with typed effect taxonomies.

Includes:
  - FinancialEffect  — ERISA / financial-services agents
  - HealthcareEffect — HIPAA / clinical agents
  - LegalEffect      — legal-services agents (contract review, compliance, discovery)
"""

from .builder import EffectRequestBuilder
from .effects import (
    DefaultDecision,
    EFFECT_METADATA,
    EffectMeta,
    EffectTier,
    FinancialEffect,
    effect_meta,
    effects_by_tier,
    effects_requiring_review,
)
from .healthcare_effects import HealthcareEffect, HEALTHCARE_EFFECT_METADATA
from .legal_effects import LegalEffect, LEGAL_EFFECT_METADATA

__all__ = [
    # Shared primitives
    "EffectTier",
    "EffectMeta",
    "DefaultDecision",
    "EffectRequestBuilder",
    # Financial
    "FinancialEffect",
    "EFFECT_METADATA",
    "effect_meta",
    "effects_by_tier",
    "effects_requiring_review",
    # Healthcare
    "HealthcareEffect",
    "HEALTHCARE_EFFECT_METADATA",
    # Legal
    "LegalEffect",
    "LEGAL_EFFECT_METADATA",
]
