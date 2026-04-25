"""
Shared primitives for all arc effect taxonomies.

Every domain taxonomy (financial, healthcare, legal, ITSM, compliance) reuses
these types. The per-domain modules each define their own enum and metadata
dict, but the structure is uniform so cross-domain tooling (audit, dashboard,
policy evaluation) can treat any effect identically.
"""

from dataclasses import dataclass
from enum import Enum

from tollgate.types import Effect


class EffectTier(int, Enum):
    """Sensitivity tier. Higher = more scrutiny required."""
    DATA_ACCESS = 1
    COMPUTATION = 2
    DRAFT = 3
    OUTPUT = 4
    PERSISTENCE = 5
    SYSTEM_CONTROL = 6


class DefaultDecision(str, Enum):
    ALLOW = "ALLOW"
    ASK = "ASK"
    DENY = "DENY"


@dataclass(frozen=True)
class EffectMeta:
    """Metadata for a single effect, in any domain taxonomy.

    `effect` is intentionally untyped (any Enum) so the same dataclass works
    across FinancialEffect, HealthcareEffect, LegalEffect, ITSMEffect, and
    ComplianceEffect without runtime type juggling. Type-safe lookup against
    the right registry is handled by `arc.core.effects.effect_meta()`.
    """
    effect: Enum
    tier: EffectTier
    base_effect: Effect
    default_decision: DefaultDecision
    description: str
    requires_human_review: bool
    audit_required: bool = True
