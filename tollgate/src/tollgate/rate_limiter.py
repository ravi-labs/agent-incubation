"""Rate limiting for AI agent tool calls.

Provides a sliding-window rate limiter that tracks per-agent, per-tool
call frequency and blocks calls that exceed configured thresholds.
"""

import asyncio
import time
from typing import Any, Protocol, runtime_checkable

from .types import AgentContext, Effect, ToolRequest


@runtime_checkable
class RateLimiter(Protocol):
    """Protocol for rate limiting backends.

    Implement this protocol to use a custom backend (Redis, etc.).
    The InMemoryRateLimiter serves as the reference implementation.
    """

    async def check_rate_limit(
        self, agent_ctx: AgentContext, tool_request: ToolRequest
    ) -> tuple[bool, str | None, float | None]:
        """Check whether a tool call should be rate-limited.

        Returns:
            (allowed, reason, retry_after)
            - allowed: True if the call is within limits
            - reason: Human-readable reason if blocked (None if allowed)
            - retry_after: Seconds until the window resets (None if allowed)
        """
        ...


class RateLimitRule:
    """A single rate limit rule parsed from config."""

    def __init__(
        self,
        *,
        agent_id: str = "*",
        org_id: str = "*",
        tool: str = "*",
        effect: str | None = None,
        max_calls: int,
        window_seconds: int,
    ):
        self.agent_id = agent_id
        self.org_id = org_id
        self.tool = tool
        self.effect = effect
        self.max_calls = max_calls
        self.window_seconds = window_seconds

    def matches(self, agent_ctx: AgentContext, tool_request: ToolRequest) -> bool:
        """Check if this rule applies to the given request."""
        # Org match
        if self.org_id != "*" and agent_ctx.org_id != self.org_id:
            return False

        # Agent match
        if self.agent_id != "*" and self.agent_id != agent_ctx.agent_id:
            return False

        # Tool match (supports prefix wildcard like "mcp:*")
        if self.tool != "*":
            if self.tool.endswith("*"):
                if not tool_request.tool.startswith(self.tool[:-1]):
                    return False
            elif self.tool != tool_request.tool:
                return False

        # Effect match
        if self.effect is not None:
            try:
                if Effect(self.effect) != tool_request.effect:
                    return False
            except ValueError:
                return False

        return True

    def bucket_key(self, agent_ctx: AgentContext) -> str:
        """Generate a unique bucket key for this rule + agent."""
        org = agent_ctx.org_id or "_global_"
        return (
            f"{self.org_id}|{self.agent_id}|{self.tool}|"
            f"{self.effect or '*'}|{org}|{agent_ctx.agent_id}"
        )


class InMemoryRateLimiter:
    """Sliding-window rate limiter with in-memory storage.

    Config is a list of rule dicts, typically from policy.yaml:

        rate_limits:
          - agent_id: "*"
            tool: "*"
            max_calls: 100
            window_seconds: 60
          - agent_id: "*"
            effect: "write"
            max_calls: 10
            window_seconds: 60
    """

    def __init__(self, rules: list[dict[str, Any]] | None = None):
        self._rules: list[RateLimitRule] = []
        self._buckets: dict[str, list[float]] = {}
        self._lock = asyncio.Lock()

        if rules:
            for r in rules:
                self._rules.append(
                    RateLimitRule(
                        agent_id=r.get("agent_id", "*"),
                        org_id=r.get("org_id", "*"),
                        tool=r.get("tool", "*"),
                        effect=r.get("effect"),
                        max_calls=r["max_calls"],
                        window_seconds=r["window_seconds"],
                    )
                )

    async def check_rate_limit(
        self, agent_ctx: AgentContext, tool_request: ToolRequest
    ) -> tuple[bool, str | None, float | None]:
        """Check all matching rules. First violation wins."""
        now = time.time()

        async with self._lock:
            for rule in self._rules:
                if not rule.matches(agent_ctx, tool_request):
                    continue

                key = rule.bucket_key(agent_ctx)
                window_start = now - rule.window_seconds

                # Get or create bucket, prune expired entries
                bucket = self._buckets.get(key, [])
                bucket = [t for t in bucket if t > window_start]
                self._buckets[key] = bucket

                if len(bucket) >= rule.max_calls:
                    # Rate limit exceeded
                    oldest_in_window = bucket[0] if bucket else now
                    retry_after = oldest_in_window + rule.window_seconds - now
                    reason = (
                        f"Rate limit exceeded: {len(bucket)}/{rule.max_calls} "
                        f"calls in {rule.window_seconds}s window "
                        f"(agent={agent_ctx.agent_id}, "
                        f"tool={rule.tool}, effect={rule.effect or '*'})"
                    )
                    return False, reason, max(0.0, retry_after)

                # Record this call
                bucket.append(now)

        return True, None, None

    async def reset(self, agent_id: str | None = None) -> None:
        """Clear rate limit state. If agent_id is given, clear only that agent."""
        async with self._lock:
            if agent_id is None:
                self._buckets.clear()
            else:
                to_remove = [k for k in self._buckets if k.endswith(f"|{agent_id}")]
                for k in to_remove:
                    del self._buckets[k]
