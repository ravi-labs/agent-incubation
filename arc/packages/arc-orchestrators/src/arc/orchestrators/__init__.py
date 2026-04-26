"""
arc.orchestrators — pluggable agent execution frameworks.

Provides a common OrchestratorProtocol so agent code never imports
LangGraph, AgentCore, or Strands directly. Swap the orchestrator
in the builder — not in the agent.

Available orchestrators:
    LangGraphOrchestrator  — LangGraph StateGraph + Bedrock LLM
    AgentCoreOrchestrator  — AWS Bedrock AgentCore runtime
    StrandsOrchestrator    — AWS Strands (stub)

Usage:
    from arc.orchestrators import LangGraphOrchestrator
    from arc.orchestrators.langgraph import build_email_triage_graph

    orchestrator = LangGraphOrchestrator(
        graph=build_email_triage_graph(agent),
        checkpointer=MemorySaver(),
    )

    # Inject into agent via HarnessBuilder or RuntimeBuilder:
    agent = HarnessBuilder(...).with_orchestrator(orchestrator).build(EmailTriageAgent)
"""

from .protocol import OrchestratorProtocol, OrchestratorResult

# Lazy imports — only available if optional deps installed
def __getattr__(name: str):
    # Native arc orchestrators
    if name == "LangGraphOrchestrator":
        from .langgraph import LangGraphOrchestrator
        return LangGraphOrchestrator
    if name == "AgentCoreOrchestrator":
        from .agentcore import AgentCoreOrchestrator
        return AgentCoreOrchestrator
    if name == "StrandsOrchestrator":
        from .strands import StrandsOrchestrator
        return StrandsOrchestrator
    # LangChain bridge
    if name in ("ArcTool", "ArcToolkit", "ArcRunnable"):
        from . import langchain
        return getattr(langchain, name)
    if name in ("GraphAgent", "AgentState"):
        from . import langgraph_agent
        return getattr(langgraph_agent, name)
    raise AttributeError(f"module 'arc.orchestrators' has no attribute {name!r}")

__all__ = [
    "OrchestratorProtocol",
    "OrchestratorResult",
    "LangGraphOrchestrator",
    "AgentCoreOrchestrator",
    "StrandsOrchestrator",
    # LangChain bridge
    "ArcTool", "ArcToolkit", "ArcRunnable",  # langchain.py
    "GraphAgent", "AgentState",              # langgraph_agent.py
]
