"""
Tests for ComplianceEffect taxonomy.

Validates that:
  - All ComplianceEffect enum members are valid
  - Hard denies are correctly flagged DENY
  - effect_meta() resolves ComplianceEffect entries
  - Manifest loads correctly with ComplianceEffect values
  - arc.core exports ComplianceEffect
"""

import pytest
from pathlib import Path

from arc.core.effects import (
    ComplianceEffect,
    COMPLIANCE_EFFECT_METADATA,
)
from arc.core.effects import (
    DefaultDecision,
    EffectTier,
    effect_meta,
)


# ── Enum basics ───────────────────────────────────────────────────────────────

class TestComplianceEffectEnum:
    def test_enum_values_are_strings(self):
        for effect in ComplianceEffect:
            assert isinstance(effect.value, str)
            assert "." in effect.value or effect.value.islower()

    def test_tier1_effects_exist(self):
        tier1 = [
            ComplianceEffect.REGULATION_READ,
            ComplianceEffect.PLAN_DOCUMENT_READ,
            ComplianceEffect.PARTICIPANT_ELIGIBILITY_READ,
            ComplianceEffect.CONTRIBUTION_RECORD_READ,
            ComplianceEffect.VESTING_SCHEDULE_READ,
            ComplianceEffect.PLAN_TESTING_DATA_READ,
            ComplianceEffect.AUDIT_REPORT_READ,
            ComplianceEffect.RMD_RECORD_READ,
        ]
        assert len(tier1) == 8

    def test_tier2_effects_exist(self):
        tier2 = [
            ComplianceEffect.COMPLIANCE_GAP_IDENTIFY,
            ComplianceEffect.HCE_TEST_RUN,
            ComplianceEffect.ADP_ACP_TEST_RUN,
            ComplianceEffect.RMD_CALCULATE,
            ComplianceEffect.VESTING_CALCULATE,
            ComplianceEffect.DEADLINE_CALCULATE,
            ComplianceEffect.PLAN_LIMIT_CHECK,
            ComplianceEffect.FIDELITY_BOND_CHECK,
        ]
        assert len(tier2) == 8

    def test_tier3_draft_effects_exist(self):
        tier3 = [
            ComplianceEffect.COMPLIANCE_REPORT_DRAFT,
            ComplianceEffect.PARTICIPANT_NOTICE_DRAFT,
            ComplianceEffect.PLAN_AMENDMENT_DRAFT,
            ComplianceEffect.IRS_FILING_DRAFT,
            ComplianceEffect.DOL_FILING_DRAFT,
        ]
        assert len(tier3) == 5

    def test_tier4_output_effects_exist(self):
        tier4 = [
            ComplianceEffect.COMPLIANCE_ALERT_SEND,
            ComplianceEffect.PARTICIPANT_NOTICE_SEND,
            ComplianceEffect.PLAN_SPONSOR_NOTIFY,
            ComplianceEffect.EXTERNAL_COUNSEL_NOTIFY,
            ComplianceEffect.HUMAN_REVIEW_QUEUE_ADD,
        ]
        assert len(tier4) == 5

    def test_hard_deny_effects_exist(self):
        hard_denies = [
            ComplianceEffect.REGULATORY_FILING_SUBMIT,
            ComplianceEffect.PLAN_AMENDMENT_EXECUTE,
            ComplianceEffect.PLAN_TERMINATION_EXECUTE,
            ComplianceEffect.PROHIBITED_TRANSACTION_EXECUTE,
        ]
        assert len(hard_denies) == 4

    def test_no_duplicate_values(self):
        values = [e.value for e in ComplianceEffect]
        assert len(values) == len(set(values)), "Duplicate effect values found"


# ── Metadata completeness ─────────────────────────────────────────────────────

