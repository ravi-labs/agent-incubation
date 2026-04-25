"""
Migrated to arc.core.effects.compliance (see docs/migration-plan.md, module 1).

Thin re-export shim so existing `from foundry.policy.compliance_effects import …`
imports keep working. New code should import from arc.core.effects directly.
"""

from arc.core.effects.compliance import COMPLIANCE_EFFECT_METADATA, ComplianceEffect

__all__ = ["ComplianceEffect", "COMPLIANCE_EFFECT_METADATA"]
