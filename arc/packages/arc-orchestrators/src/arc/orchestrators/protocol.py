"""
OrchestratorProtocol — the swap interface.

Every orchestrator (LangGraph, AgentCore, Strands) implements this
two-method protocol. Agent code calls self.orchestrator.run() or
self.orchestrator.stream() — never imports the framework directly.

Swapping from LangGraph to AgentCore is one line in RuntimeBuilder:
    .with_orchestrator(AgentCoreOrchestrator(...))
"""

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Protocol, runtime_checkable


@dataclass
class OrchestratorResult:
    """
    Standardised result from any orchestrator run.

    Wraps the raw framework output so agent code never touches
    LangGraph State objects, AgentCore responses, etc. directly.
    """
    output:    dict[str, Any]           # final output dict
    state:     dict[str, Any]           # final internal state (for debugging)
    run_id:    str = ""                 # framework-specific run identifier
    metadata:  dict[str, Any] = field(default_factory=dict)

    # Convenience accessors
    def get(self, key: str, default: Any = None) -> Any:
        return self.output.get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self.output[key]


@runtime_checkable
class OrchestratorProtocol(Protocol):
    """
    Protocol every Arc orchestrator must implement.

    The two methods mirror LangGraph's invoke/stream interface but
    are framework-agnostic — AgentCore and Strands wrap their own
    APIs to match this contract.

    Inputs:
        input:  The initial state / input dict for the run.
                For EmailTriageAgent: {"email": {...}, "run_id": "..."}
        config: Optional run-level config (thread_id, recursion_limit, etc.)

    ASK handling:
        When ControlTower returns ASK, the orchestrator suspends
        (LangGraph: interrupt(), AgentCore: session pause) and raises
        OrchestratorSuspended. The caller queues the approval request
        and calls resume() when the decision arrives.
    """

    async def run(
        self,
        input: dict[str, Any],
        config: dict[str, Any] | None = None,
    ) -> OrchestratorResult:
        """Execute the full agent pipeline and return the final result."""
        ...

    async def stream(
        self,
        input: dict[str, Any],
        config: dict[str, Any] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream intermediate outputs as the pipeline executes."""
        ...

    async def resume(
        self,
        thread_id: str,
        approval: dict[str, Any],
        config: dict[str, Any] | None = None,
    ) -> OrchestratorResult:
        """
        Resume a suspended run after ASK approval arrives.

        Args:
            thread_id: The run identifier returned when the orchestrator suspended.
            approval:  The approval decision from the human reviewer.
            config:    Optional run-level config.
        """
        ...


class OrchestratorSuspended(Exception):
    """
    Raised when a run suspends waiting for human approval (ASK decision).

    Attributes:
        thread_id:    Use this to resume the run once approved.
        pending_effect: The effect that triggered the ASK.
        reason:       Human-readable reason for the suspension.
    """

    def __init__(self, thread_id: str, pending_effect: str, reason: str):
        self.thread_id      = thread_id
        self.pending_effect = pending_effect
        self.reason         = reason
        super().__init__(
            f"Run {thread_id} suspended — {pending_effect} requires approval: {reason}"
        )
