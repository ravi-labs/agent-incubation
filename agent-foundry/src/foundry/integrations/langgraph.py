"""
foundry.integrations.langgraph
───────────────────────────────
LangGraph integration for agent-foundry.

Provides GraphAgent — a BaseAgent subclass that uses a LangGraph StateGraph
as the execution engine inside execute(). All effects still go through
run_effect(), so every state transition is policy-enforced and audit-logged.

Install:
    pip install "agent-foundry[langgraph]"

Usage:
    from foundry.integrations.langgraph import GraphAgent, FoundryState
    from langgraph.graph import START, END
    from typing import TypedDict

    class WatchdogState(FoundryState):
        fund_id: str
        fee_analysis: dict | None
        performance_analysis: dict | None
        finding_level: str | None   # "none" | "low" | "high"

    class FiduciaryWatchdogAgent(GraphAgent[WatchdogState]):

        def build_graph(self):
            g = self.new_graph(WatchdogState)

            g.add_node("evaluate_fees",        self.evaluate_fees)
            g.add_node("evaluate_performance", self.evaluate_performance)
            g.add_node("emit_low_finding",     self.emit_low_finding)
            g.add_node("queue_high_finding",   self.queue_high_finding)

            g.add_edge(START, "evaluate_fees")
            g.add_edge("evaluate_fees", "evaluate_performance")
            g.add_conditional_edges(
                "evaluate_performance",
                self.route_finding,
                {"low": "emit_low_finding", "high": "queue_high_finding", "none": END},
            )
            g.add_edge("emit_low_finding",   END)
            g.add_edge("queue_high_finding", END)
            return g.compile()

        async def evaluate_fees(self, state: WatchdogState) -> dict:
            result = await self.run_effect(
                effect=FinancialEffect.FUND_FEES_READ,
                tool="fund_data", action="fees",
                params={"fund_id": state["fund_id"]},
                intent_action="evaluate_fees",
                intent_reason="Compare fund expense ratio to category average",
            )
            return {"fee_analysis": result}

        def route_finding(self, state: WatchdogState) -> str:
            level = state.get("finding_level", "none")
            return level if level in ("low", "high") else "none"
"""

from __future__ import annotations

import logging
from abc import abstractmethod
from typing import Any, Generic, TypeVar

try:
    from langgraph.graph import StateGraph
    from langgraph.graph.state import CompiledStateGraph
except ImportError as exc:
    raise ImportError(
        "LangGraph is not installed. Run: pip install 'agent-foundry[langgraph]'"
    ) from exc

from foundry.gateway.base import GatewayConnector
from foundry.observability.tracker import OutcomeTracker
from foundry.scaffold.base import BaseAgent
from foundry.scaffold.manifest import AgentManifest
from foundry.tollgate.tower import ControlTower

logger = logging.getLogger(__name__)

# ── State ──────────────────────────────────────────────────────────────────────

from typing import TypedDict


class FoundryState(TypedDict, total=False):
    """
    Base state for all GraphAgent state machines.

    Extend this with your agent's domain-specific fields:

        class MyAgentState(FoundryState):
            participant_id: str
            risk_score: float | None
            intervention_draft: str | None
    """
    # Input provided to agent.run()
    input: dict

    # Collected outputs and findings across all nodes
    outputs: list

    # Any non-fatal errors encountered during execution
    errors: list

    # Propagated metadata (agent_id, version, run_id)
    _meta: dict


# Generic type variable bound to FoundryState
S = TypeVar("S", bound=FoundryState)


# ── GraphAgent ─────────────────────────────────────────────────────────────────

