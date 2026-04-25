"""
foundry.tools.registry
───────────────────────
Governed tool registration and invocation for Foundry agents.

Provides a framework-agnostic way to register Python callables as governed
tools, independent of LangChain. Every tool call goes through run_effect()
so it is policy-enforced and audit-logged.

Key concepts:

  GovernedToolDef   — metadata about a governed tool (effect, schema, description)
  @governed_tool    — decorator to mark an async function as a governed tool
  ToolRegistry      — attaches to a BaseAgent; registers and invokes tools

Usage — decorator pattern:

    from foundry.tools.registry import governed_tool, ToolRegistry
    from foundry.policy.effects import FinancialEffect

    class FiduciaryAgent(BaseAgent):

        def __init__(self, manifest, tower, gateway, tracker=None):
            super().__init__(manifest, tower, gateway, tracker)
            self.tools = ToolRegistry(self)
            self.tools.register_all(self)   # auto-discovers @governed_tool methods

        @governed_tool(
            effect=FinancialEffect.RISK_SCORE_COMPUTE,
            description="Compute retirement readiness risk score for a participant.",
            intent_reason="Assess participant retirement readiness trajectory",
        )
        async def compute_risk_score(self, participant_id: str, age: int) -> float:
            # Your actual business logic here
            return 0.72

        async def execute(self, participant_id: str, **kwargs) -> dict:
            score = await self.tools.invoke("compute_risk_score", participant_id=participant_id, age=55)
            return {"score": score}

Usage — explicit registration:

    class MyAgent(BaseAgent):

        def __init__(self, manifest, tower, gateway, tracker=None):
            super().__init__(manifest, tower, gateway, tracker)
            self.tools = ToolRegistry(self)
            self.tools.register(
                name="send_alert",
                fn=self._send_alert,
                effect=FinancialEffect.PARTICIPANT_COMMUNICATION_SEND,
                description="Send a compliance alert to a participant.",
                intent_reason="Notify participant of account risk",
            )

        async def _send_alert(self, participant_id: str, message: str) -> dict:
            ...

Tool schema:
    @governed_tool supports an optional `params_schema` (dict mapping param name
    to type string) for documentation and input validation. This is also surfaced
    to FoundryToolkit when building LangChain tools from a ToolRegistry.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from foundry.scaffold.base import BaseAgent

logger = logging.getLogger(__name__)

_GOVERNED_TOOL_ATTR = "__governed_tool__"


# ── Data model ─────────────────────────────────────────────────────────────────


@dataclass
class GovernedToolDef:
    """
    Metadata for a registered governed tool.

    Attributes:
        name:          Tool name (used for invoke() calls).
        fn:            The underlying async callable.
        effect:        The FinancialEffect that governs this tool.
        description:   Human-readable description for LLMs and documentation.
        intent_reason: Default audit intent reason for invocations.
        params_schema: Optional dict of {"param_name": "type_string"} for docs.
        tags:          Arbitrary tags for filtering (e.g. ["read", "participant"]).
    """
    name:          str
    fn:            Callable
    effect:        Any       # FinancialEffect
    description:   str
    intent_reason: str
    params_schema: dict[str, str] = field(default_factory=dict)
    tags:          list[str]      = field(default_factory=list)


# ── Decorator ──────────────────────────────────────────────────────────────────


def governed_tool(
    effect: Any,
    description: str,
    intent_reason: str = "Tool invocation via ToolRegistry",
    params_schema: dict[str, str] | None = None,
    tags: list[str] | None = None,
):
    """
    Decorator to mark an async method as a governed tool.

    The decorated function will be auto-discovered by ToolRegistry.register_all()
    and registered with the given FinancialEffect.

    Args:
        effect:        FinancialEffect that governs this tool.
        description:   Human-readable purpose (surfaced to LLMs).
        intent_reason: Default audit trail reason.
        params_schema: Optional {"param": "type"} for documentation.
        tags:          Optional categorisation tags.

    Usage:
        @governed_tool(
            effect=FinancialEffect.RISK_SCORE_COMPUTE,
            description="Compute retirement risk score for a participant.",
            params_schema={"participant_id": "str", "age": "int"},
        )
        async def compute_risk_score(self, participant_id: str, age: int) -> float:
            ...
    """
    def decorator(fn: Callable) -> Callable:
        # Store metadata on the function object for later discovery
        setattr(fn, _GOVERNED_TOOL_ATTR, {
            "effect":        effect,
            "description":   description,
            "intent_reason": intent_reason,
            "params_schema": params_schema or {},
            "tags":          tags or [],
        })

        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            return await fn(*args, **kwargs)

        # Copy the metadata to the wrapper too
        setattr(wrapper, _GOVERNED_TOOL_ATTR, getattr(fn, _GOVERNED_TOOL_ATTR))
        return wrapper

    return decorator


# ── Registry ───────────────────────────────────────────────────────────────────


class AgentToolRegistry:
    """
    Registry of governed tools attached to a BaseAgent.

    Renamed from ``ToolRegistry`` to avoid collision with
    ``foundry.tollgate.ToolRegistry`` (which is Tollgate's internal
    resource-type registry, a different concept). A backward-compatible
    ``ToolRegistry`` alias is exported from this module.

    All tool invocations go through agent.run_effect() so every call is:
      - Policy-enforced (declared effect must be in manifest)
      - Audit-logged (intent, params, result)
      - Rate-limited and anomaly-detected by Tollgate

    Args:
        agent: The BaseAgent that owns this registry.
    """

    def __init__(self, agent: "BaseAgent"):
        self._agent = agent
        self._tools: dict[str, GovernedToolDef] = {}

    def register(
        self,
        name: str,
        fn: Callable,
        effect: Any,
        description: str,
        intent_reason: str = "Tool invocation via ToolRegistry",
        params_schema: dict[str, str] | None = None,
        tags: list[str] | None = None,
    ) -> None:
        """
        Register an async callable as a governed tool.

        Args:
            name:          Tool name used in invoke() calls.
            fn:            Async callable (can be a method, lambda, or function).
            effect:        FinancialEffect governing this tool.
            description:   Human-readable description for documentation.
            intent_reason: Default audit intent reason.
            params_schema: Optional {"param": "type"} mapping for docs.
            tags:          Optional categorisation tags.
        """
        if name in self._tools:
            logger.warning("ToolRegistry: overwriting existing tool '%s'", name)

        self._tools[name] = GovernedToolDef(
            name=name,
            fn=fn,
            effect=effect,
            description=description,
            intent_reason=intent_reason,
            params_schema=params_schema or {},
            tags=tags or [],
        )
        logger.debug(
            "tool_registered name=%s effect=%s agent=%s",
            name,
            effect.value if hasattr(effect, "value") else effect,
            self._agent.manifest.agent_id,
        )

    def register_all(self, obj: Any | None = None) -> int:
        """
        Auto-discover and register all @governed_tool methods on an object.

        Scans all methods on `obj` (default: the agent itself) for the
        @governed_tool decorator and registers them automatically.

        Args:
            obj: Object to scan (default: the agent this registry belongs to).

        Returns:
            Number of tools registered.
        """
        target = obj if obj is not None else self._agent
        count = 0
        for attr_name in dir(target):
            try:
                attr = getattr(target, attr_name)
            except AttributeError:
                continue
            meta = getattr(attr, _GOVERNED_TOOL_ATTR, None)
            if meta is None:
                continue
            self.register(
                name=attr_name,
                fn=attr,
                effect=meta["effect"],
                description=meta["description"],
                intent_reason=meta["intent_reason"],
                params_schema=meta["params_schema"],
                tags=meta["tags"],
            )
            count += 1
        logger.info(
            "tool_registry_scan agent=%s registered=%d",
            self._agent.manifest.agent_id, count,
        )
        return count

    async def invoke(
        self,
        name: str,
        intent_reason: str | None = None,
        metadata: dict | None = None,
        **kwargs: Any,
    ) -> Any:
        """
        Invoke a registered governed tool by name.

        Routes through agent.run_effect() — policy-enforced and audit-logged.

        Args:
            name:          Name of the registered tool.
            intent_reason: Override the default audit intent reason.
            metadata:      Extra metadata for the audit event.
            **kwargs:      Parameters passed to the tool function.

        Returns:
            Whatever the tool function returns.

        Raises:
            KeyError:        If the tool is not registered.
            PermissionError: If the effect is denied by policy.
        """
        tool = self._tools.get(name)
        if tool is None:
            available = list(self._tools.keys())
            raise KeyError(
                f"Tool '{name}' not registered in ToolRegistry for agent "
                f"'{self._agent.manifest.agent_id}'. "
                f"Available: {available}"
            )

        effect_value = tool.effect.value if hasattr(tool.effect, "value") else str(tool.effect)
        reason = intent_reason or tool.intent_reason

        async def _exec():
            if asyncio.iscoroutinefunction(tool.fn):
                return await tool.fn(**kwargs)
            return await asyncio.to_thread(tool.fn, **kwargs)

        return await self._agent.run_effect(
            effect=tool.effect,
            tool=f"tool_registry.{name}",
            action="invoke",
            params=kwargs,
            intent_action=f"tool.{effect_value}",
            intent_reason=reason,
            metadata={
                "tool_name": name,
                "registry":  "ToolRegistry",
                **(metadata or {}),
            },
            exec_fn=_exec,
        )

    def list_tools(self, tag: str | None = None) -> list[GovernedToolDef]:
        """
        List all registered tools, optionally filtered by tag.

        Args:
            tag: If provided, only return tools with this tag.

        Returns:
            List of GovernedToolDef in registration order.
        """
        tools = list(self._tools.values())
        if tag:
            tools = [t for t in tools if tag in t.tags]
        return tools

    def get(self, name: str) -> GovernedToolDef | None:
        """Get a specific tool definition by name."""
        return self._tools.get(name)

    def names(self) -> list[str]:
        """Return all registered tool names."""
        return list(self._tools.keys())

    def __len__(self) -> int:
        return len(self._tools)

    def __repr__(self) -> str:
        return f"AgentToolRegistry(agent={self._agent.manifest.agent_id!r}, tools={len(self)})"


# Backward-compatible alias — keeps existing code working while avoiding
# the name collision with foundry.tollgate.ToolRegistry.
ToolRegistry = AgentToolRegistry
