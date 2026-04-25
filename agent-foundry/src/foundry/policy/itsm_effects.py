"""
Migrated to arc.core.effects.itsm (see docs/migration-plan.md, module 1).

Thin re-export shim so existing `from foundry.policy.itsm_effects import …`
imports keep working. New code should import from arc.core.effects directly.
"""

from arc.core.effects.itsm import ITSM_EFFECT_METADATA, ITSMEffect

__all__ = ["ITSMEffect", "ITSM_EFFECT_METADATA"]
