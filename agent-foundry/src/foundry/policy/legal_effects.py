"""
Migrated to arc.core.effects.legal (see docs/migration-plan.md, module 1).

Thin re-export shim so existing `from foundry.policy.legal_effects import …`
imports keep working. New code should import from arc.core.effects directly.
"""

from arc.core.effects.legal import LEGAL_EFFECT_METADATA, LegalEffect

__all__ = ["LegalEffect", "LEGAL_EFFECT_METADATA"]
