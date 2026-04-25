"""Migrated to arc.orchestrators.langgraph_agent. Thin re-export shim.

Renamed during migration to avoid collision with arc.orchestrators.langgraph
(which is the OrchestratorProtocol implementation, a different concept).
"""

from arc.orchestrators.langgraph_agent import FoundryState, GraphAgent

__all__ = ["GraphAgent", "FoundryState"]
