import asyncio
from typing import Any

from ..registry import ToolRegistry
from ..tower import ControlTower
from ..types import AgentContext, Intent, ToolRequest


class GuardedStrandsTool:
    """A wrapper for Strands tools that enforces Tollgate gating."""

    def __init__(self, tool: Any, tower: ControlTower, registry: ToolRegistry):
        self.tool = tool
        self.tower = tower
        self.registry = registry

        # Resolve tool name
        if callable(tool) and hasattr(tool, "__name__"):
            self.name = tool.__name__
        elif hasattr(tool, "name"):
            self.name = tool.name
        else:
            self.name = tool.__class__.__name__

        self.description = getattr(tool, "description", f"Strands tool: {self.name}")

    async def __call__(
        self,
        tool_input: Any,
        agent_ctx: AgentContext,
        intent: Intent,
        metadata: dict[str, Any] | None = None,
        **kwargs,
    ) -> Any:
        return await self.run_async(tool_input, agent_ctx, intent, metadata, **kwargs)

    async def run_async(
        self,
        tool_input: Any,
        agent_ctx: AgentContext,
        intent: Intent,
        metadata: dict[str, Any] | None = None,
        **kwargs,
    ) -> Any:
        tool_key = f"strands:{self.name}"
        effect, resource_type, manifest_version = self.registry.resolve_tool(tool_key)

        params = tool_input if isinstance(tool_input, dict) else {"input": tool_input}
        if kwargs:
            params.update(kwargs)

        request = ToolRequest(
            tool="strands",
            action=self.name,
            resource_type=resource_type,
            effect=effect,
            params=params,
            metadata=metadata or {},
            manifest_version=manifest_version,
        )

        async def _exec_async():
            # Support various calling conventions
            if hasattr(self.tool, "ainvoke"):
                return await self.tool.ainvoke(tool_input, **kwargs)
            if hasattr(self.tool, "arun"):
                return await self.tool.arun(tool_input, **kwargs)
            if asyncio.iscoroutinefunction(self.tool):
                return await self.tool(tool_input, **kwargs)

            # Sync fallback
            def _sync_call():
                if hasattr(self.tool, "invoke"):
                    return self.tool.invoke(tool_input, **kwargs)
                if hasattr(self.tool, "run"):
                    return self.tool.run(tool_input, **kwargs)
                return self.tool(tool_input, **kwargs)

            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, _sync_call)

        return await self.tower.execute_async(agent_ctx, intent, request, _exec_async)


def guard_tools(
    tools: list[Any], tower: ControlTower, registry: ToolRegistry
) -> list[GuardedStrandsTool]:
    """Wrap a list of Strands tools with Tollgate."""
    return [GuardedStrandsTool(t, tower, registry) for t in tools]
