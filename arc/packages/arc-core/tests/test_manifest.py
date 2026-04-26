"""
Tests for arc.core.manifest — AgentManifest loading and validation.

Validates that:
  - Valid manifests load correctly
  - Required fields are enforced
  - Invalid effects, stages, and status values are rejected
  - Kill switch (status) works correctly
  - to_dict() round-trip is complete
"""

import pytest
import tempfile
from pathlib import Path

import yaml

from arc.core.manifest import AgentManifest, AgentStatus, load_manifest
from arc.core.effects import FinancialEffect

from arc.core.lifecycle import LifecycleStage


# ─── Fixtures ─────────────────────────────────────────────────────────────────

VALID_MANIFEST_DATA = {
    "agent_id": "test-agent",
    "version": "0.1.0",
    "owner": "test-team",
    "description": "A test agent for unit testing",
    "lifecycle_stage": "BUILD",
    "environment": "sandbox",
    "status": "active",
    "allowed_effects": [
        "participant.data.read",
        "risk.score.compute",
        "audit.log.write",
    ],
    "data_access": ["participant.data"],
    "policy_path": "tests/fixtures/policy.yaml",
    "success_metrics": ["Metric one", "Metric two"],
    "tags": ["test"],
    "team_repo": "https://github.com/test/test-agents",
    "arc_version": ">=0.1.0",
}


def write_manifest(tmp_path: Path, data: dict) -> Path:
    """Write a manifest dict to a temp YAML file and return its path."""
    p = tmp_path / "manifest.yaml"
    p.write_text(yaml.dump(data))
    return p


# ─── Happy Path ───────────────────────────────────────────────────────────────

class TestValidManifest:
    def test_load_valid_manifest(self, tmp_path):
        p = write_manifest(tmp_path, VALID_MANIFEST_DATA)
        manifest = load_manifest(p)
        assert manifest.agent_id == "test-agent"
        assert manifest.version == "0.1.0"
        assert manifest.owner == "test-team"
        assert manifest.lifecycle_stage == LifecycleStage.BUILD
        assert manifest.environment == "sandbox"
        assert manifest.status == AgentStatus.ACTIVE

    def test_allowed_effects_parsed_as_enum(self, tmp_path):
        p = write_manifest(tmp_path, VALID_MANIFEST_DATA)
        manifest = load_manifest(p)
        assert FinancialEffect.PARTICIPANT_DATA_READ in manifest.allowed_effects
        assert FinancialEffect.RISK_SCORE_COMPUTE in manifest.allowed_effects
        assert FinancialEffect.AUDIT_LOG_WRITE in manifest.allowed_effects

    def test_tags_default_to_empty_list(self, tmp_path):
        data = {**VALID_MANIFEST_DATA}
        del data["tags"]
        p = write_manifest(tmp_path, data)
        manifest = load_manifest(p)
        assert manifest.tags == []

    def test_environment_defaults_to_sandbox(self, tmp_path):
        data = {k: v for k, v in VALID_MANIFEST_DATA.items() if k != "environment"}
        p = write_manifest(tmp_path, data)
        manifest = load_manifest(p)
        assert manifest.environment == "sandbox"

    def test_status_defaults_to_active(self, tmp_path):
        data = {k: v for k, v in VALID_MANIFEST_DATA.items() if k != "status"}
        p = write_manifest(tmp_path, data)
        manifest = load_manifest(p)
        assert manifest.status == AgentStatus.ACTIVE

    def test_load_from_real_retirement_trajectory_manifest(self):
        """Integration check: the real example manifest loads correctly."""
        real_path = Path("examples/retirement_trajectory/manifest.yaml")
        if not real_path.exists():
            pytest.skip("Real manifest not available")
        manifest = load_manifest(real_path)
        assert manifest.agent_id == "retirement-trajectory"
        assert manifest.lifecycle_stage == LifecycleStage.BUILD
        assert len(manifest.allowed_effects) > 0

    def test_to_dict_contains_all_fields(self, tmp_path):
        p = write_manifest(tmp_path, VALID_MANIFEST_DATA)
        manifest = load_manifest(p)
        d = manifest.to_dict()
        assert d["agent_id"] == "test-agent"
        assert d["lifecycle_stage"] == "BUILD"
        assert d["status"] == "active"
        assert "allowed_effects" in d
        assert "team_repo" in d
        assert "arc_version" in d


