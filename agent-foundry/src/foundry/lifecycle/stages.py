"""Migrated to arc.core.lifecycle.stages. Thin re-export shim."""

from arc.core.lifecycle.stages import LifecycleStage, StageGate, stage_gate

__all__ = ["LifecycleStage", "StageGate", "stage_gate"]
