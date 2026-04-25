"""
Tests for arc.core.policy.EffectRequestBuilder — native after migration
module 2. Foundry has equivalent coverage via the shim
(agent-foundry/tests/test_builder.py).

Validates that:
  - ToolRequests are built with correct resource_type and base effect
  - Manifest version is carried through to ToolRequest
  - Metadata is merged correctly
  - Intent objects are constructed correctly
"""

import pytest

from arc.core.policy import EffectRequestBuilder
from arc.core.effects import FinancialEffect, EffectTier, effect_meta
from tollgate.types import Effect, Intent, ToolRequest


MANIFEST_VERSION = "test-agent@1.0.0"


@pytest.fixture
def builder():
    return EffectRequestBuilder(manifest_version=MANIFEST_VERSION)


class TestToolRequestBuilding:
    def test_resource_type_is_effect_value(self, builder):
        """resource_type must equal the FinancialEffect value for YAML policy matching."""
        request = builder.build(
            effect=FinancialEffect.PARTICIPANT_DATA_READ,
            tool="data_gateway",
            action="read",
            params={"participant_id": "p-001"},
        )
        assert request.resource_type == "participant.data.read"

    def test_base_effect_is_mapped_correctly(self, builder):
        """The base effect must come from the taxonomy, not be hardcoded."""
        # Data reads should map to READ
        read_request = builder.build(
            effect=FinancialEffect.PARTICIPANT_DATA_READ,
            tool="gateway", action="read", params={},
        )
        assert read_request.effect == Effect.READ

        # Communication sends should map to NOTIFY
        notify_request = builder.build(
            effect=FinancialEffect.PARTICIPANT_COMMUNICATION_SEND,
            tool="email_gateway", action="send", params={},
        )
        assert notify_request.effect == Effect.NOTIFY

    def test_manifest_version_is_set(self, builder):
        request = builder.build(
            effect=FinancialEffect.RISK_SCORE_COMPUTE,
            tool="scorer", action="compute", params={},
        )
        assert request.manifest_version == MANIFEST_VERSION

    def test_tool_and_action_are_passed_through(self, builder):
        request = builder.build(
            effect=FinancialEffect.AUDIT_LOG_WRITE,
            tool="audit_sink",
            action="append",
            params={"event": "test"},
        )
        assert request.tool == "audit_sink"
        assert request.action == "append"

    def test_params_are_included(self, builder):
        params = {"participant_id": "p-001", "amount": 1000}
        request = builder.build(
            effect=FinancialEffect.PARTICIPANT_DATA_READ,
            tool="gateway", action="read", params=params,
        )
        assert request.params == params

    def test_metadata_is_merged_with_effect_metadata(self, builder):
        """effect and tier should always be in metadata."""
        request = builder.build(
            effect=FinancialEffect.RISK_SCORE_COMPUTE,
            tool="scorer", action="compute", params={},
        )
        assert "effect" in request.metadata
        assert "tier" in request.metadata
        assert "requires_human_review" in request.metadata
        assert request.metadata["effect"] == "risk.score.compute"
        assert request.metadata["tier"] == EffectTier.COMPUTATION.value

    def test_custom_metadata_is_merged(self, builder):
        """Custom metadata should be merged with built-in effect metadata."""
        request = builder.build(
            effect=FinancialEffect.PARTICIPANT_COMMUNICATION_SEND,
            tool="email", action="send", params={},
            metadata={"message_type": "informational", "campaign_id": "c-001"},
        )
        assert request.metadata["message_type"] == "informational"
        assert request.metadata["campaign_id"] == "c-001"
        # Built-in fields still present
        assert "effect" in request.metadata

    def test_none_metadata_is_safe(self, builder):
        """Passing metadata=None should not raise."""
        request = builder.build(
            effect=FinancialEffect.AUDIT_LOG_WRITE,
            tool="audit", action="write", params={}, metadata=None,
        )
        assert "effect" in request.metadata


class TestIntentBuilding:
    def test_intent_action_and_reason(self, builder):
        intent = builder.intent(action="score_risk", reason="Identify at-risk participants")
        assert intent.action == "score_risk"
        assert intent.reason == "Identify at-risk participants"

    def test_intent_confidence_default_none(self, builder):
        intent = builder.intent(action="test", reason="test")
        assert intent.confidence is None

    def test_intent_confidence_set(self, builder):
        intent = builder.intent(action="test", reason="test", confidence=0.92)
        assert intent.confidence == 0.92


class TestEffectCoverage:
    """Ensure the builder can construct requests for all taxonomy effects."""

    @pytest.mark.parametrize("effect", list(FinancialEffect))
    def test_can_build_request_for_any_effect(self, effect):
        builder = EffectRequestBuilder(manifest_version=f"test@1.0")
        request = builder.build(
            effect=effect,
            tool="test_tool",
            action="test_action",
            params={},
        )
        assert request.resource_type == effect.value
        assert request.manifest_version == "test@1.0"
