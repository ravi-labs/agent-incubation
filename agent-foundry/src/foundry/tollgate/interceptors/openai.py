import asyncio
import json
from collections.abc import Callable
from typing import Any

from ..exceptions import TollgateDenied
from ..registry import ToolRegistry
from ..tower import ControlTower
from ..types import AgentContext, Intent, NormalizedToolCall, ToolRequest
from .base import TollgateInterceptor


class OpenAIAdapter:
    """Adapter for OpenAI function/tool calls."""

    def __init__(self, registry: ToolRegistry, tool_map: dict[str, Callable]):
        self.registry = registry
        self.tool_map = tool_map

    def normalize(self, tool_call: Any) -> NormalizedToolCall:
        # tool_call is expected to be a dict with 'name' and 'arguments'
        # OR (tool_call_dict, kwargs)
        if isinstance(tool_call, tuple) and len(tool_call) == 2:
            tc_dict, kwargs = tool_call
        else:
            tc_dict = tool_call
            kwargs = {}

        tool_name = tc_dict.get("function", {}).get("name") or tc_dict.get("name")

        # Security: Validate tool_name is not None or empty
        if not tool_name:
            raise TollgateDenied("Tool call missing required 'name' field")

        args_str = tc_dict.get("function", {}).get("arguments") or tc_dict.get(
            "arguments"
        )

        # Security: Safe JSON parsing with proper error handling
        if isinstance(args_str, str):
            try:
                args = json.loads(args_str)
            except json.JSONDecodeError as e:
                raise TollgateDenied(f"Invalid JSON in tool arguments: {e.msg}") from e
        else:
            args = args_str if args_str is not None else {}

        registry_key = f"openai:{tool_name}"
        effect, resource_type, manifest_version = self.registry.resolve_tool(
            registry_key
        )

        metadata = kwargs.get("metadata", {})

        request = ToolRequest(
            tool="openai",
            action=tool_name,
            resource_type=resource_type,
            effect=effect,
            params=args,
            metadata=metadata,
            manifest_version=manifest_version,
        )

        # Security: Handle missing tool in tool_map
        if tool_name not in self.tool_map:
            raise TollgateDenied(f"Unknown tool: {tool_name}")

        func = self.tool_map[tool_name]

        async def _exec_async():
            if asyncio.iscoroutinefunction(func):
                return await func(**args)
            return func(**args)

        def _exec_sync():
            return func(**args)

        return NormalizedToolCall(
            request=request, exec_async=_exec_async, exec_sync=_exec_sync
        )


class OpenAIToolRunner:
    """Helper to run OpenAI tool calls through Tollgate."""

    def __init__(self, tower: ControlTower, registry: ToolRegistry):
        self.tower = tower
        self.registry = registry

    async def run_async(
        self,
        tool_calls: list[Any],
        tool_map: dict[str, Callable],
        agent_ctx: AgentContext,
        intent: Intent,
    ) -> list[Any]:
        adapter = OpenAIAdapter(self.registry, tool_map)
        interceptor = TollgateInterceptor(self.tower, adapter)

        results = []
        for tc in tool_calls:
            result = await interceptor.intercept_async(agent_ctx, intent, tc)
            results.append(result)
        return results
