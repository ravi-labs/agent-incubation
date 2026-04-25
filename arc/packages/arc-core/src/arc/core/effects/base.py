"""
Shared primitives for all arc effect taxonomies.

Every domain taxonomy (financial, healthcare, legal, ITSM, compliance) reuses
these types. The per-domain modules each define their own enum and metadata
dict, but the structure is uniform so cross-domain tooling (audit, dashboard,
policy evaluation) can treat any effect identically.
"""

from dataclasses import dataclass
from enum import Enum

# NOTE: import foundry's vendored tollgate.types until that vendored copy is
# itself shimmed to re-export from canonical `tollgate`. Both classes are
# identical in shape, but Python's isinstance() checks class identity, so
# importing from foundry preserves identity for foundry's existing tests.
# Switch to `from tollgate.types import Effect` once foundry/tollgate/ is
# converted to a re-export (tracked separately, not part of module 1).
from foundry.tollgate.types import Effect


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