class TestSLOBlock:
    """SLO block is optional; loaders + dumpers must be back-compat clean."""

    def test_no_slo_block_means_none(self, tmp_path):
        p = write_manifest(tmp_path, VALID_MANIFEST_DATA)
        manifest = load_manifest(p)
        assert manifest.slo is None
        # And to_dict() should NOT emit an empty `slo:` key on a manifest
        # that didn't declare one.
        assert "slo" not in manifest.to_dict()

    def test_slo_block_round_trips(self, tmp_path):
        from arc.core import DemotionMode

        data = {**VALID_MANIFEST_DATA}
        data["slo"] = {
            "window":     "7d",
            "min_volume": 200,
            "rules": [
                {"metric": "error_rate",     "op": "<", "threshold": 0.05},
                {"metric": "p95_latency_ms", "op": "<", "threshold": 2000},
            ],
            "demotion_mode": "auto",
        }
        p = write_manifest(tmp_path, data)
        manifest = load_manifest(p)
        assert manifest.slo is not None
        assert manifest.slo.window == "7d"
        assert manifest.slo.min_volume == 200
        assert manifest.slo.demotion_mode == DemotionMode.AUTO
        assert len(manifest.slo.rules) == 2
        # Round-trip back to dict matches what we wrote (modulo ordering).
        out = manifest.to_dict()
        assert out["slo"]["window"] == "7d"
        assert out["slo"]["demotion_mode"] == "auto"

    def test_invalid_window_rejected_at_load(self, tmp_path):
        data = {**VALID_MANIFEST_DATA}
        data["slo"] = {
            "window":     "always",     # bogus
            "min_volume": 100,
            "rules": [{"metric": "error_rate", "op": "<", "threshold": 0.05}],
        }
        p = write_manifest(tmp_path, data)
        with pytest.raises(ValueError, match="window"):
            load_manifest(p)


class TestLegacyFoundryVersionField:
    """Pre-rename manifests written with `foundry_version:` must still load.

    The field was renamed `foundry_version` → `arc_version` during the
    foundry → arc consolidation. ``load_manifest`` accepts the legacy
    key as a fallback so existing manifest.yaml files keep working.
    """

    def test_legacy_foundry_version_key_still_loads(self, tmp_path):
        legacy = {**VALID_MANIFEST_DATA}
        # Strip the new key, write the legacy one
        legacy.pop("arc_version", None)
        legacy["foundry_version"] = ">=0.1.0"

        p = write_manifest(tmp_path, legacy)
        manifest = load_manifest(p)
        assert manifest.arc_version == ">=0.1.0"

    def test_arc_version_wins_when_both_keys_present(self, tmp_path):
        data = {**VALID_MANIFEST_DATA}
        data["arc_version"] = ">=0.2.0"
        data["foundry_version"] = ">=0.1.0"   # should be ignored

        p = write_manifest(tmp_path, data)
        manifest = load_manifest(p)
        assert manifest.arc_version == ">=0.2.0"


# ─── Required Fields ──────────────────────────────────────────────────────────

class TestRequiredFields:
    REQUIRED_KEYS = [
        "agent_id", "version", "owner", "description", "lifecycle_stage",
        "allowed_effects", "data_access", "policy_path", "success_metrics",
    ]

    @pytest.mark.parametrize("missing_key", REQUIRED_KEYS)
    def test_missing_required_field_raises(self, tmp_path, missing_key):
        data = {k: v for k, v in VALID_MANIFEST_DATA.items() if k != missing_key}
        p = write_manifest(tmp_path, data)
        with pytest.raises(ValueError, match="missing required fields"):
            load_manifest(p)


# ─── Invalid Values ───────────────────────────────────────────────────────────

