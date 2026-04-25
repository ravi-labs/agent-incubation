"""
Migrated to arc.core.policy.builder (see docs/migration-plan.md, module 2).

Thin re-export shim so existing `from foundry.policy.builder import …`
imports keep working. New code should import from arc.core directly.
"""

from arc.core.policy.builder import EffectRequestBuilder

__all__ = ["EffectRequestBuilder"]
