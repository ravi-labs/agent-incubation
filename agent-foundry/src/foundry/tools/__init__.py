"""
Migrated to arc.core.tools (see docs/migration-plan.md, module 8).

Thin re-export shim so existing `from foundry.tools import …` keeps working.
New code should import from arc.core directly.
"""

from arc.core.tools import AgentToolRegistry, GovernedToolDef, ToolRegistry, governed_tool

__all__ = ["AgentToolRegistry", "ToolRegistry", "governed_tool", "GovernedToolDef"]