class TestComplianceEffectMetadata:
    def test_every_effect_has_metadata(self):
        """All ComplianceEffect members must have a metadata entry."""
        for effect in ComplianceEffect:
            assert effect in COMPLIANCE_EFFECT_METADATA, (
                f"ComplianceEffect.{effect.name} missing from COMPLIANCE_EFFECT_METADATA"
            )

    def test_tier1_effects_are_allow(self):
        """All Tier 1 (Data Access) effects should be ALLOW by default."""
        tier1_effects = [
            ComplianceEffect.REGULATION_READ,
            ComplianceEffect.PLAN_DOCUMENT_READ,
            ComplianceEffect.PARTICIPANT_ELIGIBILITY_READ,
            ComplianceEffect.CONTRIBUTION_RECORD_READ,
            ComplianceEffect.VESTING_SCHEDULE_READ,
            ComplianceEffect.PLAN_TESTING_DATA_READ,
            ComplianceEffect.AUDIT_REPORT_READ,
            ComplianceEffect.RMD_RECORD_READ,
        ]
        for effect in tier1_effects:
            meta = COMPLIANCE_EFFECT_METADATA[effect]
            assert meta.tier == EffectTier.DATA_ACCESS, f"{effect.name} should be DATA_ACCESS tier"
            assert meta.default_decision == DefaultDecision.ALLOW, (
                f"{effect.name} should have ALLOW default decision"
            )

    def test_tier2_computation_effects_are_allow(self):
        """All Tier 2 (Computation) effects should be ALLOW by default."""
        tier2_effects = [
            ComplianceEffect.COMPLIANCE_GAP_IDENTIFY,
            ComplianceEffect.HCE_TEST_RUN,
            ComplianceEffect.ADP_ACP_TEST_RUN,
            ComplianceEffect.RMD_CALCULATE,
            ComplianceEffect.VESTING_CALCULATE,
            ComplianceEffect.DEADLINE_CALCULATE,
            ComplianceEffect.PLAN_LIMIT_CHECK,
            ComplianceEffect.FIDELITY_BOND_CHECK,
        ]
        for effect in tier2_effects:
            meta = COMPLIANCE_EFFECT_METADATA[effect]
            assert meta.tier == EffectTier.COMPUTATION, f"{effect.name} should be COMPUTATION tier"
            assert meta.default_decision == DefaultDecision.ALLOW, (
                f"{effect.name} should have ALLOW default"
            )

    def test_tier3_draft_effects_are_allow(self):
        """All Tier 3 (Draft) effects should be ALLOW by default."""
        tier3_effects = [
            ComplianceEffect.COMPLIANCE_REPORT_DRAFT,
            ComplianceEffect.PARTICIPANT_NOTICE_DRAFT,
            ComplianceEffect.PLAN_AMENDMENT_DRAFT,
            ComplianceEffect.IRS_FILING_DRAFT,
            ComplianceEffect.DOL_FILING_DRAFT,
        ]
        for effect in tier3_effects:
            meta = COMPLIANCE_EFFECT_METADATA[effect]
            assert meta.tier == EffectTier.DRAFT, f"{effect.name} should be DRAFT tier"
            assert meta.default_decision == DefaultDecision.ALLOW, (
                f"{effect.name} should have ALLOW default"
            )


# ── Hard denies ───────────────────────────────────────────────────────────────

class TestComplianceHardDenies:
    HARD_DENY_EFFECTS = [
        ComplianceEffect.REGULATORY_FILING_SUBMIT,
        ComplianceEffect.PLAN_AMENDMENT_EXECUTE,
        ComplianceEffect.PLAN_TERMINATION_EXECUTE,
        ComplianceEffect.PROHIBITED_TRANSACTION_EXECUTE,
    ]

    def test_hard_denies_are_deny(self):
        for effect in self.HARD_DENY_EFFECTS:
            meta = COMPLIANCE_EFFECT_METADATA[effect]
            assert meta.default_decision == DefaultDecision.DENY, (
                f"{effect.name} should be DENY (hard deny)"
            )

    def test_hard_denies_require_human_review(self):
        for effect in self.HARD_DENY_EFFECTS:
            meta = COMPLIANCE_EFFECT_METADATA[effect]
            assert meta.requires_human_review is True, (
                f"{effect.name} should require human review"
            )

    def test_policy_rule_modify_is_deny(self):
        meta = COMPLIANCE_EFFECT_METADATA[ComplianceEffect.POLICY_RULE_MODIFY]
        assert meta.default_decision == DefaultDecision.DENY

    def test_regulatory_filing_submit_description_explains_why(self):
        meta = COMPLIANCE_EFFECT_METADATA[ComplianceEffect.REGULATORY_FILING_SUBMIT]
        desc = meta.description.lower()
        assert "plan administrator" in desc or "signed" in desc, (
            "REGULATORY_FILING_SUBMIT description should explain why it's denied"
        )

    def test_prohibited_transaction_mentions_erisa(self):
        meta = COMPLIANCE_EFFECT_METADATA[ComplianceEffect.PROHIBITED_TRANSACTION_EXECUTE]
        assert "erisa" in meta.description.lower() or "§406" in meta.description


