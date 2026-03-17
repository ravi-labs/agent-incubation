"""
Tests for the financial effects taxonomy.

Validates that:
  - All effects have complete metadata
  - Effect values are consistent with their declared tiers
  - Hard-denied effects are correctly flagged
  - Taxonomy utility functions work correctly
"""

import pytest

from foundry.policy.effects import (
    DefaultDecision,
    EffectTier,
    FinancialEffect,
    EffectMeta,
    effect_meta,
    effects_by_tier,
    effects_requiring_review,
)
from foundry.tollgate.types import Effect


# ─── Completeness ─────────────────────────────────────────────────────────────

class TestTaxonomyCompleteness:
    def test_every_effect_has_metadata(self):
        """All FinancialEffect enum members must have a metadata entry."""
        for effect in FinancialEffect:
            meta = effect_meta(effect)
            assert meta is not None, f"Missing metadata for {effect.value}"

    def test_metadata_fields_are_valid(self):
        """Every EffectMeta must have all required fields populated."""
        for effect in FinancialEffect:
            meta = effect_meta(effect)
            assert meta.effect == effect
            assert isinstance(meta.tier, EffectTier)
            assert isinstance(meta.base_effect, Effect)
            assert isinstance(meta.default_decision, DefaultDecision)
            assert meta.description, f"Empty description for {effect.value}"
            assert isinstance(meta.requires_human_review, bool)
            assert isinstance(meta.audit_required, bool)

    def test_effect_values_are_dot_notation(self):
        """Effect values should follow 'category.subcategory.action' pattern."""
        for effect in FinancialEffect:
            assert "." in effect.value, (
                f"Effect '{effect.value}' should use dot notation (e.g., 'plan.data.read')"
            )


# ─── Hard Denies ──────────────────────────────────────────────────────────────

class TestHardDenies:
    EXPECTED_HARD_DENIES = {
        FinancialEffect.ACCOUNT_TRANSACTION_EXECUTE,
        FinancialEffect.PARTICIPANT_DATA_WRITE,
        FinancialEffect.PLAN_DATA_WRITE,
        FinancialEffect.POLICY_RULE_MODIFY,
        FinancialEffect.AGENT_PROMOTE,
    }

    def test_hard_denied_effects_are_denied(self):
        """Known hard-denied effects must have DENY as default decision."""
        for effect in self.EXPECTED_HARD_DENIES:
            meta = effect_meta(effect)
            assert meta.default_decision == DefaultDecision.DENY, (
                f"Effect '{effect.value}' should be DENY but is {meta.default_decision.value}"
            )

    def test_hard_denied_effects_require_human_review(self):
        """Hard-denied effects must also require human review."""
        for effect in self.EXPECTED_HARD_DENIES:
            meta = effect_meta(effect)
            assert meta.requires_human_review, (
                f"Hard-denied effect '{effect.value}' should require human review"
            )

    def test_account_transaction_is_denied(self):
        """account.transaction.execute is the most critical hard deny."""
        meta = effect_meta(FinancialEffect.ACCOUNT_TRANSACTION_EXECUTE)
        assert meta.default_decision == DefaultDecision.DENY
        assert meta.tier == EffectTier.SYSTEM_CONTROL

    def test_participant_data_write_is_denied(self):
        meta = effect_meta(FinancialEffect.PARTICIPANT_DATA_WRITE)
        assert meta.default_decision == DefaultDecision.DENY

    def test_plan_data_write_is_denied(self):
        meta = effect_meta(FinancialEffect.PLAN_DATA_WRITE)
        assert meta.default_decision == DefaultDecision.DENY


# ─── Tier Assignments ─────────────────────────────────────────────────────────

