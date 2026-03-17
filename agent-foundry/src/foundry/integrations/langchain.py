"""
foundry.integrations.langchain
────────────────────────────────
LangChain integration for agent-foundry.

Provides two primary adapters:

  1. FoundryTool — a LangChain StructuredTool that wraps a single
     FinancialEffect + run_effect() call. Drop into any LangChain
     AgentExecutor or LCEL chain.

  2. FoundryToolkit — a collection of FoundryTools derived automatically
     from an agent manifest's declared effects, ready for use with
     LangChain's initialize_agent().

  3. FoundryRunnable — makes BaseAgent implement LangChain's Runnable
     protocol so agents can be composed with | in LCEL pipelines:

         chain = retriever | FoundryRunnable(agent) | output_parser

Install:
    pip install "agent-foundry[langchain]"

Quick start — single tool:

    from foundry.integrations.langchain import FoundryTool, FoundryToolkit
    from foundry.policy.effects import FinancialEffect

    # Wrap a single effect as a LangChain tool
    risk_tool = FoundryTool.from_effect(
        agent=my_agent,
        effect=FinancialEffect.RISK_SCORE_COMPUTE,
        description="Compute retirement readiness risk score for a participant",
        args_schema=RiskScoreInput,  # optional Pydantic schema
    )

    # Build all tools from manifest
    toolkit = FoundryToolkit.from_agent(my_agent)
    tools   = toolkit.get_tools()

    # Use with LangChain AgentExecutor
    from langchain.agents import AgentExecutor, create_openai_tools_agent
    from langchain_openai import ChatOpenAI

    llm   = ChatOpenAI(model="gpt-4o")
    agent = create_openai_tools_agent(llm, tools, prompt)
    executor = AgentExecutor(agent=agent, tools=tools, verbose=True)
    result   = executor.invoke({"input": "Is participant P001 on track?"})

Quick start — LCEL composition:

    from foundry.integrations.langchain import FoundryRunnable
    from langchain_core.output_parsers import StrOutputParser

    chain = (
        {"fund_id": lambda x: x["fund_id"], "plan_id": lambda x: x["plan_id"]}
        | FoundryRunnable(watchdog_agent)
        | StrOutputParser()
    )
    result = chain.invoke({"fund_id": "FUND001", "plan_id": "PLAN001"})
"""

from __future__ import annotations

import asyncio
import inspect
import logging
from typing import Any, Iterator, AsyncIterator, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from foundry.scaffold.base import BaseAgent

logger = logging.getLogger(__name__)


# ── LangChain import guard ─────────────────────────────────────────────────────

def _require_langchain() -> None:
    try:
        import langchain_core  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "LangChain is not installed. "
            "Run: pip install 'agent-foundry[langchain]'"
        ) from exc


# ── FoundryTool ────────────────────────────────────────────────────────────────


