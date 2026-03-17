import asyncio
import time
from typing import Protocol, runtime_checkable

from .types import AgentContext, Grant, ToolRequest


@runtime_checkable
class GrantStore(Protocol):
    """Protocol for grant storage backends.

    Implement this protocol to use a custom storage backend (Redis, SQLite, etc.).

    Example Redis implementation:

        class RedisGrantStore:
            def __init__(self, redis_client):
                self.redis = redis_client

            async def create_grant(self, grant: Grant) -> str:
                await self.redis.hset(f"grant:{grant.id}", mapping=grant.to_dict())
                await self.redis.expireat(f"grant:{grant.id}", int(grant.expires_at))
                return grant.id

            async def find_matching_grant(
                self, agent_ctx, tool_request
            ) -> Grant | None:
                # Implement matching logic with Redis SCAN or secondary indexes
                ...

    All methods must be async. The InMemoryGrantStore serves as
    the reference implementation.
    """

    async def create_grant(self, grant: Grant) -> str: ...

    async def find_matching_grant(
        self, agent_ctx: AgentContext, tool_request: ToolRequest
    ) -> Grant | None: ...

    async def revoke_grant(self, grant_id: str) -> bool: ...

    async def list_active_grants(
        self, agent_id: str | None = None, org_id: str | None = None
    ) -> list[Grant]: ...

    async def cleanup_expired(self) -> int: ...

    async def get_usage_count(self, grant_id: str) -> int: ...


class InMemoryGrantStore:
    """In-memory store for action grants with thread-safe matching logic."""

    def __init__(self):
        self._grants: dict[str, Grant] = {}
        self._usage_counts: dict[str, int] = {}
        self._lock = asyncio.Lock()

    async def create_grant(self, grant: Grant) -> str:
        """Store a new grant."""
        async with self._lock:
            self._grants[grant.id] = grant
            self._usage_counts[grant.id] = 0
            return grant.id

    async def find_matching_grant(
        self, agent_ctx: AgentContext, tool_request: ToolRequest
    ) -> Grant | None:
        """Find a non-expired grant that matches the request."""
        now = time.time()
        async with self._lock:
            for grant in self._grants.values():
                # 1. Skip expired
                if grant.expires_at <= now:
                    continue

                # 2. Match org_id (if grant is org-scoped, agent must be in same org)
                if grant.org_id is not None and grant.org_id != agent_ctx.org_id:
                    continue

                # 3. Match agent_id
                if grant.agent_id is not None and grant.agent_id != agent_ctx.agent_id:
                    continue

                # 3. Match effect
                if grant.effect is not None and grant.effect != tool_request.effect:
                    continue

                # 4. Match tool (exact or prefix with *)
                if grant.tool is not None:
                    if grant.tool.endswith("*"):
                        prefix = grant.tool[:-1]
                        if not tool_request.tool.startswith(prefix):
                            continue
                    elif grant.tool != tool_request.tool:
                        continue

                # 5. Match action
                if grant.action is not None and grant.action != tool_request.action:
                    continue

                # 6. Match resource_type
                if (
                    grant.resource_type is not None
                    and grant.resource_type != tool_request.resource_type
                ):
                    continue

                # Match found! Increment usage count
                self._usage_counts[grant.id] += 1
                return grant
            return None

    async def get_usage_count(self, grant_id: str) -> int:
        """Get the number of times a grant has been used."""
        async with self._lock:
            return self._usage_counts.get(grant_id, 0)

    async def revoke_grant(self, grant_id: str) -> bool:
        """Remove a grant by ID."""
        async with self._lock:
            if grant_id in self._grants:
                del self._grants[grant_id]
                if grant_id in self._usage_counts:
                    del self._usage_counts[grant_id]
                return True
            return False

    async def list_active_grants(
        self, agent_id: str | None = None, org_id: str | None = None
    ) -> list[Grant]:
        """List all non-expired grants, optionally filtered by agent or org."""
        now = time.time()
        async with self._lock:
            active = []
            for grant in self._grants.values():
                if grant.expires_at <= now:
                    continue
                if agent_id is not None and grant.agent_id != agent_id:
                    continue
                if org_id is not None and grant.org_id != org_id:
                    continue
                active.append(grant)
            return active

    async def cleanup_expired(self) -> int:
        """Remove all expired grants from the store."""
        now = time.time()
        async with self._lock:
            to_remove = [gid for gid, g in self._grants.items() if g.expires_at <= now]
            for gid in to_remove:
                del self._grants[gid]
                if gid in self._usage_counts:
                    del self._usage_counts[gid]
            return len(to_remove)