class TestTierAssignments:
    def test_data_access_effects_are_tier_1(self):
        tier1_effects = [
            FinancialEffect.PARTICIPANT_DATA_READ,
            FinancialEffect.PLAN_DATA_READ,
            FinancialEffect.FUND_PERFORMANCE_READ,
            FinancialEffect.FUND_FEES_READ,
            FinancialEffect.MARKET_DATA_READ,
        ]
        for effect in tier1_effects:
            assert effect_meta(effect).tier == EffectTier.DATA_ACCESS, (
                f"Expected {effect.value} to be Tier 1 (DATA_ACCESS)"
            )

    def test_tier_1_effects_are_allowed_by_default(self):
        """All data access effects should be ALLOW by default."""
        for effect in effects_by_tier(EffectTier.DATA_ACCESS):
            meta = effect_meta(effect)
            assert meta.default_decision == DefaultDecision.ALLOW, (
                f"Data access effect '{effect.value}' should be ALLOW by default"
            )

    def test_tier_4_output_effects_require_scrutiny(self):
        """Output effects (Tier 4) should be ASK or DENY — never ALLOW."""
        # participant.communication.send and compliance findings should require scrutiny
        scrutinized = [
            FinancialEffect.PARTICIPANT_COMMUNICATION_SEND,
            FinancialEffect.COMPLIANCE_FINDING_EMIT_HIGH,
            FinancialEffect.ADVISOR_ESCALATION_TRIGGER,
        ]
        for effect in scrutinized:
            meta = effect_meta(effect)
            assert meta.default_decision in (DefaultDecision.ASK, DefaultDecision.DENY), (
                f"Output effect '{effect.value}' should be ASK or DENY, not ALLOW"
            )

    def test_tier_5_persistence_effects_are_allowed(self):
        """Audit/log writes must always be permitted — they cannot be blocked."""
        for effect in effects_by_tier(EffectTier.PERSISTENCE):
            meta = effect_meta(effect)
            assert meta.default_decision == DefaultDecision.ALLOW, (
                f"Persistence effect '{effect.value}' must be ALLOW — logging cannot be blocked"
            )

    def test_audit_log_never_requires_human_review(self):
        """audit.log.write must never require human review — it would block audit trails."""
        meta = effect_meta(FinancialEffect.AUDIT_LOG_WRITE)
        assert not meta.requires_human_review
        assert meta.default_decision == DefaultDecision.ALLOW


# ─── Utility Functions ────────────────────────────────────────────────────────

class TestUtilityFunctions:
    def test_effects_by_tier_returns_only_that_tier(self):
        tier1 = effects_by_tier(EffectTier.DATA_ACCESS)
        for effect in tier1:
            assert effect_meta(effect).tier == EffectTier.DATA_ACCESS

    def test_effects_by_tier_is_not_empty(self):
        for tier in EffectTier:
            assert len(effects_by_tier(tier)) > 0, f"No effects found for tier {tier.name}"

    def test_effects_requiring_review_subset(self):
        """effects_requiring_review() should be a subset of all effects."""
        review_effects = effects_requiring_review()
        all_effects = set(FinancialEffect)
        assert set(review_effects).issubset(all_effects)

    def test_effects_requiring_review_not_empty(self):
        assert len(effects_requiring_review()) > 0

    def test_all_review_effects_are_marked(self):
        """Every effect marked requires_human_review should appear in effects_requiring_review."""
        review_effects = set(effects_requiring_review())
        for effect in FinancialEffect:
            if effect_meta(effect).requires_human_review:
                assert effect in review_effects, (
                    f"Effect '{effect.value}' requires_human_review=True but not in effects_requiring_review()"
                )

    def test_effect_count_covers_all_tiers(self):
        """Sanity check: there should be effects in all 6 tiers."""
        for tier in EffectTier:
            count = len(effects_by_tier(tier))
            assert count > 0, f"Tier {tier.name} has no effects"


# ─── Computation Effects ──────────────────────────────────────────────────────

class TestComputationEffects:
    def test_computation_effects_use_read_base(self):
        """Computation effects should map to READ base effect (no side effects)."""
        computation_effects = effects_by_tier(EffectTier.COMPUTATION)
        for effect in computation_effects:
            meta = effect_meta(effect)
            assert meta.base_effect == Effect.READ, (
                f"Computation effect '{effect.value}' should use READ base effect, got {meta.base_effect}"
            )

    def test_risk_score_is_allowed(self):
        meta = effect_meta(FinancialEffect.RISK_SCORE_COMPUTE)
        assert meta.default_decision == DefaultDecision.ALLOW
        assert not meta.requires_human_review


# ─── Draft Effects ────────────────────────────────────────────────────────────

class TestDraftEffects:
    def test_draft_effects_are_allowed(self):
        """Draft effects create internal content — should be ALLOW."""
        for effect in effects_by_tier(EffectTier.DRAFT):
            meta = effect_meta(effect)
            assert meta.default_decision == DefaultDecision.ALLOW, (
                f"Draft effect '{effect.value}' should be ALLOW (nothing leaves the system)"
            )

    def test_draft_effects_do_not_require_review(self):
        """Drafts that haven't been sent don't need human review."""
        for effect in effects_by_tier(EffectTier.DRAFT):
            meta = effect_meta(effect)
            assert not meta.requires_human_review, (
                f"Draft effect '{effect.value}' should not require human review at draft stage"
            )
