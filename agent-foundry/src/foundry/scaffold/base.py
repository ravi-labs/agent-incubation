"""
Migrated to arc.core.agent (see docs/migration-plan.md, module 4).

Thin re-export shim so existing `from foundry.scaffold.base import BaseAgent`
imports keep working. New code should import from arc.core directly.
"""

from arc.core.agent import BaseAgent

__all__ = ["BaseAgent"]
