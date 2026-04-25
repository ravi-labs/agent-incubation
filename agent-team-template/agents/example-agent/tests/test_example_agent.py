"""Tests for Example Agent — replace with your actual tests."""

import pytest
from pathlib import Path

from arc.core.manifest import load_manifest
from arc.core.effects import FinancialEffect


class TestManifest:
    def test_manifest_loads(self):
        manifest = load_manifest(Path(__file__).parent.parent / "manifest.yaml")
        assert manifest.agent_id == "example-agent"
        assert manifest.is_sandbox
        assert manifest.is_active

    def test_declared_effects_are_valid(self):
        manifest = load_manifest(Path(__file__).parent.parent / "manifest.yaml")
        assert len(manifest.allowed_effects) > 0
        for effect in manifest.allowed_effects:
            assert isinstance(effect, FinancialEffect)


# TODO: Add tests for your agent's execute() method
# See examples/ in agent-foundry for patterns
