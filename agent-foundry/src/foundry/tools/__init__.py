"""
foundry.tools
─────────────
Tool registration and governed execution for agent-foundry.

Provides:
  - @governed_tool decorator — attach a FinancialEffect to any async callable
  - ToolRegistry            — register and invoke governed tools from an agent

No LangChain dependency required. Works with any BaseAgent.
"""
from foundry.tools.registry import ToolRegistry, governed_tool, GovernedToolDef

__all__ = ["ToolRegistry", "governed_tool", "GovernedToolDef"]