class FoundryTool:
    """
    A LangChain StructuredTool wrapper around a single FinancialEffect.

    Wraps agent.run_effect() so it can be used inside any LangChain
    AgentExecutor, LCEL chain, or tool-calling LLM.

    All calls still go through Tollgate — policy enforcement, rate-limiting,
    and audit logging are preserved even inside a LangChain agent.

    Usage:
        tool = FoundryTool.from_effect(
            agent=my_agent,
            effect=FinancialEffect.RISK_SCORE_COMPUTE,
            description="Compute retirement risk score for a participant.",
        )
        tools = [tool]

        # Works with AgentExecutor, LCEL, etc.
        result = await tool.arun({"participant_id": "P001"})
    """

    def __init__(
        self,
        agent: "BaseAgent",
        effect: Any,            # FinancialEffect
        tool_name: str,
        description: str,
        tool_action: str = "execute",
        intent_reason: str = "LangChain agent tool invocation",
        args_schema: type | None = None,
    ):
        _require_langchain()
        self.agent         = agent
        self.effect        = effect
        self.tool_name     = tool_name
        self.description   = description
        self.tool_action   = tool_action
        self.intent_reason = intent_reason
        self.args_schema   = args_schema
        self._lc_tool: Any = None

    @classmethod
    def from_effect(
        cls,
        agent: "BaseAgent",
        effect: Any,
        description: str,
        intent_reason: str = "LangChain agent tool invocation",
        args_schema: type | None = None,
    ) -> "FoundryTool":
        """
        Create a FoundryTool for a specific FinancialEffect.

        Args:
            agent:         The BaseAgent that will execute the effect.
            effect:        The FinancialEffect to wrap.
            description:   Human-readable description for the LLM.
            intent_reason: Default audit trail reason for invocations.
            args_schema:   Optional Pydantic BaseModel for input validation.

        Returns:
            A FoundryTool ready for use with LangChain.
        """
        tool_name = effect.value.replace(".", "_")
        return cls(
            agent=agent,
            effect=effect,
            tool_name=tool_name,
            description=description,
            intent_reason=intent_reason,
            args_schema=args_schema,
        )

    async def _arun(self, **kwargs: Any) -> Any:
        """Async execution — calls run_effect() with the provided kwargs."""
        effect_value = self.effect.value if hasattr(self.effect, "value") else str(self.effect)

        return await self.agent.run_effect(
            effect=self.effect,
            tool=f"langchain.{self.tool_name}",
            action=self.tool_action,
            params=kwargs,
            intent_action=f"lc.{effect_value}",
            intent_reason=self.intent_reason,
            metadata={"source": "langchain_tool", "tool_name": self.tool_name},
        )

    def _run(self, **kwargs: Any) -> Any:
        """Sync execution — runs the async _arun() in a new event loop."""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # Inside an existing async context (e.g. Jupyter, async test)
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(asyncio.run, self._arun(**kwargs))
                    return future.result()
            else:
                return loop.run_until_complete(self._arun(**kwargs))
        except RuntimeError:
            return asyncio.run(self._arun(**kwargs))

    def as_langchain_tool(self) -> Any:
        """
        Return a LangChain StructuredTool wrapping this FoundryTool.

        The returned tool is compatible with AgentExecutor, LCEL chains,
        and any LangChain component that accepts Tool objects.

        Returns:
            langchain_core.tools.StructuredTool
        """
        if self._lc_tool is not None:
            return self._lc_tool

        try:
            from langchain_core.tools import StructuredTool
        except ImportError as exc:
            raise ImportError(
                "langchain-core is not installed. "
                "Run: pip install 'agent-foundry[langchain]'"
            ) from exc

        tool_self = self

        async def _async_func(**kwargs: Any) -> Any:
            return await tool_self._arun(**kwargs)

        def _sync_func(**kwargs: Any) -> Any:
            return tool_self._run(**kwargs)

        kwargs: dict[str, Any] = dict(
            name=self.tool_name,
            description=self.description,
            func=_sync_func,
            coroutine=_async_func,
        )
        if self.args_schema is not None:
            kwargs["args_schema"] = self.args_schema

        self._lc_tool = StructuredTool.from_function(**kwargs)
        return self._lc_tool

    def __repr__(self) -> str:
        return f"FoundryTool(effect={self.effect!r}, name={self.tool_name!r})"


# ── FoundryToolkit ─────────────────────────────────────────────────────────────


