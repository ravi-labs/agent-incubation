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
    from arc.orchestrators.langgraph_agent import GraphAgent, FoundryState
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

Checkpointing (conversation memory / multi-turn state):

    from langgraph.checkpoint.memory import MemorySaver

    agent = FiduciaryWatchdogAgent(
        manifest=manifest,
        tower=tower,
        gateway=gateway,
        checkpointer=MemorySaver(),           # in-memory persistence
    )

    # Thread ID scopes the conversation history
    result = await agent.execute(
        fund_id="FUND001",
        plan_id="PLAN001",
        thread_id="session-42",
    )

Streaming state updates:

    # Yield state snapshots after each node completes
    async for snapshot in agent.astream(
        fund_id="FUND001", plan_id="PLAN001"
    ):
        print(snapshot)    # dict with partial state updates from latest node
"""

from __future__ import annotations

import logging
from abc import abstractmethod
from typing import Any, AsyncIterator, Generic, TypeVar

try:
    from langgraph.graph import StateGraph
    from langgraph.graph.state import CompiledStateGraph
except ImportError as exc:
    raise ImportError(
        "LangGraph is not installed. Run: pip install 'agent-foundry[langgraph]'"
    ) from exc

from arc.core.gateway import GatewayConnector
from arc.core.observability import OutcomeTracker
from arc.core import BaseAgent
from arc.core.manifest import AgentManifest
from tollgate.tower import ControlTower

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
        checkpointer: Any | None = None,
    ):
        """
        Args:
            manifest:     Agent manifest (scope, effects, stage).
            tower:        Configured Tollgate ControlTower.
            gateway:      Data access connector.
            tracker:      Optional outcome tracker.
            checkpointer: Optional LangGraph checkpointer for state persistence
                          across multiple runs. Enables multi-turn history and
                          resumable workflows.

                          Examples:
                            from langgraph.checkpoint.memory import MemorySaver
                            checkpointer=MemorySaver()

                            from langgraph.checkpoint.sqlite import SqliteSaver
                            checkpointer=SqliteSaver.from_conn_string("state.db")
        """
        super().__init__(manifest, tower, gateway, tracker)
        self._checkpointer      = checkpointer
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
                return g.compile(checkpointer=self._checkpointer)
        """
        return StateGraph(state_schema)

    @abstractmethod
    def build_graph(self) -> CompiledStateGraph:
        """
        Define the agent's state machine.

        Returns a compiled LangGraph graph. Called once on first run.

        IMPORTANT: Pass self._checkpointer to compile() for persistence:

            def build_graph(self):
                g = self.new_graph(MyState)
                g.add_node("fetch",    self.fetch_data)
                g.add_node("compute",  self.compute_score)
                g.add_node("draft",    self.draft_output)
                g.add_edge(START, "fetch")
                g.add_edge("fetch", "compute")
                g.add_edge("compute", "draft")
                g.add_edge("draft", END)
                return g.compile(checkpointer=self._checkpointer)
        """
        ...

    @staticmethod
    def _build_config(thread_id: str | None) -> dict:
        """Build LangGraph RunnableConfig for checkpointing."""
        if thread_id is None:
            import uuid
            thread_id = str(uuid.uuid4())
        return {"configurable": {"thread_id": thread_id}}

    def _get_graph(self) -> CompiledStateGraph:
        """Return the compiled graph, building it on first call."""
        if self._compiled_graph is None:
            logger.debug("Building graph for agent=%s", self.manifest.agent_id)
            self._compiled_graph = self.build_graph()
        return self._compiled_graph

    async def execute(self, **kwargs: Any) -> Any:
        """
        Runs the LangGraph state machine with kwargs as the initial input.

        The graph receives:  {"input": kwargs, "outputs": [], "errors": []}
        Returns:             The final state dict after all nodes complete.

        Args:
            thread_id: Optional — scopes conversation history when checkpointer
                       is configured. Pass it as a kwarg:
                       await agent.execute(fund_id="...", thread_id="session-42")
            **kwargs:  All other kwargs become the graph's input.
        """
        thread_id = kwargs.pop("thread_id", None)
        graph     = self._get_graph()

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
            "graph_start agent=%s stage=%s thread=%s",
            self.manifest.agent_id,
            self.manifest.lifecycle_stage.value,
            thread_id,
        )

        invoke_kwargs: dict[str, Any] = {}
        if self._checkpointer is not None or thread_id is not None:
            invoke_kwargs["config"] = self._build_config(thread_id)

        final_state = await graph.ainvoke(initial_state, **invoke_kwargs)

        if errors := final_state.get("errors"):
            logger.warning(
                "graph_completed_with_errors agent=%s errors=%s",
                self.manifest.agent_id, errors,
            )
        else:
            logger.info("graph_completed agent=%s", self.manifest.agent_id)

        return final_state

    async def astream(
        self,
        *,
        thread_id: str | None = None,
        stream_mode: str = "updates",
        **kwargs: Any,
    ) -> AsyncIterator[dict]:
        """
        Stream state updates as the graph executes node by node.

        Yields a snapshot after each node completes. In "updates" mode (default),
        each yield is keyed by node name and contains the fields that node changed:

            {"evaluate_fees": {"fee_analysis": {...}, "fee_flag": True}}

        In "values" mode, yields the full accumulated state after each node.

        Args:
            thread_id:   Optional thread ID for checkpointer scoping.
            stream_mode: "updates" (default) — partial state delta per node.
                         "values"  — full state snapshot after each node.
                         "debug"   — verbose LangGraph debug events.
            **kwargs:    Passed as the initial graph input.

        Yields:
            dict: State snapshot or update, depending on stream_mode.

        Usage:
            async for snapshot in agent.astream(
                fund_id="FUND001", plan_id="PLAN001",
            ):
                node_name = next(iter(snapshot))
                print(f"Node '{node_name}' completed")
        """
        graph = self._get_graph()

        initial_state: FoundryState = {
            "input": kwargs,
            "outputs": [],
            "errors": [],
            "_meta": {
                "agent_id": self.manifest.agent_id,
                "version":  self.manifest.version,
            },
        }

        stream_kwargs: dict[str, Any] = {"stream_mode": stream_mode}
        if self._checkpointer is not None or thread_id is not None:
            stream_kwargs["config"] = self._build_config(thread_id)

        logger.info(
            "graph_astream_start agent=%s mode=%s thread=%s",
            self.manifest.agent_id, stream_mode, thread_id,
        )

        async for snapshot in graph.astream(initial_state, **stream_kwargs):
            yield snapshot

        logger.info("graph_astream_complete agent=%s", self.manifest.agent_id)

    async def aget_state(self, thread_id: str) -> Any:
        """
        Retrieve the persisted state for a thread (requires checkpointer).

        Args:
            thread_id: The thread ID whose state to retrieve.

        Returns:
            LangGraph StateSnapshot with .values, .next, .config, .metadata.
        """
        if self._checkpointer is None:
            raise RuntimeError(
                f"GraphAgent '{self.manifest.agent_id}' has no checkpointer configured."
            )
        graph  = self._get_graph()
        config = self._build_config(thread_id)
        return await graph.aget_state(config)

    async def aupdate_state(self, thread_id: str, values: dict) -> None:
        """
        Manually update the persisted state for a thread (requires checkpointer).

        Useful for injecting corrections or human feedback:

            await agent.aupdate_state("session-42", {"finding_severity": "low"})
        """
        if self._checkpointer is None:
            raise RuntimeError(
                f"GraphAgent '{self.manifest.agent_id}' has no checkpointer configured."
            )
        graph  = self._get_graph()
        config = self._build_config(thread_id)
        await graph.aupdate_state(config, values)

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
