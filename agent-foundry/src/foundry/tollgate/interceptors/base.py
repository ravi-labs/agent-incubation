import asyncio
from typing import Any, Protocol

from ..tower import ControlTower
from ..types import AgentContext, Intent, NormalizedToolCall


class ToolAdapter(Protocol):
    """Protocol for framework-specific tool adapters."""

    def normalize(self, tool_call: Any) -> NormalizedToolCall:
        """Normalize a framework-specific tool call."""
        ...


class TollgateInterceptor:
    """Core interceptor for gating tool calls."""

    def __init__(self, tower: ControlTower, adapter: ToolAdapter):
        self.tower = tower
        self.adapter = adapter

    async def intercept_async(
        self, agent_ctx: AgentContext, intent: Intent, tool_call: Any
    ) -> Any:
        """Intercept and gate a tool call asynchronously."""
        normalized = self.adapter.normalize(tool_call)
        return await self.tower.execute_async(
            agent_ctx, intent, normalized.request, normalized.exec_async
        )

    def intercept(self, agent_ctx: AgentContext, intent: Intent, tool_call: Any) -> Any:
        """Intercept and gate a tool call synchronously."""
        normalized = self.adapter.normalize(tool_call)
        # For sync, we use tower.execute which handles the loop safety check
        return self.tower.execute(
            agent_ctx,
            intent,
            normalized.request,
            lambda: asyncio.run(normalized.exec_async()),
        )