class FoundryToolkit:
    """
    A collection of FoundryTools derived from an agent manifest.

    Automatically creates a FoundryTool for each effect declared in the
    agent's manifest, with descriptions pulled from the EffectMeta registry.

    Usage:
        toolkit = FoundryToolkit.from_agent(my_agent)
        tools   = toolkit.get_tools()               # → list[StructuredTool]

        # With AgentExecutor
        executor = AgentExecutor(agent=agent, tools=tools)

        # Or just the raw FoundryTool wrappers
        foundry_tools = toolkit.get_foundry_tools()  # → list[FoundryTool]
    """

    def __init__(self, tools: list[FoundryTool]):
        self._tools = tools

    @classmethod
    def from_agent(
        cls,
        agent: "BaseAgent",
        *,
        include_tiers: list[int] | None = None,
        exclude_effects: list[str] | None = None,
        intent_reason: str = "LangChain AgentExecutor tool invocation",
    ) -> "FoundryToolkit":
        """
        Build a FoundryToolkit from an agent's declared effects.

        Args:
            agent:           The BaseAgent to build tools for.
            include_tiers:   Only include effects from these tiers (default: all).
                             Example: [1, 2] for read/compute only.
            exclude_effects: Effect values to exclude (e.g. ["audit.log.write"]).
            intent_reason:   Default audit intent reason for all tools.

        Returns:
            A FoundryToolkit with one FoundryTool per declared effect.
        """
        from foundry.policy.effects import FinancialEffect, EFFECT_METADATA

        exclude = set(exclude_effects or [])
        tools: list[FoundryTool] = []

        for effect in agent.manifest.allowed_effects:
            # allowed_effects contains FinancialEffect instances (not raw strings).
            # Normalise defensively: accept both FinancialEffect and str values.
            if not isinstance(effect, FinancialEffect):
                try:
                    effect = FinancialEffect(effect)
                except ValueError:
                    logger.debug("Skipping unknown effect: %s", effect)
                    continue

            effect_value = effect.value
            if effect_value in exclude or effect in exclude:
                continue

            meta = EFFECT_METADATA.get(effect)
            if meta is None:
                continue

            tier = getattr(meta, "tier", None)
            if include_tiers is not None and (tier is None or tier.value not in include_tiers):
                continue

            description = (
                getattr(meta, "description", None)
                or f"Execute effect: {effect_value}"
            )
            # Append tier and default decision for LLM context
            tier_label = f"Tier {tier.value}" if tier else "Unknown tier"
            default_dec = getattr(meta, "default_decision", None)
            default_str = f" — default policy: {default_dec.value}" if default_dec else ""
            full_description = f"[{tier_label}{default_str}] {description}"

            tools.append(FoundryTool.from_effect(
                agent=agent,
                effect=effect,
                description=full_description,
                intent_reason=intent_reason,
            ))

        logger.info(
            "FoundryToolkit.from_agent agent=%s tools=%d",
            agent.manifest.agent_id, len(tools),
        )
        return cls(tools)

    def get_tools(self) -> list[Any]:
        """Return LangChain StructuredTool objects for all effects."""
        return [t.as_langchain_tool() for t in self._tools]

    def get_foundry_tools(self) -> list[FoundryTool]:
        """Return the raw FoundryTool wrappers (before LangChain wrapping)."""
        return list(self._tools)

    def get_tool(self, effect_value: str) -> FoundryTool | None:
        """Get a specific FoundryTool by effect value (e.g. 'risk.score.compute')."""
        for tool in self._tools:
            effect_val = tool.effect.value if hasattr(tool.effect, "value") else str(tool.effect)
            if effect_val == effect_value:
                return tool
        return None

    def __len__(self) -> int:
        return len(self._tools)

    def __repr__(self) -> str:
        return f"FoundryToolkit(tools={len(self._tools)})"


# ── FoundryRunnable ────────────────────────────────────────────────────────────


