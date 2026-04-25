"""
Migrated to arc.core.observability (see docs/migration-plan.md, module 9).
Thin re-export shim.
"""

from arc.core.observability import OutcomeEvent, OutcomeTracker, generate_report

__all__ = ["OutcomeTracker", "OutcomeEvent", "generate_report"]
