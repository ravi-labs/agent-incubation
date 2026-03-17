"""Quota management for multi-tenant Tollgate deployments.

Provides per-tenant quota enforcement for tool calls, grants, and other
resources. Quotas are soft limits that can be checked before operations.

Usage:

    from tollgate.tenancy import TenantQuotas, QuotaEnforcer, InMemoryQuotaStore

    # Define quotas per tier
    free_tier = TenantQuotas(max_tool_calls_per_hour=100)
    pro_tier = TenantQuotas(max_tool_calls_per_hour=10000)

    # Create enforcer
    store = InMemoryQuotaStore()
    enforcer = QuotaEnforcer(store, default_quotas=free_tier)
    enforcer.set_tenant_quotas("org-premium", pro_tier)

    # Check quota before operation
    if await enforcer.check_and_increment("org-123", "tool_calls"):
        # Proceed with tool call
        ...
    else:
        raise QuotaExceededError("Tool call quota exceeded")
"""

import asyncio
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from ..exceptions import TollgateError


class QuotaExceededError(TollgateError):
    """Raised when a tenant exceeds their quota."""

    def __init__(
        self,
        org_id: str,
        quota_type: str,
        current: int,
        limit: int,
        message: str | None = None,
    ):
        self.org_id = org_id
        self.quota_type = quota_type
        self.current = current
        self.limit = limit
        super().__init__(
            message
            or f"Quota exceeded for {org_id}: {quota_type} "
            f"({current}/{limit})"
        )


@dataclass
class TenantQuotas:
    """Quota limits for a tenant.

    All limits are optional. A None value means no limit.
    """

    max_tool_calls_per_hour: int | None = None
    max_tool_calls_per_day: int | None = None
    max_grants_per_day: int | None = None
    max_active_grants: int | None = None
    max_agents: int | None = None
    max_approvals_per_hour: int | None = None

    # Custom quotas for extensibility
    custom: dict[str, int] = field(default_factory=dict)

    def get_limit(self, quota_type: str) -> int | None:
        """Get the limit for a quota type."""
        if quota_type in self.custom:
            return self.custom[quota_type]
        return getattr(self, quota_type, None)

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_tool_calls_per_hour": self.max_tool_calls_per_hour,
            "max_tool_calls_per_day": self.max_tool_calls_per_day,
            "max_grants_per_day": self.max_grants_per_day,
            "max_active_grants": self.max_active_grants,
            "max_agents": self.max_agents,
            "max_approvals_per_hour": self.max_approvals_per_hour,
            "custom": self.custom,
        }


@dataclass
class QuotaUsage:
    """Current usage for a quota type."""

    org_id: str
    quota_type: str
    current: int
    limit: int | None
    window_start: float  # Unix timestamp
    window_seconds: int

    @property
    def remaining(self) -> int | None:
        if self.limit is None:
            return None
        return max(0, self.limit - self.current)

    @property
    def is_exceeded(self) -> bool:
        if self.limit is None:
            return False
        return self.current >= self.limit

    @property
    def reset_at(self) -> float:
        return self.window_start + self.window_seconds


@runtime_checkable
class QuotaStore(Protocol):
    """Protocol for quota storage backends."""

    async def get_usage(
        self, org_id: str, quota_type: str, window_seconds: int
    ) -> int:
        """Get current usage count for a quota type within the window."""
        ...

    async def increment(
        self, org_id: str, quota_type: str, window_seconds: int, amount: int = 1
    ) -> int:
        """Increment usage and return new count."""
        ...

    async def reset(self, org_id: str, quota_type: str | None = None) -> None:
        """Reset usage for an org. If quota_type is None, reset all."""
        ...