# ── ASK effects ───────────────────────────────────────────────────────────────

class TestComplianceAskEffects:
    def test_participant_notice_send_is_ask(self):
        meta = COMPLIANCE_EFFECT_METADATA[ComplianceEffect.PARTICIPANT_NOTICE_SEND]
        assert meta.default_decision == DefaultDecision.ASK
        assert meta.requires_human_review is True

    def test_external_counsel_notify_is_ask(self):
        meta = COMPLIANCE_EFFECT_METADATA[ComplianceEffect.EXTERNAL_COUNSEL_NOTIFY]
        assert meta.default_decision == DefaultDecision.ASK
        assert meta.requires_human_review is True

    def test_agent_suspend_is_ask(self):
        meta = COMPLIANCE_EFFECT_METADATA[ComplianceEffect.AGENT_SUSPEND]
        assert meta.default_decision == DefaultDecision.ASK


# ── effect_meta() integration ─────────────────────────────────────────────────

class TestEffectMetaResolution:
    def test_effect_meta_resolves_compliance_effect(self):
        """effect_meta() in effects.py should find ComplianceEffect entries."""
        meta = effect_meta(ComplianceEffect.REGULATION_READ)
        assert meta.effect == ComplianceEffect.REGULATION_READ
        assert meta.tier == EffectTier.DATA_ACCESS

    def test_effect_meta_resolves_hard_deny(self):
        meta = effect_meta(ComplianceEffect.REGULATORY_FILING_SUBMIT)
        assert meta.default_decision == DefaultDecision.DENY

    def test_effect_meta_resolves_all_compliance_effects(self):
        for effect in ComplianceEffect:
            meta = effect_meta(effect)
            assert meta is not None
            assert meta.effect == effect


# ── arc.core export ───────────────────────────────────────────────────────────

class TestArcCoreExport:
    def test_arc_core_exports_compliance_effect(self):
        from arc.core import ComplianceEffect as CE
        assert CE is ComplianceEffect

    def test_arc_core_exports_compliance_metadata(self):
        from arc.core import COMPLIANCE_EFFECT_METADATA as CEM
        assert CEM is COMPLIANCE_EFFECT_METADATA

    def test_compliance_effect_in_all_list(self):
        import arc.core as ac
        assert "ComplianceEffect" in ac.__all__
        assert "COMPLIANCE_EFFECT_METADATA" in ac.__all__


# ── Manifest loading with ComplianceEffect ────────────────────────────────────

class TestManifestWithComplianceEffect:
    def test_manifest_loads_with_compliance_effects(self, tmp_path):
        """Manifest with ComplianceEffect values should load without error."""
        import yaml
        manifest_data = {
            "agent_id": "compliance-monitor",
            "version": "0.1.0",
            "owner": "compliance-team",
            "description": "Test compliance manifest",
            "lifecycle_stage": "BUILD",
            "allowed_effects": [
                "regulation.read",
                "plan.document.read",
                "compliance.gap.identify",
                "compliance.report.draft",
                "compliance.alert.send",
                "compliance.audit.log.write",
            ],
            "data_access": ["plan.data"],
            "policy_path": "policy.yaml",
            "success_metrics": ["test"],
        }
        manifest_path = tmp_path / "manifest.yaml"
        manifest_path.write_text(yaml.dump(manifest_data))

        from arc.core.manifest import load_manifest
        manifest = load_manifest(manifest_path)
        assert manifest.agent_id == "compliance-monitor"
        assert len(manifest.allowed_effects) == 6

        # Verify ComplianceEffect values parsed correctly
        effect_values = [e.value for e in manifest.allowed_effects]
        assert "regulation.read" in effect_values
        assert "compliance.gap.identify" in effect_values

    def test_manifest_itsm_still_loads(self):
        """Existing email_triage manifest with ITSMEffect values still loads."""
        foundry_manifest = (
            Path(__file__).parent.parent.parent.parent.parent.parent.parent /
            "agent-foundry" / "examples" / "email_triage" / "manifest.yaml"
        )
        if not foundry_manifest.exists():
            pytest.skip("Foundry email_triage manifest not found")

        from arc.core.manifest import load_manifest
        manifest = load_manifest(foundry_manifest)
        assert manifest.agent_id == "email-triage"
        assert len(manifest.allowed_effects) > 0
