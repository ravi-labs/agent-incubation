"""Redis-backed persistent stores for Tollgate.

Requires the ``redis`` package: ``pip install redis[hiredis]``

Suitable for multi-process and multi-host deployments. Uses Redis hashes
for grant storage and pub/sub for real-time approval notifications.

Usage:

    from tollgate.backends import RedisGrantStore, RedisApprovalStore

    grant_store = RedisGrantStore(redis_url="redis://localhost:6379/0")
    approval_store = RedisApprovalStore(redis_url="redis://localhost:6379/0")

    tower = ControlTower(
        ...,
        grant_store=grant_store,
    )
"""

import asyncio
import json
import time
import uuid
from typing import Any

try:
    import redis.asyncio as aioredis
except ImportError as err:
    raise ImportError(
        "Redis backend requires the 'redis' package. "
        "Install it with: pip install redis[hiredis]"
    ) from err

from ..types import AgentContext, ApprovalOutcome, Effect, Grant, ToolRequest


class RedisGrantStore:
    """Redis-backed GrantStore implementation.

    Uses Redis hashes for grant storage with automatic TTL-based expiry.
    Satisfies the ``GrantStore`` protocol.

    Args:
        redis_url: Redis connection URL (e.g., ``redis://localhost:6379/0``).
        redis_client: Pre-configured async Redis client (alternative to URL).
        key_prefix: Prefix for all Redis keys (default ``tollgate:grant:``).
    """

    def __init__(
        self,
        redis_url: str | None = None,
        *,
        redis_client: Any | None = None,
        key_prefix: str = "tollgate:grant:",
    ):
        if redis_client is not None:
            self._redis = redis_client
        elif redis_url is not None:
            self._redis = aioredis.from_url(redis_url, decode_responses=True)
        else:
            raise ValueError("Either redis_url or redis_client must be provided")

        self._prefix = key_prefix
        self._index_key = f"{key_prefix}__index__"

    def _grant_key(self, grant_id: str) -> str:
        return f"{self._prefix}{grant_id}"

    def _grant_to_dict(self, grant: Grant) -> dict[str, str]:
        """Serialize a Grant to a flat dict for Redis HSET."""
        return {
            "id": grant.id,
            "agent_id": grant.agent_id or "",
            "effect": grant.effect.value if grant.effect else "",
            "tool": grant.tool or "",
            "action": grant.action or "",
            "resource_type": grant.resource_type or "",
            "expires_at": str(grant.expires_at),
            "granted_by": grant.granted_by,
            "created_at": str(grant.created_at),
            "reason": grant.reason or "",
            "usage_count": "0",
            "revoked": "0",
        }

    def _dict_to_grant(self, d: dict[str, str]) -> Grant:
        """Deserialize a Redis hash dict to a Grant."""
        return Grant(
            id=d["id"],
            agent_id=d["agent_id"] or None,
            effect=Effect(d["effect"]) if d["effect"] else None,
            tool=d["tool"] or None,
            action=d["action"] or None,
            resource_type=d["resource_type"] or None,
            expires_at=float(d["expires_at"]),
            granted_by=d["granted_by"],
            created_at=float(d["created_at"]),
            reason=d["reason"] or None,
        )

    async def create_grant(self, grant: Grant) -> str:
        key = self._grant_key(grant.id)
        data = self._grant_to_dict(grant)

        pipe = self._redis.pipeline()
        pipe.hset(key, mapping=data)
        # Set TTL based on expiry time
        ttl = max(1, int(grant.expires_at - time.time()))
        pipe.expire(key, ttl)
        # Track in index set
        pipe.sadd(self._index_key, grant.id)
        await pipe.execute()

        return grant.id

    async def find_matching_grant(
        self, agent_ctx: AgentContext, tool_request: ToolRequest
    ) -> Grant | None:
        # Get all active grant IDs from the index
        grant_ids = await self._redis.smembers(self._index_key)

        for grant_id in grant_ids:
            key = self._grant_key(grant_id)
            data = await self._redis.hgetall(key)

            if not data:
                # Key expired — remove from index
                await self._redis.srem(self._index_key, grant_id)
                continue

            if data.get("revoked") == "1":
                continue

            expires_at = float(data["expires_at"])
            if expires_at <= time.time():
                continue

            # Match agent_id
            if data["agent_id"] and data["agent_id"] != agent_ctx.agent_id:
                continue

            # Match effect
            if data["effect"] and data["effect"] != tool_request.effect.value:
                continue

            # Match tool (exact or prefix with *)
            if data["tool"]:
                if data["tool"].endswith("*"):
                    prefix = data["tool"][:-1]
                    if not tool_request.tool.startswith(prefix):
                        continue
                elif data["tool"] != tool_request.tool:
                    continue

            # Match action
            if data["action"] and data["action"] != tool_request.action:
                continue

            # Match resource_type
            if (
                data["resource_type"]
                and data["resource_type"] != tool_request.resource_type
            ):
                continue

            # Match found — increment usage
            await self._redis.hincrby(key, "usage_count", 1)
            return self._dict_to_grant(data)

        return None

    async def revoke_grant(self, grant_id: str) -> bool:
        key = self._grant_key(grant_id)
        exists = await self._redis.exists(key)
        if not exists:
            return False

        await self._redis.hset(key, "revoked", "1")
        return True

    async def list_active_grants(self, agent_id: str | None = None) -> list[Grant]:
        grant_ids = await self._redis.smembers(self._index_key)
        now = time.time()
        active: list[Grant] = []

        for grant_id in grant_ids:
            key = self._grant_key(grant_id)
            data = await self._redis.hgetall(key)

            if not data:
                await self._redis.srem(self._index_key, grant_id)
                continue

            if data.get("revoked") == "1":
                continue

            if float(data["expires_at"]) <= now:
                continue

            if agent_id is not None and data["agent_id"] != agent_id:
                continue

            active.append(self._dict_to_grant(data))

        return active

    async def cleanup_expired(self) -> int:
        grant_ids = await self._redis.smembers(self._index_key)
        now = time.time()
        removed = 0

        for grant_id in grant_ids:
            key = self._grant_key(grant_id)
            data = await self._redis.hgetall(key)

            if not data or float(data.get("expires_at", "0")) <= now:
                await self._redis.delete(key)
                await self._redis.srem(self._index_key, grant_id)
                removed += 1

        return removed

    async def get_usage_count(self, grant_id: str) -> int:
        key = self._grant_key(grant_id)
        count = await self._redis.hget(key, "usage_count")
        return int(count) if count else 0

    async def close(self):
        """Close the Redis connection."""
        await self._redis.close()


