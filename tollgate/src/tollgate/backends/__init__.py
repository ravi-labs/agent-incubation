"""Persistent backends for Tollgate stores.

Provides drop-in replacements for InMemoryGrantStore and InMemoryApprovalStore
with persistent storage:

  - SQLiteGrantStore / SQLiteApprovalStore — zero extra dependencies
  - RedisGrantStore / RedisApprovalStore — requires ``redis[hiredis]``

Usage:

    # SQLite (zero deps, good for single-process)
    from tollgate.backends import SQLiteGrantStore, SQLiteApprovalStore

    grant_store = SQLiteGrantStore("tollgate_grants.db")
    approval_store = SQLiteApprovalStore("tollgate_approvals.db")

    # Redis (multi-process, multi-host)
    from tollgate.backends import RedisGrantStore, RedisApprovalStore

    grant_store = RedisGrantStore(redis_url="redis://localhost:6379/0")
    approval_store = RedisApprovalStore(redis_url="redis://localhost:6379/0")
"""

from .s3_audit_sink import S3AuditSink
from .sqlite_store import SQLiteApprovalStore, SQLiteGrantStore

__all__ = [
    "S3AuditSink",
    "SQLiteGrantStore",
    "SQLiteApprovalStore",
]

# Conditionally export Redis backends if redis is available
try:
    from .redis_rate_limiter import RedisRateLimiter
    from .redis_store import RedisApprovalStore, RedisGrantStore

    __all__ += ["RedisGrantStore", "RedisApprovalStore", "RedisRateLimiter"]
except ImportError:
    pass