class TestInvalidValues:
    def test_invalid_lifecycle_stage_raises(self, tmp_path):
        data = {**VALID_MANIFEST_DATA, "lifecycle_stage": "INVALID_STAGE"}
        p = write_manifest(tmp_path, data)
        with pytest.raises(ValueError, match="Invalid lifecycle_stage"):
            load_manifest(p)

    def test_invalid_effect_raises(self, tmp_path):
        data = {**VALID_MANIFEST_DATA, "allowed_effects": ["not.a.real.effect"]}
        p = write_manifest(tmp_path, data)
        with pytest.raises(ValueError, match="Invalid effect"):
            load_manifest(p)

    def test_invalid_status_raises(self, tmp_path):
        data = {**VALID_MANIFEST_DATA, "status": "running"}
        p = write_manifest(tmp_path, data)
        with pytest.raises(ValueError, match="Invalid status"):
            load_manifest(p)

    def test_file_not_found_raises(self):
        with pytest.raises(FileNotFoundError):
            load_manifest("/tmp/nonexistent_manifest_xyz.yaml")


# ─── All Lifecycle Stages ─────────────────────────────────────────────────────

class TestLifecycleStages:
    @pytest.mark.parametrize("stage", [s.value for s in LifecycleStage])
    def test_all_stages_load_correctly(self, tmp_path, stage):
        data = {**VALID_MANIFEST_DATA, "lifecycle_stage": stage}
        p = write_manifest(tmp_path, data)
        manifest = load_manifest(p)
        assert manifest.lifecycle_stage == LifecycleStage(stage)


# ─── Kill Switch ──────────────────────────────────────────────────────────────

class TestKillSwitch:
    def test_active_status(self, tmp_path):
        p = write_manifest(tmp_path, {**VALID_MANIFEST_DATA, "status": "active"})
        manifest = load_manifest(p)
        assert manifest.status == AgentStatus.ACTIVE
        assert manifest.is_active

    def test_suspended_status(self, tmp_path):
        p = write_manifest(tmp_path, {**VALID_MANIFEST_DATA, "status": "suspended"})
        manifest = load_manifest(p)
        assert manifest.status == AgentStatus.SUSPENDED
        assert not manifest.is_active

    def test_deprecated_status(self, tmp_path):
        p = write_manifest(tmp_path, {**VALID_MANIFEST_DATA, "status": "deprecated"})
        manifest = load_manifest(p)
        assert manifest.status == AgentStatus.DEPRECATED
        assert not manifest.is_active

    @pytest.mark.parametrize("status", ["active", "suspended", "deprecated"])
    def test_all_statuses_parse(self, tmp_path, status):
        p = write_manifest(tmp_path, {**VALID_MANIFEST_DATA, "status": status})
        manifest = load_manifest(p)
        assert manifest.status.value == status


# ─── Effect Permission Checks ─────────────────────────────────────────────────

class TestEffectPermissions:
    def test_allows_declared_effect(self, tmp_path):
        p = write_manifest(tmp_path, VALID_MANIFEST_DATA)
        manifest = load_manifest(p)
        assert manifest.allows_effect(FinancialEffect.PARTICIPANT_DATA_READ)

    def test_blocks_undeclared_effect(self, tmp_path):
        p = write_manifest(tmp_path, VALID_MANIFEST_DATA)
        manifest = load_manifest(p)
        assert not manifest.allows_effect(FinancialEffect.PARTICIPANT_COMMUNICATION_SEND)

    def test_manifest_version_format(self, tmp_path):
        p = write_manifest(tmp_path, VALID_MANIFEST_DATA)
        manifest = load_manifest(p)
        assert manifest.manifest_version == "test-agent@0.1.0"


# ─── Environment Flags ────────────────────────────────────────────────────────

class TestEnvironmentFlags:
    def test_sandbox_flag(self, tmp_path):
        p = write_manifest(tmp_path, {**VALID_MANIFEST_DATA, "environment": "sandbox"})
        manifest = load_manifest(p)
        assert manifest.is_sandbox
        assert not manifest.is_production

    def test_production_flag(self, tmp_path):
        p = write_manifest(tmp_path, {**VALID_MANIFEST_DATA, "environment": "production"})
        manifest = load_manifest(p)
        assert manifest.is_production
        assert not manifest.is_sandbox
