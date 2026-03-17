"""Caching utilities for Tollgate.

Provides read-through caching for grant stores and other lookups
to reduce database load in high-throughput deployments.

Usage:

    from tollgate import InMemoryGrantStore
    from tollgate.cache import GrantCache, CachedGrantStore

    # Create a cached grant store
    base_store = InMemoryGrantStore()
    cache = GrantCache(max_size=1000, ttl_seconds=60)
    cached_store = CachedGrantStore(base_store, cache)

    # Use cached_store like any GrantStore
    grant = await cached_store.find_matching_grant(agent_ctx, tool_request)
"""

import asyncio
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, TypeVar

from .grants import GrantStore
from .types import AgentContext, Grant, ToolRequest

T = TypeVar("T")


@dataclass
class CacheEntry:
    """A cached value with expiration."""

    value: Any
    expires_at: float
    hits: int = 0

    @property
    def is_expired(self) -> bool:
        return time.time() > self.expires_at


class GrantCache:
    """LRU cache for grant lookups with TTL-based expiration.

    Thread-safe cache using OrderedDict for LRU eviction.

    Args:
        max_size: Maximum number of entries to cache.
        ttl_seconds: Time-to-live for cache entries.
    """

    def __init__(self, max_size: int = 1000, ttl_seconds: float = 60.0):
        self._max_size = max_size
        self._ttl = ttl_seconds
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = asyncio.Lock()
        self._hits = 0
        self._misses = 0

    def _make_key(self, agent_ctx: AgentContext, tool_request: ToolRequest) -> str:
        """Generate cache key from agent context and tool request.

        Uses | as separator since tool names may contain colons.
        """
        return (
            f"{agent_ctx.agent_id}|{agent_ctx.org_id or '_'}|"
            f"{tool_request.tool}|{tool_request.action}|"
            f"{tool_request.effect.value}"
        )

    async def get(
        self, agent_ctx: AgentContext, tool_request: ToolRequest
    ) -> Grant | None | type[CacheEntry]:
        """Get cached grant, or sentinel indicating cache miss.

        Returns:
            - Grant: Cached grant found
            - None: Cached "no grant" result
            - CacheEntry class: Cache miss (sentinel)
        """
        key = self._make_key(agent_ctx, tool_request)

        async with self._lock:
            entry = self._cache.get(key)

            if entry is None:
                self._misses += 1
                return CacheEntry  # Sentinel for cache miss

            if entry.is_expired:
                self._cache.pop(key, None)
                self._misses += 1
                return CacheEntry

            # Move to end (most recently used)
            self._cache.move_to_end(key)
            entry.hits += 1
            self._hits += 1

            return entry.value

    async def set(
        self,
        agent_ctx: AgentContext,
        tool_request: ToolRequest,
        grant: Grant | None,
    ) -> None:
        """Cache a grant lookup result."""
        key = self._make_key(agent_ctx, tool_request)

        async with self._lock:
            # Evict oldest entries if at capacity
            while len(self._cache) >= self._max_size:
                self._cache.popitem(last=False)

            self._cache[key] = CacheEntry(
                value=grant,
                expires_at=time.time() + self._ttl,
            )

    async def invalidate(
        self,
        agent_id: str | None = None,
        org_id: str | None = None,
        tool: str | None = None,
    ) -> int:
        """Invalidate cache entries matching criteria.

        Args:
            agent_id: Invalidate entries for this agent.
            org_id: Invalidate entries for this org.
            tool: Invalidate entries for this tool (prefix match).

        Returns:
            Number of entries invalidated.
        """
        async with self._lock:
            to_remove = []

            for key in self._cache:
                parts = key.split("|")
                if len(parts) >= 3:
                    key_agent = parts[0]
                    key_org = parts[1] if parts[1] != "_" else None
                    key_tool = parts[2]

                    # Check if entry matches all specified criteria
                    matches = True
                    if agent_id is not None and key_agent != agent_id:
                        matches = False
                    if org_id is not None and key_org != org_id:
                        matches = False
                    if tool is not None and not key_tool.startswith(tool):
                        matches = False

                    if matches:
                        to_remove.append(key)

            for key in to_remove:
                self._cache.pop(key, None)

            return len(to_remove)

    async def clear(self) -> None:
        """Clear all cached entries."""
        async with self._lock:
            self._cache.clear()

    @property
    def stats(self) -> dict[str, Any]:
        """Get cache statistics."""
        total = self._hits + self._misses
        return {
            "size": len(self._cache),
            "max_size": self._max_size,
            "ttl_seconds": self._ttl,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": self._hits / total if total > 0 else 0.0,
        }


class CachedGrantStore:
    """Grant store wrapper with read-through caching.

    Wraps any GrantStore implementation with a cache layer for
    find_matching_grant lookups. Write operations automatically
    invalidate relevant cache entries.

    Args:
        store: The underlying GrantStore.
        cache: The GrantCache to use.
    """

    def __init__(self, store: GrantStore, cache: GrantCache):
        self._store = store
        self._cache = cache

    async def create_grant(self, grant: Grant) -> str:
        """Create a grant and invalidate cache."""
        result = await self._store.create_grant(grant)

        # Invalidate cache entries that might be affected
        await self._cache.invalidate(
            agent_id=grant.agent_id,
            org_id=grant.org_id,
            tool=grant.tool,
        )

        return result

    async def find_matching_grant(
        self, agent_ctx: AgentContext, tool_request: ToolRequest
    ) -> Grant | None:
        """Find matching grant with caching."""
        # Check cache first
        cached = await self._cache.get(agent_ctx, tool_request)

        if cached is not CacheEntry:
            # Cache hit (could be Grant or None)
            return cached

        # Cache miss - query store
        grant = await self._store.find_matching_grant(agent_ctx, tool_request)

        # Cache the result (even if None)
        await self._cache.set(agent_ctx, tool_request, grant)

        return grant

    async def revoke_grant(self, grant_id: str) -> bool:
        """Revoke a grant and invalidate cache."""
        result = await self._store.revoke_grant(grant_id)

        if result:
            # Invalidate all cache entries (we don't know which ones matched)
            await self._cache.clear()

        return result

    async def list_active_grants(
        self, agent_id: str | None = None, org_id: str | None = None
    ) -> list[Grant]:
        """List active grants (not cached)."""
        return await self._store.list_active_grants(agent_id, org_id)

    async def cleanup_expired(self) -> int:
        """Cleanup expired grants and clear cache."""
        result = await self._store.cleanup_expired()

        if result > 0:
            await self._cache.clear()

        return result

    async def get_usage_count(self, grant_id: str) -> int:
        """Get usage count for a grant (not cached)."""
        return await self._store.get_usage_count(grant_id)

    @property
    def cache_stats(self) -> dict[str, Any]:
        """Get cache statistics."""
        return self._cache.stats
