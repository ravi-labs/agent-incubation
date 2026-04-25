"""
Migrated to arc.core.manifest (see docs/migration-plan.md, module 3).

Thin re-export shim so existing `from foundry.scaffold.manifest import …`
imports keep working. New code should import from arc.core directly.
"""

from arc.core.manifest import (
    AgentManifest,
    AgentStatus,
    _parse_effect,  # used by foundry.scaffold.base — keep accessible
    load_manifest,
)

__all__ = ["AgentManifest", "AgentStatus", "load_manifest"]
