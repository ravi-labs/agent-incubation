"""arc.core.lifecycle — incubation pipeline stages and gates.

DISCOVER → SHAPE → BUILD → VALIDATE → GOVERN → SCALE

Each stage has an entry criteria checklist and required exit artifacts;
promotion between stages is recorded on the AgentManifest.

Phase 3 work (the real promotion-with-gates system, auto-demotion on
anomaly, approval workflows) builds on top of these primitives once the
foundry → arc migration completes.
"""

from .stages import LifecycleStage, StageGate, stage_gate

__all__ = ["LifecycleStage", "StageGate", "stage_gate"]
