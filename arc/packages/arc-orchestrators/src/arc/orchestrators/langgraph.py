"""
LangGraphOrchestrator — runs Arc agents on LangGraph.

Wraps a compiled LangGraph StateGraph and exposes the OrchestratorProtocol
interface. Agent code never imports LangGraph directly.

Key design points:
  - ASK decisions map to LangGraph interrupt() — graph suspends, state checkpointed
  - Resumption via resume() restores graph state and continues from the suspension point
  - AgentCore Memory API can be used as the checkpointer for production persistence
  - Bedrock (Claude) is the default LLM — swap via llm= parameter

Usage:
    from arc.orchestrators import LangGraphOrchestrator
    from arc.orchestrators.langgraph import build_email_triage_graph

    graph = build_email_triage_graph(governed_tools)
    orchestrator = LangGraphOrchestrator(graph=graph)

    result = await orchestrator.run({"email": {...}})
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from .protocol import OrchestratorProtocol, OrchestratorResult, OrchestratorSuspended


@dataclass
class LangGraphOrchestrator:
    """
    Arc orchestrator backed by LangGraph.

    Args:
        graph:        A compiled LangGraph StateGraph (CompiledGraph).
        checkpointer: LangGraph checkpointer for state persistence.
                      Use MemorySaver() for harness, AgentCore Memory API for prod.
        config:       Default run config (recursion_limit, etc.)
    """

    graph:        Any                        # CompiledGraph — typed Any to avoid hard import
    checkpointer: Any = None                 # MemorySaver | AsyncPostgresSaver | AgentCore
    default_config: dict[str, Any] = field(default_factory=dict)

    async def run(
        self,
        input: dict[str, Any],
        config: dict[str, Any] | None = None,
    ) -> OrchestratorResult:
        """
        Invoke the LangGraph graph and return the final state.

        Maps LangGraph GraphInterrupt (ASK decision) to OrchestratorSuspended
        so callers don't need to know LangGraph internals.
        """
        run_config = self._build_config(config)
        thread_id  = run_config.get("configurable", {}).get("thread_id", str(uuid.uuid4()))

        try:
            # LangGraph invoke — runs until completion or interrupt
            final_state = await self.graph.ainvoke(input, config=run_config)

            return OrchestratorResult(
                output   = self._extract_output(final_state),
                state    = dict(final_state) if hasattr(final_state, "__iter__") else {},
                run_id   = thread_id,
                metadata = {"framework": "langgraph"},
            )

        except Exception as e:
            # Map LangGraph's GraphInterrupt to OrchestratorSuspended
            if self._is_interrupt(e):
                effect, reason = self._parse_interrupt(e)
                raise OrchestratorSuspended(
                    thread_id      = thread_id,
                    pending_effect = effect,
                    reason         = reason,
                ) from e
            raise

    async def stream(
        self,
        input: dict[str, Any],
        config: dict[str, Any] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """
        Stream intermediate state updates as the graph executes.

        Yields one dict per node completion — useful for real-time
        dashboard updates showing triage progress.
        """
        run_config = self._build_config(config)
        async for event in self.graph.astream(input, config=run_config):
            yield event

    async def resume(
        self,
        thread_id: str,
        approval: dict[str, Any],
        config: dict[str, Any] | None = None,
    ) -> OrchestratorResult:
        """
        Resume a suspended graph after ASK approval.

        LangGraph resumes by calling ainvoke(None) on the checkpointed
        thread — the interrupt() returns the approval dict and the graph
        continues from the suspension point.
        """
        run_config = self._build_config(config)
        run_config.setdefault("configurable", {})["thread_id"] = thread_id

        final_state = await self.graph.ainvoke(
            # None = resume from checkpoint, pass approval as Command
            None,
            config=run_config,
        )

        return OrchestratorResult(
            output   = self._extract_output(final_state),
            state    = dict(final_state) if hasattr(final_state, "__iter__") else {},
            run_id   = thread_id,
            metadata = {"framework": "langgraph", "resumed": True, "approval": approval},
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _build_config(self, override: dict[str, Any] | None) -> dict[str, Any]:
        cfg = dict(self.default_config)
        if override:
            cfg.update(override)
        # Ensure thread_id exists for checkpointer
        cfg.setdefault("configurable", {}).setdefault("thread_id", str(uuid.uuid4()))
        cfg.setdefault("recursion_limit", 50)
        return cfg

    @staticmethod
    def _is_interrupt(exc: Exception) -> bool:
        """Check if an exception is a LangGraph GraphInterrupt.

        Detects both real LangGraph GraphInterrupt/NodeInterrupt by class name
        AND the attribute-pattern used in tests and future LangGraph versions.
        """
        if type(exc).__name__ in ("GraphInterrupt", "NodeInterrupt"):
            return True
        # Detect by attribute pattern: LangGraph interrupts carry a .value dict
        # with an 'effect' key. This also handles test mocks.
        value = getattr(exc, "value", None)
        if isinstance(value, dict) and "effect" in value:
            return True
        return False

    @staticmethod
    def _parse_interrupt(exc: Exception) -> tuple[str, str]:
        """Extract effect and reason from a GraphInterrupt."""
        value = getattr(exc, "value", None) or {}
        if isinstance(value, dict):
            return value.get("effect", "unknown"), value.get("reason", str(exc))
        return "unknown", str(exc)

    @staticmethod
    def _extract_output(state: Any) -> dict[str, Any]:
        """Extract the output dict from a LangGraph final state."""
        if isinstance(state, dict):
            return state
        if hasattr(state, "__dict__"):
            return vars(state)
        return {}