class FoundryRunnable:
    """
    Makes a BaseAgent usable as a LangChain Runnable in LCEL pipelines.

    Implements the Runnable protocol (invoke, ainvoke, stream, astream)
    so a foundry agent can be composed using LangChain's | operator:

        chain = retrieval_runnable | FoundryRunnable(agent) | output_parser

    The agent's execute(**kwargs) is called with the input dict unpacked
    as keyword arguments. The result is passed downstream as-is.

    Usage:

        from foundry.integrations.langchain import FoundryRunnable
        from langchain_core.output_parsers import JsonOutputParser

        runnable = FoundryRunnable(watchdog_agent)

        # Direct invocation
        result = runnable.invoke({"fund_id": "FUND001", "plan_id": "PLAN001"})

        # Async
        result = await runnable.ainvoke({"fund_id": "FUND001", "plan_id": "PLAN001"})

        # LCEL composition
        chain = (
            RunnablePassthrough()
            | FoundryRunnable(watchdog_agent)
            | JsonOutputParser()
        )
        output = chain.invoke({"fund_id": "FUND001", "plan_id": "PLAN001"})

    Streaming:
        If the agent implements execute_stream() (async generator), astream()
        will yield each chunk as it is produced. Otherwise, invoke() result
        is wrapped in a single-element iterator.
    """

    def __init__(self, agent: "BaseAgent", config: dict | None = None):
        _require_langchain()
        self.agent  = agent
        self.config = config or {}

    # ── Core Runnable protocol ─────────────────────────────────────────────

    def invoke(self, input: Any, config: dict | None = None, **kwargs: Any) -> Any:
        """
        Synchronously invoke the agent.

        Args:
            input:  dict of kwargs for agent.execute(), or a string
                    (passed as {"input": string} for simple chat-style agents).
            config: Optional LangChain RunnableConfig (ignored internally,
                    forwarded for chain compatibility).
        """
        agent_kwargs = self._normalise_input(input)
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                    future = executor.submit(
                        asyncio.run, self.agent.execute(**agent_kwargs)
                    )
                    return future.result()
            else:
                return loop.run_until_complete(self.agent.execute(**agent_kwargs))
        except RuntimeError:
            return asyncio.run(self.agent.execute(**agent_kwargs))

    async def ainvoke(self, input: Any, config: dict | None = None, **kwargs: Any) -> Any:
        """Asynchronously invoke the agent."""
        agent_kwargs = self._normalise_input(input)
        return await self.agent.execute(**agent_kwargs)

    def stream(self, input: Any, config: dict | None = None, **kwargs: Any) -> Iterator[Any]:
        """
        Synchronously stream agent output.

        If the agent implements execute_stream(), yields each chunk.
        Otherwise yields the full execute() result as a single item.
        """
        agent_kwargs = self._normalise_input(input)
        if hasattr(self.agent, "execute_stream"):
            # Drain the async generator synchronously
            async def _collect() -> list:
                return [chunk async for chunk in self.agent.execute_stream(**agent_kwargs)]
            chunks = asyncio.run(_collect())
            yield from chunks
        else:
            yield self.invoke(input, config, **kwargs)

    async def astream(
        self, input: Any, config: dict | None = None, **kwargs: Any
    ) -> AsyncIterator[Any]:
        """
        Asynchronously stream agent output.

        If the agent implements execute_stream(), yields each chunk as
        it is produced (true async streaming). Otherwise yields the full
        execute() result as a single item.
        """
        agent_kwargs = self._normalise_input(input)
        if hasattr(self.agent, "execute_stream"):
            async for chunk in self.agent.execute_stream(**agent_kwargs):
                yield chunk
        else:
            result = await self.agent.execute(**agent_kwargs)
            yield result

    # ── LCEL pipe operator ─────────────────────────────────────────────────

    def __or__(self, other: Any) -> Any:
        """
        Support LCEL pipe composition: FoundryRunnable(agent) | next_step.

        Returns a LangChain RunnableSequence.
        """
        try:
            from langchain_core.runnables import RunnableSequence
            return RunnableSequence(self, other)
        except ImportError:
            raise ImportError(
                "langchain-core is not installed. "
                "Run: pip install 'agent-foundry[langchain]'"
            )

    def __ror__(self, other: Any) -> Any:
        """Support LCEL pipe composition: prev_step | FoundryRunnable(agent)."""
        try:
            from langchain_core.runnables import RunnableSequence
            return RunnableSequence(other, self)
        except ImportError:
            raise ImportError(
                "langchain-core is not installed. "
                "Run: pip install 'agent-foundry[langchain]'"
            )

    # ── Helpers ────────────────────────────────────────────────────────────

    @staticmethod
    def _normalise_input(input: Any) -> dict:
        """
        Convert LangChain input to agent.execute() kwargs.

        Handles:
          - dict → passed directly as **kwargs
          - str  → {"input": str}
          - list → {"messages": list}
          - other → {"input": value}
        """
        if isinstance(input, dict):
            return input
        if isinstance(input, str):
            return {"input": input}
        if isinstance(input, list):
            return {"messages": input}
        return {"input": input}

    def __repr__(self) -> str:
        return f"FoundryRunnable(agent={self.agent.manifest.agent_id!r})"


# ── LangChain Runnable ABC compliance ──────────────────────────────────────────
# Register FoundryRunnable as a virtual subclass of Runnable if available,
# so isinstance(runnable, Runnable) returns True in LangChain environments.

try:
    from langchain_core.runnables import Runnable as _LCRunnable
    _LCRunnable.register(FoundryRunnable)   # type: ignore[attr-defined]
except (ImportError, AttributeError):
    pass


__all__ = [
    "FoundryTool",
    "FoundryToolkit",
    "FoundryRunnable",
]
