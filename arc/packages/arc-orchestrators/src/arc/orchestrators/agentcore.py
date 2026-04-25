"""
AgentCoreOrchestrator — runs Arc agents on AWS Bedrock AgentCore.

Uses AgentCore as the deployment runtime (Memory API, Sessions API,
managed infrastructure) while keeping arc.core as the governance layer.

AgentCore role:  Memory, Sessions, observability, managed ECS deployment.
arc.core role:   Typed effects, ControlTower, policy, audit trail.

Do NOT use AgentCore Cedar policies — arc.core typed taxonomies are the
governance layer. Cedar would replace your domain-specific hard-denies.

Usage:
    from arc.orchestrators import AgentCoreOrchestrator

    orchestrator = AgentCoreOrchestrator(
        agent_id   = "email-triage-v1",
        region     = "us-east-1",
        memory_id  = "mem-abc123",   # AgentCore Memory API
    )
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from .protocol import OrchestratorProtocol, OrchestratorResult, OrchestratorSuspended


@dataclass
class AgentCoreOrchestrator:
    """
    Arc orchestrator backed by AWS Bedrock AgentCore.

    Wraps a LangGraph graph deployed on AgentCore infrastructure.
    AgentCore handles: Memory API (conversation history, entity tracking),
    Sessions API (long-running state), CloudWatch observability.

    Args:
        agent_id:   The AgentCore agent identifier.
        region:     AWS region (default: us-east-1).
        memory_id:  AgentCore Memory API ID for cross-session persistence.
        graph:      Optional LangGraph graph — if provided, runs locally via
                    AgentCore's LangGraph executor. If None, calls AgentCore
                    invoke API (fully managed).
    """

    agent_id:   str
    region:     str = "us-east-1"
    memory_id:  str | None = None
    graph:      Any = None                   # CompiledGraph (optional)
    _client:    Any = field(default=None, init=False, repr=False)

    def __post_init__(self):
        try:
            import boto3
            self._client = boto3.client("bedrock-agent-runtime", region_name=self.region)
        except ImportError:
            raise ImportError(
                "boto3 is required for AgentCoreOrchestrator: "
                "pip install arc-orchestrators[agentcore]"
            )

    async def run(
        self,
        input: dict[str, Any],
        config: dict[str, Any] | None = None,
    ) -> OrchestratorResult:
        """
        Invoke the agent on AgentCore and return the result.

        If self.graph is set, runs the LangGraph graph via AgentCore's
        LangGraph executor (memory + sessions managed by AgentCore).
        Otherwise calls the AgentCore invoke API directly.
        """
        session_id = (config or {}).get("session_id", self._new_session_id())

        if self.graph is not None:
            return await self._run_with_graph(input, session_id, config)
        return await self._run_managed(input, session_id, config)

    async def stream(
        self,
        input: dict[str, Any],
        config: dict[str, Any] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Stream AgentCore response chunks."""
        session_id = (config or {}).get("session_id", self._new_session_id())
        # AgentCore streaming via invoke_agent with streaming=True
        # Yields event chunks as they arrive
        yield {"status": "streaming_not_yet_implemented", "session_id": session_id}

    async def resume(
        self,
        thread_id: str,
        approval: dict[str, Any],
        config: dict[str, Any] | None = None,
    ) -> OrchestratorResult:
        """Resume a suspended AgentCore session after ASK approval."""
        # AgentCore sessions are persistent — resume by sending approval
        # as the next input to the existing session
        return await self.run(
            input={"_resume": True, "_approval": approval},
            config={"session_id": thread_id, **(config or {})},
        )

    # ── Private ───────────────────────────────────────────────────────────────

    async def _run_with_graph(
        self,
        input: dict[str, Any],
        session_id: str,
        config: dict[str, Any] | None,
    ) -> OrchestratorResult:
        """Run LangGraph graph using AgentCore as the checkpointer/memory backend."""
        try:
            # Use AgentCore Memory API as the LangGraph checkpointer
            # This persists graph state across sessions
            from langgraph.checkpoint.memory import MemorySaver
            checkpointer = MemorySaver()  # swap for AgentCore checkpointer when available

            run_config = {
                "configurable": {"thread_id": session_id},
                "recursion_limit": 50,
                **(config or {}),
            }
            final_state = await self.graph.ainvoke(input, config=run_config)

            return OrchestratorResult(
                output   = final_state if isinstance(final_state, dict) else {},
                state    = final_state if isinstance(final_state, dict) else {},
                run_id   = session_id,
                metadata = {
                    "framework":  "agentcore+langgraph",
                    "agent_id":   self.agent_id,
                    "memory_id":  self.memory_id,
                },
            )
        except Exception as e:
            if type(e).__name__ in ("GraphInterrupt", "NodeInterrupt"):
                raise OrchestratorSuspended(
                    thread_id      = session_id,
                    pending_effect = getattr(e, "value", {}).get("effect", "unknown"),
                    reason         = str(e),
                ) from e
            raise

    async def _run_managed(
        self,
        input: dict[str, Any],
        session_id: str,
        config: dict[str, Any] | None,
    ) -> OrchestratorResult:
        """Call AgentCore invoke API (fully managed, no local graph)."""
        import asyncio, json
        loop     = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: self._client.invoke_agent(
                agentId      = self.agent_id,
                sessionId    = session_id,
                inputText    = json.dumps(input),
                memoryId     = self.memory_id,
                enableTrace  = True,
            ),
        )
        output_text = ""
        for event in response.get("completion", []):
            if "chunk" in event:
                output_text += event["chunk"].get("bytes", b"").decode("utf-8", errors="replace")

        return OrchestratorResult(
            output   = {"response": output_text, "raw": response},
            state    = {},
            run_id   = session_id,
            metadata = {"framework": "agentcore_managed", "agent_id": self.agent_id},
        )

    @staticmethod
    def _new_session_id() -> str:
        import uuid
        return str(uuid.uuid4())
