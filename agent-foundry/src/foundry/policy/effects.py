"""
Migrated to arc.core.effects (see docs/migration-plan.md, module 1).

This module is now a thin re-export shim so existing
`from foundry.policy.effects import …` imports keep working. New code
should import from arc.core.effects directly.
"""

from arc.core.effects.base import DefaultDecision, EffectMeta, EffectTier
from arc.core.effects.financial import (
    EFFECT_METADATA,
    FinancialEffect,
    effects_by_tier,
    effects_requiring_review,
)
from arc.core.effects import effect_meta

__all__ = [
    "DefaultDecision",
    "EffectMeta",
    "EffectTier",
    "FinancialEffect",
    "EFFECT_METADATA",
    "effect_meta",
    "effects_by_tier",
    "effects_requiring_review",
]