class InMemoryQuotaStore:
    """In-memory quota store with sliding window tracking.

    Suitable for single-process deployments. For distributed deployments,
    use RedisQuotaStore instead.
    """

    def __init__(self):
        # Structure: {org_id: {quota_type: [(timestamp, amount), ...]}}
        self._usage: dict[str, dict[str, list[tuple[float, int]]]] = defaultdict(
            lambda: defaultdict(list)
        )
        self._lock = asyncio.Lock()

    async def get_usage(
        self, org_id: str, quota_type: str, window_seconds: int
    ) -> int:
        async with self._lock:
            return self._get_usage_sync(org_id, quota_type, window_seconds)

    def _get_usage_sync(
        self, org_id: str, quota_type: str, window_seconds: int
    ) -> int:
        now = time.time()
        cutoff = now - window_seconds

        entries = self._usage[org_id][quota_type]
        # Remove expired entries
        entries[:] = [(ts, amt) for ts, amt in entries if ts > cutoff]

        return sum(amt for _, amt in entries)

    async def increment(
        self, org_id: str, quota_type: str, window_seconds: int, amount: int = 1
    ) -> int:
        async with self._lock:
            now = time.time()
            cutoff = now - window_seconds

            entries = self._usage[org_id][quota_type]
            # Remove expired entries
            entries[:] = [(ts, amt) for ts, amt in entries if ts > cutoff]

            # Add new entry
            entries.append((now, amount))

            return sum(amt for _, amt in entries)

    async def reset(self, org_id: str, quota_type: str | None = None) -> None:
        async with self._lock:
            if quota_type is None:
                self._usage.pop(org_id, None)
            elif org_id in self._usage:
                self._usage[org_id].pop(quota_type, None)


class QuotaEnforcer:
    """Enforces tenant quotas before operations.

    Args:
        store: The QuotaStore backend.
        default_quotas: Default quotas for tenants without custom limits.
    """

    # Window sizes for different quota types (in seconds)
    WINDOW_SIZES = {
        "max_tool_calls_per_hour": 3600,
        "max_tool_calls_per_day": 86400,
        "max_grants_per_day": 86400,
        "max_approvals_per_hour": 3600,
    }

    def __init__(
        self,
        store: QuotaStore,
        default_quotas: TenantQuotas | None = None,
    ):
        self._store = store
        self._default_quotas = default_quotas or TenantQuotas()
        self._tenant_quotas: dict[str, TenantQuotas] = {}

    def set_tenant_quotas(self, org_id: str, quotas: TenantQuotas) -> None:
        """Set custom quotas for a tenant."""
        self._tenant_quotas[org_id] = quotas

    def get_quotas(self, org_id: str) -> TenantQuotas:
        """Get quotas for a tenant (custom or default)."""
        return self._tenant_quotas.get(org_id, self._default_quotas)

    async def check_quota(self, org_id: str, quota_type: str) -> QuotaUsage:
        """Check current quota usage without incrementing.

        Args:
            org_id: The tenant organization ID.
            quota_type: The quota type to check.

        Returns:
            QuotaUsage with current usage and limit.
        """
        quotas = self.get_quotas(org_id)
        limit = quotas.get_limit(quota_type)
        window = self.WINDOW_SIZES.get(quota_type, 3600)

        current = await self._store.get_usage(org_id, quota_type, window)

        return QuotaUsage(
            org_id=org_id,
            quota_type=quota_type,
            current=current,
            limit=limit,
            window_start=time.time() - window,
            window_seconds=window,
        )

    async def check_and_increment(
        self,
        org_id: str,
        quota_type: str,
        amount: int = 1,
        raise_on_exceeded: bool = False,
    ) -> bool:
        """Check if quota allows operation and increment if so.

        Args:
            org_id: The tenant organization ID.
            quota_type: The quota type to check.
            amount: Amount to increment by.
            raise_on_exceeded: If True, raise QuotaExceededError instead
                of returning False.

        Returns:
            True if operation is allowed, False if quota exceeded.

        Raises:
            QuotaExceededError: If raise_on_exceeded is True and quota
                is exceeded.
        """
        quotas = self.get_quotas(org_id)
        limit = quotas.get_limit(quota_type)

        if limit is None:
            # No limit, always allow
            return True

        window = self.WINDOW_SIZES.get(quota_type, 3600)
        current = await self._store.get_usage(org_id, quota_type, window)

        if current + amount > limit:
            if raise_on_exceeded:
                raise QuotaExceededError(
                    org_id=org_id,
                    quota_type=quota_type,
                    current=current,
                    limit=limit,
                )
            return False

        await self._store.increment(org_id, quota_type, window, amount)
        return True

    async def get_all_usage(self, org_id: str) -> dict[str, QuotaUsage]:
        """Get usage for all quota types for a tenant."""
        quotas = self.get_quotas(org_id)
        result = {}

        for quota_type in self.WINDOW_SIZES:
            limit = quotas.get_limit(quota_type)
            if limit is not None:
                result[quota_type] = await self.check_quota(org_id, quota_type)

        return result

    async def reset_usage(
        self, org_id: str, quota_type: str | None = None
    ) -> None:
        """Reset usage for a tenant."""
        await self._store.reset(org_id, quota_type)
