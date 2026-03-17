"""Redis-backed Rate Limiter for Tollgate.

Provides distributed, production-scale rate limiting using Redis.
Implements the sliding window algorithm with atomic operations.

Requires the ``redis`` package: ``pip install redis[hiredis]``

Usage:

    from tollgate.backends import RedisRateLimiter

    rate_limiter = RedisRateLimiter(
        redis_url="redis://localhost:6379/0",
        rules=[
            {"agent_id": "*", "tool": "*", "max_calls": 100, "window_seconds": 60},
            {"effect": "write", "max_calls": 10, "window_seconds": 60},
        ]
    )

    tower = ControlTower(..., rate_limiter=rate_limiter)
"""

import time
from typing import Any

try:
    import redis.asyncio as aioredis
except ImportError as err:
    raise ImportError(
        "Redis rate limiter requires the 'redis' package. "
        "Install it with: pip install redis[hiredis]"
    ) from err

from ..rate_limiter import RateLimitRule
from ..types import AgentContext, ToolRequest

# Lua script for atomic sliding window rate limiting
# This ensures atomicity even under high concurrency
SLIDING_WINDOW_SCRIPT = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local window_seconds = tonumber(ARGV[2])
local max_calls = tonumber(ARGV[3])

-- Calculate window start time (milliseconds)
local window_start = now - (window_seconds * 1000)

-- Remove expired entries
redis.call('ZREMRANGEBYSCORE', key, '-inf', window_start)

-- Get current count
local current = redis.call('ZCARD', key)

if current >= max_calls then
    -- Rate limited - get oldest entry to calculate retry_after
    local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
    local retry_after = 0
    if #oldest > 0 then
        retry_after = (tonumber(oldest[2]) + (window_seconds * 1000) - now) / 1000
    end
    return {0, current, retry_after}
else
    -- Add new entry
    redis.call('ZADD', key, now, now .. ':' .. math.random(1000000))
    -- Set TTL on the key
    redis.call('EXPIRE', key, window_seconds + 1)
    return {1, current + 1, 0}
