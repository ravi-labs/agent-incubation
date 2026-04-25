"""
StrandsOrchestrator — AWS Strands Agents adapter (stub).

Implements OrchestratorProtocol for AWS Strands when needed.
Strands is a lighter-weight alternative to LangGraph for simpler
single-step agent flows.

Install: pip install arc-orchestrators[strands]
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Any, AsyncIterator
from .protocol import OrchestratorResult


@dataclass
class StrandsOrchestrator:
    """
    Arc orchestrator backed by AWS Strands Agents.

    Stub implementation — wire in Strands SDK when needed.
    Strands is best suited for single-step tool-calling agents;
    for multi-step stateful pipelines prefer LangGraphOrchestrator.
    """

    model_id: str = "us.anthropic.claude-3-5-sonnet-20241022-v2:0"
    region:   str = "us-east-1"
    tools:    list = None   # type: ignore

    async def run(
        self,
        input: dict[str, Any],
        config: dict[str, Any] | None = None,
    ) -> OrchestratorResult:
        try:
            from strands import Agent
            from strands.models import BedrockModel
        except ImportError:
            raise ImportError(
                "strands-agents is required: pip install arc-orchestrators[strands]"
            )

        model = BedrockModel(model_id=self.model_id, region_name=self.region)
        agent = Agent(model=model, tools=self.tools or [])
        result = agent(input.get("prompt", str(input)))

        return OrchestratorResult(
            output   = {"response": str(result)},
            state    = {},
            run_id   = "",
            metadata = {"framework": "strands"},
        )

    async def stream(
        self,
        input: dict[str, Any],
        config: dict[str, Any] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        result = await self.run(input, config)
        yield result.output

    async def resume(
        self,
        thread_id: str,
        approval: dict[str, Any],
        config: dict[str, Any] | None = None,
    ) -> OrchestratorResult:
        # Strands does not support stateful resumption — re-run with approval
        return await self.run({"_approval": approval}, config)
