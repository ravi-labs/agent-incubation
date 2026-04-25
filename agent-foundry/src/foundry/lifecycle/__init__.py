"""
Migrated to arc.core.lifecycle (see docs/migration-plan.md, module 10).
Thin re-export shim.
"""

from arc.core.lifecycle import LifecycleStage, StageGate, stage_gate

__all__ = ["LifecycleStage", "StageGate", "stage_gate"]