class GraphAgent(BaseAgent, Generic[S]):
    """
    A BaseAgent that uses a LangGraph StateGraph as its execution engine.

    Why GraphAgent instead of plain BaseAgent?
    - Your agent has conditional branching (route to human review OR auto-emit)
    - Your agent has multiple sequential steps that share state
    - You want retry logic or loops (e.g., re-score after data refresh)
    - You want clear visibility into which node is executing for debugging

    All effects still go through run_effect() — policy enforcement is unchanged.
    LangGraph controls the *flow*; Tollgate controls the *permissions*.

    Abstract method to implement:
        build_graph() -> CompiledStateGraph
            Define nodes, edges, and conditional routing.
            Use self.new_graph(StateClass) to get a pre-configured StateGraph.

    Nodes are typically methods on your subclass:
        async def my_node(self, state: MyState) -> dict:
            result = await self.run_effect(...)   # enforced
            return {"my_field": result}           # partial state update
    """

    def __init__(
        self,
        manifest: AgentManifest,
        tower: ControlTower,
        gateway: GatewayConnector,
        tracker: OutcomeTracker | None = None,
    ):
        super().__init__(manifest, tower, gateway, tracker)
        self._compiled_graph: CompiledStateGraph | None = None

    # ── Public API ─────────────────────────────────────────────────────────────

    def new_graph(self, state_schema: type[S]) -> StateGraph:
        """
        Create a StateGraph bound to the given state schema.

        Use this inside build_graph() rather than constructing StateGraph directly:

            def build_graph(self):
                g = self.new_graph(MyState)
                g.add_node("step_one", self.step_one)
                ...
                return g.compile()
        """
        return StateGraph(state_schema)

    @abstractmethod
    def build_graph(self) -> CompiledStateGraph:
        """
        Define the agent's state machine.

        Returns a compiled LangGraph graph. Called once on first run.

        Example:
            def build_graph(self):
                g = self.new_graph(MyState)
                g.add_node("fetch",    self.fetch_data)
                g.add_node("compute",  self.compute_score)
                g.add_node("draft",    self.draft_output)
                g.add_edge(START, "fetch")
                g.add_edge("fetch", "compute")
                g.add_edge("compute", "draft")
                g.add_edge("draft", END)
                return g.compile()
        """
        ...

    async def execute(self, **kwargs: Any) -> Any:
        """
        Runs the LangGraph state machine with kwargs as the initial input.

        The graph receives:  {"input": kwargs, "outputs": [], "errors": []}
        Returns:             The final state dict after all nodes complete.
        """
        if self._compiled_graph is None:
            logger.debug("Building graph for agent=%s", self.manifest.agent_id)
            self._compiled_graph = self.build_graph()

        initial_state: FoundryState = {
            "input": kwargs,
            "outputs": [],
            "errors": [],
            "_meta": {
                "agent_id": self.manifest.agent_id,
                "version":  self.manifest.version,
            },
        }

        logger.info(
            "graph_start agent=%s stage=%s",
            self.manifest.agent_id,
            self.manifest.lifecycle_stage.value,
        )

        final_state = await self._compiled_graph.ainvoke(initial_state)

        if errors := final_state.get("errors"):
            logger.warning(
                "graph_completed_with_errors agent=%s errors=%s",
                self.manifest.agent_id, errors,
            )
        else:
            logger.info("graph_completed agent=%s", self.manifest.agent_id)

        return final_state

    # ── Node helpers ───────────────────────────────────────────────────────────

    def append_output(self, state: FoundryState, output: Any) -> dict:
        """
        Helper for nodes that produce outputs — appends to the outputs list.

        Usage in a node:
            async def emit_finding(self, state: MyState) -> dict:
                finding = {...}
                await self.run_effect(effect=FinancialEffect.COMPLIANCE_FINDING_EMIT_LOW, ...)
                return self.append_output(state, finding)
        """
        existing = list(state.get("outputs", []))
        existing.append(output)
        return {"outputs": existing}

    def append_error(self, state: FoundryState, error: str) -> dict:
        """
        Helper for nodes that encounter non-fatal errors — continues execution.

        Usage in a node:
            except SomeExpectedError as e:
                return self.append_error(state, str(e))
        """
        existing = list(state.get("errors", []))
        existing.append(error)
        return {"errors": existing}


# ── Convenience re-exports ─────────────────────────────────────────────────────

try:
    from langgraph.graph import END, START
    __all__ = ["GraphAgent", "FoundryState", "START", "END"]
except ImportError:
    __all__ = ["GraphAgent", "FoundryState"]
