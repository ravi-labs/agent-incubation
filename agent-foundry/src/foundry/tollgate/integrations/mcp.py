from typing import Any

from ..registry import ToolRegistry
from ..tower import ControlTower
from ..types import AgentContext, Intent, ToolRequest


class TollgateMCPClient:
    """A wrapper for an MCP client that gates tool calls through Tollgate."""

    def __init__(
        self,
        client: Any,
        server_name: str,
        tower: ControlTower,
        registry: ToolRegistry,
    ):
        """
        Initialize the TollgateMCPClient.

        :param client: The underlying MCP client (must have a call_tool).
        :param server_name: The explicit name of the MCP server.
        :param tower: The Tollgate ControlTower instance.
        :param registry: The ToolRegistry instance.
        """
        self.client = client
        self.server_name = server_name
        self.tower = tower
        self.registry = registry

    async def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        agent_ctx: AgentContext,
        intent: Intent,
        metadata: dict[str, Any] | None = None,
    ) -> Any:
        """
        Call an MCP tool, intercepted by Tollgate.
        """
        tool_key = f"mcp:{self.server_name}.{tool_name}"
        effect, resource_type, manifest_version = self.registry.resolve_tool(tool_key)

        request = ToolRequest(
            tool="mcp",
            action=tool_name,
            resource_type=resource_type,
            effect=effect,
            params=arguments,
            metadata=metadata or {},
            manifest_version=manifest_version,
        )

        async def _exec_async():
            return await self.client.call_tool(tool_name, arguments)

        return await self.tower.execute_async(agent_ctx, intent, request, _exec_async)
