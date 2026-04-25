"""
arc.core.tools — tool registration and governed execution.

Provides:
  - @governed_tool       decorator — attach a typed Effect to any async callable
  - AgentToolRegistry    — register and invoke governed tools from an agent
  - ToolRegistry         — backward-compatible alias for AgentToolRegistry

No LangChain dependency required. Works with any BaseAgent.
"""

from .registry import AgentToolRegistry, GovernedToolDef, ToolRegistry, governed_tool

__all__ = ["AgentToolRegistry", "ToolRegistry", "governed_tool", "GovernedToolDef"]
