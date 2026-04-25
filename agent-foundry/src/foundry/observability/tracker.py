"""Migrated to arc.core.observability.tracker. Thin re-export shim."""

from arc.core.observability.tracker import OutcomeEvent, OutcomeTracker

__all__ = ["OutcomeTracker", "OutcomeEvent"]
