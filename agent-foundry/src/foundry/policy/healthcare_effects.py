"""
Migrated to arc.core.effects.healthcare (see docs/migration-plan.md, module 1).

Thin re-export shim so existing `from foundry.policy.healthcare_effects import …`
imports keep working. New code should import from arc.core.effects directly.
"""

from arc.core.effects.healthcare import HEALTHCARE_EFFECT_METADATA, HealthcareEffect

__all__ = ["HealthcareEffect", "HEALTHCARE_EFFECT_METADATA"]
