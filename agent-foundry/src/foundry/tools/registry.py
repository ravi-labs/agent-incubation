"""Migrated to arc.core.tools.registry. Thin re-export shim."""

from arc.core.tools.registry import (
    AgentToolRegistry,
    GovernedToolDef,
    ToolRegistry,
    governed_tool,
)

__all__ = ["AgentToolRegistry", "ToolRegistry", "governed_tool", "GovernedToolDef"]
