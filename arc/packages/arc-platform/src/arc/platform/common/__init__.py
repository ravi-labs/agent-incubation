"""arc.platform.common — shared data access + UI primitives for both dashboards.

Both ``arc.platform.dev`` and ``arc.platform.ops`` build on this module.
Keeps a single, tested data-access layer; the dashboards themselves only
contain routes + templates, no business logic.
"""

from .data import (
    AgentSummary,
    AuditEvent,
    PendingApproval,
    PlatformData,
    PlatformDataConfig,
)

# Re-export Correction so frontends + tests can import from one place.
from arc.core import Correction

__all__ = [
    "PlatformData",
    "PlatformDataConfig",
    "AgentSummary",
    "AuditEvent",
    "PendingApproval",
    "Correction",
]
