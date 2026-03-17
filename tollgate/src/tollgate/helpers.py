import asyncio
from collections.abc import Callable
from functools import wraps
from typing import Any

from .tower import ControlTower
from .types import AgentContext, Effect, Intent, ToolRequest


def wrap_tool(
    tower: ControlTower,
    tool_callable: Callable,
    *,
    tool: str,
    action: str,
    resource_type: str,
    effect: Effect,
):
    """
    Wraps a tool callable to be executed through a ControlTower.
    Maintained for backward compatibility with v0.
    """

    @wraps(tool_callable)
    def wrapper(
        agent_ctx: AgentContext,
        intent: Intent,
        metadata: dict[str, Any] | None = None,
        **params,
    ) -> Any:
        req = ToolRequest(
            tool=tool,
            action=action,
            resource_type=resource_type,
            effect=effect,
            params=params,
            metadata=metadata or {},
        )

        if asyncio.iscoroutinefunction(tool_callable):

            async def _exec():
                return await tool_callable(**params)

            return asyncio.run(tower.execute_async(agent_ctx, intent, req, _exec))

        return tower.execute(agent_ctx, intent, req, lambda: tool_callable(**params))

    return wrapper


def guard(
    tower: ControlTower,
    *,
    tool: str,
    action: str,
    resource_type: str,
    effect: Effect,
):
    """Decorator to guard a tool function with a ControlTower."""

    def decorator(func: Callable):
        return wrap_tool(
            tower,
            func,
            tool=tool,
            action=action,
            resource_type=resource_type,
            effect=effect,
        )

    return decorator