end
"""


class RedisRateLimiter:
    """Redis-backed distributed rate limiter.

    Uses Redis sorted sets with atomic Lua scripts to implement
    a sliding window rate limiter. Thread-safe and cluster-ready.

    Args:
        redis_url: Redis connection URL (e.g., ``redis://localhost:6379/0``).
        redis_client: Pre-configured async Redis client (alternative to URL).
        rules: List of rate limit rule dictionaries.
        key_prefix: Prefix for all Redis keys (default ``tollgate:ratelimit:``).
    """

    def __init__(
        self,
        redis_url: str | None = None,
        *,
        redis_client: Any | None = None,
        rules: list[dict[str, Any]] | None = None,
        key_prefix: str = "tollgate:ratelimit:",
    ):
        if redis_client is not None:
            self._redis = redis_client
        elif redis_url is not None:
            self._redis = aioredis.from_url(redis_url, decode_responses=True)
        else:
            raise ValueError("Either redis_url or redis_client must be provided")

        self._prefix = key_prefix
        self._script_sha: str | None = None

        # Parse rules
        self._rules: list[RateLimitRule] = []
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

    async def _ensure_script_loaded(self) -> str:
        """Load the Lua script and return its SHA."""
        if self._script_sha is None:
            self._script_sha = await self._redis.script_load(SLIDING_WINDOW_SCRIPT)
        return self._script_sha

    def _bucket_key(self, rule: RateLimitRule, agent_ctx: AgentContext) -> str:
        """Generate a unique bucket key for this rule + agent."""
        org = agent_ctx.org_id or "_global_"
        return (
            f"{self._prefix}{rule.org_id}|{rule.agent_id}|{rule.tool}|"
            f"{rule.effect or '*'}|{org}|{agent_ctx.agent_id}"
        )

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
        script_sha = await self._ensure_script_loaded()
        now_ms = int(time.time() * 1000)

        for rule in self._rules:
            if not rule.matches(agent_ctx, tool_request):
                continue

            key = self._bucket_key(rule, agent_ctx)

            try:
                result = await self._redis.evalsha(
                    script_sha,
                    1,  # number of keys
                    key,
                    now_ms,
                    rule.window_seconds,
                    rule.max_calls,
                )

                allowed = bool(result[0])
                current_count = int(result[1])
                retry_after = float(result[2])

                if not allowed:
                    reason = (
                        f"Rate limit exceeded: {current_count}/{rule.max_calls} "
                        f"calls in {rule.window_seconds}s window "
                        f"(agent={agent_ctx.agent_id}, "
                        f"tool={rule.tool}, effect={rule.effect or '*'})"
                    )
                    return False, reason, max(0.0, retry_after)

            except aioredis.exceptions.NoScriptError:
                # Script was flushed, reload it
                self._script_sha = None
                script_sha = await self._ensure_script_loaded()
                # Retry once
                result = await self._redis.evalsha(
                    script_sha,
                    1,
                    key,
                    now_ms,
                    rule.window_seconds,
                    rule.max_calls,
                )

                allowed = bool(result[0])
                if not allowed:
                    current_count = int(result[1])
                    retry_after = float(result[2])
                    reason = (
                        f"Rate limit exceeded: {current_count}/{rule.max_calls} "
                        f"calls in {rule.window_seconds}s window "
                        f"(agent={agent_ctx.agent_id}, "
                        f"tool={rule.tool}, effect={rule.effect or '*'})"
                    )
                    return False, reason, max(0.0, retry_after)

        return True, None, None

    async def get_usage(
        self, agent_ctx: AgentContext, tool_request: ToolRequest
    ) -> dict[str, dict[str, Any]]:
        """Get current usage for all matching rules.

        Returns a dict mapping rule descriptions to usage info:
            {
                "rule_description": {
                    "current": 5,
                    "max": 10,
                    "window_seconds": 60,
                    "remaining": 5,
                    "resets_in": 45.2
                }
            }
        """
        now_ms = int(time.time() * 1000)
        usage: dict[str, dict[str, Any]] = {}

        for rule in self._rules:
            if not rule.matches(agent_ctx, tool_request):
                continue

            key = self._bucket_key(rule, agent_ctx)
            window_start = now_ms - (rule.window_seconds * 1000)

            # Remove expired and count
            await self._redis.zremrangebyscore(key, "-inf", window_start)
            current = await self._redis.zcard(key)

            # Get oldest entry for reset time
            oldest = await self._redis.zrange(key, 0, 0, withscores=True)
            if oldest:
                oldest_ts = oldest[0][1]
                resets_in = (oldest_ts + (rule.window_seconds * 1000) - now_ms) / 1000
            else:
                resets_in = rule.window_seconds

            rule_desc = (
                f"agent={rule.agent_id}, tool={rule.tool}, "
                f"effect={rule.effect or '*'}"
            )

            usage[rule_desc] = {
                "current": current,
                "max": rule.max_calls,
                "window_seconds": rule.window_seconds,
                "remaining": max(0, rule.max_calls - current),
                "resets_in": max(0.0, resets_in),
            }

        return usage

    async def reset(
        self,
        agent_id: str | None = None,
        org_id: str | None = None,
    ) -> int:
        """Clear rate limit state.

        Args:
            agent_id: If given, clear only this agent's buckets.
            org_id: If given, clear only this org's buckets.

        Returns:
            Number of keys deleted.
        """
        pattern = f"{self._prefix}*"

        if org_id:
            pattern = f"{self._prefix}*|{org_id}|*"
        if agent_id:
            pattern = f"{self._prefix}*|{agent_id}"

        deleted = 0
        cursor = 0

        while True:
            cursor, keys = await self._redis.scan(
                cursor=cursor, match=pattern, count=100
            )

            if keys:
                # Filter keys based on agent_id if specified
                if agent_id:
                    keys = [k for k in keys if k.endswith(f"|{agent_id}")]

                if keys:
                    deleted += await self._redis.delete(*keys)

            if cursor == 0:
                break

        return deleted

    async def get_all_buckets(self) -> list[dict[str, Any]]:
        """Get information about all active rate limit buckets.

        Returns list of bucket info dicts.
        """
        buckets: list[dict[str, Any]] = []
        cursor = 0

        while True:
            cursor, keys = await self._redis.scan(
                cursor=cursor, match=f"{self._prefix}*", count=100
            )

            for key in keys:
                count = await self._redis.zcard(key)
                ttl = await self._redis.ttl(key)

                # Parse key to extract rule info
                parts = key[len(self._prefix) :].split("|")
                if len(parts) >= 6:
                    buckets.append(
                        {
                            "key": key,
                            "rule_org": parts[0],
                            "rule_agent": parts[1],
                            "rule_tool": parts[2],
                            "rule_effect": parts[3],
                            "actual_org": parts[4],
                            "actual_agent": parts[5],
                            "current_count": count,
                            "ttl_seconds": ttl,
                        }
                    )

            if cursor == 0:
                break

        return buckets

    async def close(self) -> None:
        """Close the Redis connection."""
        await self._redis.close()
