"""
foundry.tools
─────────────
Tool registration and governed execution for agent-foundry.

Provides:
  - @governed_tool      decorator — attach a FinancialEffect to any async callable
  - AgentToolRegistry             — register and invoke governed tools from an agent
  - ToolRegistry                  — backward-compatible alias for AgentToolRegistry

Note: Use ``AgentToolRegistry`` in new code to avoid shadowing
``foundry.tollgate.ToolRegistry`` (Tollgate's internal resource registry).

No LangChain dependency required. Works with any BaseAgent.
"""
from foundry.tools.registry import AgentToolRegistry, ToolRegistry, governed_tool, GovernedToolDef

__all__ = ["AgentToolRegistry", "ToolRegistry", "governed_tool", "GovernedToolDef"]
