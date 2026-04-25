"""
EffectRequestBuilder — constructs Tollgate ToolRequest objects from
typed effect values (FinancialEffect, HealthcareEffect, LegalEffect).

Agents use this builder to declare their intent without hand-crafting
raw ToolRequest objects. The builder enforces that every request:
  - Has a declared typed effect from a supported domain taxonomy
  - Carries the manifest_version (required for ALLOW decisions)
  - Maps to the correct base Effect for Tollgate's policy engine
"""

from typing import Any

from foundry.tollgate.types import Intent, ToolRequest

from .effects import effect_meta


class EffectRequestBuilder:
    """
    Builds Tollgate ToolRequest instances from typed effect declarations.

    Supports all three domain taxonomies: FinancialEffect, HealthcareEffect,
    and LegalEffect. The builder resolves metadata from the appropriate domain
    registry automatically.

    Usage:
        builder = EffectRequestBuilder(manifest_version="retirement-trajectory@1.0")
        request = builder.build(
            effect=FinancialEffect.PARTICIPANT_COMMUNICATION_SEND,
            tool="email_gateway",
            action="send",
            params={"participant_id": "p-123", "message": "..."},
        )
    """

    def __init__(self, manifest_version: str):
        """
        Args:
            manifest_version: The agent manifest version string. Required
                              for Tollgate ALLOW decisions (trusted metadata).
        """
        self.manifest_version = manifest_version

    def build(
        self,
        effect,   # FinancialEffect | HealthcareEffect | LegalEffect
        tool: str,
        action: str,
        params: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> ToolRequest:
        """
        Build a ToolRequest for the given typed effect.

        The `resource_type` is set to the effect's value string, enabling
        fine-grained YAML policy matching. The base `effect` is derived
        from the domain taxonomy metadata registry.

        Args:
            effect:   The typed domain effect (FinancialEffect, HealthcareEffect,
                      or LegalEffect) this request represents.
            tool:     The tool being invoked (e.g., "email_gateway", "gateway").
            action:   The specific action (e.g., "send", "fetch", "compute").
            params:   Tool parameters. Sensitive keys are redacted by ControlTower.
            metadata: Optional metadata passed to policy `when:` conditions.
        """
        meta = effect_meta(effect)
        return ToolRequest(
            tool=tool,
            action=action,
            resource_type=effect.value,   # ← effect value → YAML rule matching
            effect=meta.base_effect,       # ← Mapped to Tollgate base Effect
            params=params,
            metadata={
                "effect": effect.value,
                "tier": meta.tier.value,
                "requires_human_review": meta.requires_human_review,
                **(metadata or {}),
            },
            manifest_version=self.manifest_version,
        )

    def intent(self, action: str, reason: str, confidence: float | None = None) -> Intent:
        """Convenience method to build a Tollgate Intent."""
        return Intent(action=action, reason=reason, confidence=confidence)
