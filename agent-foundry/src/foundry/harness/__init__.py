"""
Migrated to arc.harness (see docs/migration-plan.md, module 5).

Thin re-export shim so existing `from foundry.harness import …` imports
keep working. New code should import from arc.harness directly.
"""

from arc.harness import (
    DecisionReport,
    FixtureLoader,
    HarnessBuilder,
    SandboxApprover,
    ShadowAuditSink,
)

__all__ = [
    "HarnessBuilder",
    "SandboxApprover",
    "ShadowAuditSink",
    "DecisionReport",
    "FixtureLoader",
]