class RedisApprovalStore:
    """Redis-backed ApprovalStore implementation.

    Uses Redis hashes for request storage and pub/sub for real-time
    notifications when a decision is made. This avoids polling.

    Args:
        redis_url: Redis connection URL.
        redis_client: Pre-configured async Redis client.
        key_prefix: Prefix for all Redis keys.
        channel_prefix: Prefix for pub/sub channels.
    """

    def __init__(
        self,
        redis_url: str | None = None,
        *,
        redis_client: Any | None = None,
        key_prefix: str = "tollgate:approval:",
        channel_prefix: str = "tollgate:approval_notify:",
    ):
        if redis_client is not None:
            self._redis = redis_client
        elif redis_url is not None:
            self._redis = aioredis.from_url(redis_url, decode_responses=True)
        else:
            raise ValueError("Either redis_url or redis_client must be provided")

        self._prefix = key_prefix
        self._channel_prefix = channel_prefix

    def _request_key(self, approval_id: str) -> str:
        return f"{self._prefix}{approval_id}"

    def _channel_key(self, approval_id: str) -> str:
        return f"{self._channel_prefix}{approval_id}"

    async def create_request(
        self,
        agent_ctx: AgentContext,
        intent: Any,
        tool_request: ToolRequest,
        request_hash: str,
        reason: str,
        expiry: float,
    ) -> str:
        approval_id = str(uuid.uuid4())
        key = self._request_key(approval_id)

        data = {
            "id": approval_id,
            "agent_json": json.dumps(agent_ctx.to_dict()),
            "intent_json": json.dumps(intent.to_dict()),
            "tool_request_json": json.dumps(tool_request.to_dict()),
            "request_hash": request_hash,
            "reason": reason,
            "expiry": str(expiry),
            "outcome": ApprovalOutcome.DEFERRED.value,
            "decided_by": "",
            "decided_at": "",
        }

        pipe = self._redis.pipeline()
        pipe.hset(key, mapping=data)
        # Auto-expire after the approval window
        ttl = max(1, int(expiry - time.time()) + 60)  # +60s buffer
        pipe.expire(key, ttl)
        await pipe.execute()

        return approval_id

    async def set_decision(
        self,
        approval_id: str,
        outcome: ApprovalOutcome,
        decided_by: str,
        decided_at: float,
        request_hash: str,
    ) -> None:
        key = self._request_key(approval_id)

        # Verify request hash (replay protection)
        stored_hash = await self._redis.hget(key, "request_hash")
        if stored_hash is None:
            return  # Request not found

        if stored_hash != request_hash:
            raise ValueError(
                "Request hash mismatch. Approval bound to a different request."
            )

        pipe = self._redis.pipeline()
        pipe.hset(
            key,
            mapping={
                "outcome": outcome.value,
                "decided_by": decided_by,
                "decided_at": str(decided_at),
            },
        )
        # Publish notification for waiters
        pipe.publish(self._channel_key(approval_id), outcome.value)
        await pipe.execute()

    async def get_request(self, approval_id: str) -> dict[str, Any] | None:
        key = self._request_key(approval_id)
        data = await self._redis.hgetall(key)

        if not data:
            return None

        return {
            "id": data["id"],
            "agent": json.loads(data["agent_json"]),
            "intent": json.loads(data["intent_json"]),
            "tool_request": json.loads(data["tool_request_json"]),
            "request_hash": data["request_hash"],
            "reason": data["reason"],
            "expiry": float(data["expiry"]),
            "outcome": ApprovalOutcome(data["outcome"]),
            "decided_by": data.get("decided_by") or None,
            "decided_at": float(data["decided_at"]) if data.get("decided_at") else None,
        }

    async def wait_for_decision(
        self, approval_id: str, timeout: float
    ) -> ApprovalOutcome:
        """Wait for a decision using Redis pub/sub (non-polling)."""
        # First check if a decision already exists
        req = await self.get_request(approval_id)
        if req is None:
            return ApprovalOutcome.TIMEOUT

        if req["expiry"] < time.time():
            return ApprovalOutcome.TIMEOUT

        if req["outcome"] != ApprovalOutcome.DEFERRED:
            return req["outcome"]

        # Subscribe and wait for a notification
        channel = self._channel_key(approval_id)
        pubsub = self._redis.pubsub()

        try:
            await pubsub.subscribe(channel)

            deadline = time.time() + timeout
            while time.time() < deadline:
                remaining = deadline - time.time()
                if remaining <= 0:
                    break

                try:
                    message = await asyncio.wait_for(
                        pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0),
                        timeout=min(remaining, 2.0),
                    )
                except asyncio.TimeoutError:
                    # Check if decision was made (fallback polling)
                    req = await self.get_request(approval_id)
                    if req and req["outcome"] != ApprovalOutcome.DEFERRED:
                        return req["outcome"]
                    continue

                if message and message["type"] == "message":
                    try:
                        return ApprovalOutcome(message["data"])
                    except ValueError:
                        pass

            return ApprovalOutcome.TIMEOUT

        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.close()

    async def close(self):
        """Close the Redis connection."""
        await self._redis.close()
