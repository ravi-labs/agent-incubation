"""Multi-tenancy utilities for Tollgate.

Provides tenant isolation, quota management, and tenant-scoped operations
for enterprise multi-tenant deployments.

Usage:

    from tollgate.tenancy import TenantQuotas, QuotaEnforcer, InMemoryQuotaStore

    # Define quotas
    quotas = TenantQuotas(
        max_tool_calls_per_hour=1000,
        max_grants_per_day=100,
    )

    # Create quota store and enforcer
    quota_store = InMemoryQuotaStore()
    enforcer = QuotaEnforcer(quota_store, default_quotas=quotas)

    # Check and increment quota
    allowed = await enforcer.check_and_increment("org-123", "tool_calls")
"""

from .quotas import (
    InMemoryQuotaStore,
    QuotaEnforcer,
    QuotaExceededError,
    QuotaStore,
    QuotaUsage,
    TenantQuotas,
)

__all__ = [
    "TenantQuotas",
    "QuotaUsage",
    "QuotaStore",
    "InMemoryQuotaStore",
    "QuotaEnforcer",
    "QuotaExceededError",
]
