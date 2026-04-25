"""
Tests for arc.core.manifest persistence (save_manifest, AgentManifest.save,
LocalFileManifestStore, DirectoryManifestStore) and the apply_decision helper
in arc.core.lifecycle.pipeline.

Covers the full "manifest write-back" round trip — produce a PromotionDecision,
apply it through a ManifestStore, and verify the new lifecycle_stage
persists across a reload.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from arc.core import (
    AgentManifest,
    AgentStatus,
    DirectoryManifestStore,
    FinancialEffect,
    GateChecker,
    LifecycleStage,
    LocalFileManifestStore,
    PromotionDecision,
    PromotionOutcome,
    PromotionRequest,
    PromotionService,
    apply_decision,
    load_manifest,
    save_manifest,
    stage_order_check,
)


# ── Fixtures ────────────────────────────────────────────────────────────────


def _make_manifest(
    agent_id: str = "test-agent",
    stage: LifecycleStage = LifecycleStage.BUILD,
) -> AgentManifest:
    return AgentManifest(
        agent_id=agent_id,
        version="0.1.0",
        owner="t",
        description="t",
        lifecycle_stage=stage,
        allowed_effects=[FinancialEffect.PARTICIPANT_DATA_READ],
        data_access=[],
        policy_path="policy.yaml",
        success_metrics=["m"],
        environment="sandbox",
    )


# ── save_manifest / round trip ──────────────────────────────────────────────


class TestSaveManifest:
    def test_round_trip_preserves_required_fields(self, tmp_path: Path):
        original = _make_manifest()
        path = tmp_path / "manifest.yaml"

        save_manifest(original, path)
        loaded = load_manifest(path)

        assert loaded.agent_id == original.agent_id
        assert loaded.version == original.version
        assert loaded.lifecycle_stage == original.lifecycle_stage
        assert loaded.allowed_effects == original.allowed_effects
        assert loaded.environment == original.environment

    def test_round_trip_preserves_optional_fields(self, tmp_path: Path):
        original = _make_manifest()
        original.tags = ["financial-services", "fiduciary"]
        original.team_repo = "https://github.com/example/agent"
        original.foundry_version = ">=0.1.0"
        original.status = AgentStatus.SUSPENDED

        path = tmp_path / "manifest.yaml"
        save_manifest(original, path)
        loaded = load_manifest(path)

        assert loaded.tags == original.tags
        assert loaded.team_repo == original.team_repo
        assert loaded.foundry_version == original.foundry_version
        assert loaded.status == AgentStatus.SUSPENDED

    def test_save_creates_parent_directories(self, tmp_path: Path):
        manifest = _make_manifest()
        deep = tmp_path / "registry" / "test-agent" / "manifest.yaml"
        save_manifest(manifest, deep)
        assert deep.exists()
        assert load_manifest(deep).agent_id == manifest.agent_id

    def test_method_form_equivalent_to_function(self, tmp_path: Path):
        manifest = _make_manifest()
        a = tmp_path / "a.yaml"
        b = tmp_path / "b.yaml"
        save_manifest(manifest, a)
        manifest.save(b)
        assert a.read_text() == b.read_text()


# ── LocalFileManifestStore ──────────────────────────────────────────────────


class TestLocalFileManifestStore:
    def test_load_save_round_trip(self, tmp_path: Path):
        path = tmp_path / "manifest.yaml"
        save_manifest(_make_manifest(), path)

        store = LocalFileManifestStore(path)
        loaded = store.load()
        assert loaded.agent_id == "test-agent"

        loaded.lifecycle_stage = LifecycleStage.VALIDATE
        store.save(loaded)
        assert store.load().lifecycle_stage == LifecycleStage.VALIDATE

    def test_load_validates_agent_id_when_provided(self, tmp_path: Path):
        path = tmp_path / "manifest.yaml"
        save_manifest(_make_manifest(agent_id="alpha"), path)

        store = LocalFileManifestStore(path)
        # Right id: ok
        assert store.load("alpha").agent_id == "alpha"
        # Wrong id: raise
        with pytest.raises(ValueError, match="alpha"):
            store.load("beta")

    def test_exists(self, tmp_path: Path):
        path = tmp_path / "manifest.yaml"
        store = LocalFileManifestStore(path)

        assert store.exists() is False
        save_manifest(_make_manifest(agent_id="alpha"), path)
        assert store.exists() is True
        assert store.exists("alpha") is True
        assert store.exists("beta") is False


# ── DirectoryManifestStore ──────────────────────────────────────────────────


class TestDirectoryManifestStore:
    def test_save_then_load_by_agent_id(self, tmp_path: Path):
        store = DirectoryManifestStore(tmp_path)
        store.save(_make_manifest(agent_id="alpha"))
        store.save(_make_manifest(agent_id="beta", stage=LifecycleStage.VALIDATE))

        # Each agent in its own subdirectory
        assert (tmp_path / "alpha" / "manifest.yaml").exists()
        assert (tmp_path / "beta"  / "manifest.yaml").exists()

        # Loading round-trips
        assert store.load("alpha").lifecycle_stage == LifecycleStage.BUILD
        assert store.load("beta").lifecycle_stage == LifecycleStage.VALIDATE

    def test_load_missing_agent_raises(self, tmp_path: Path):
        store = DirectoryManifestStore(tmp_path)
        with pytest.raises(FileNotFoundError, match="ghost"):
            store.load("ghost")

    def test_exists(self, tmp_path: Path):
        store = DirectoryManifestStore(tmp_path)
        assert store.exists("alpha") is False
        store.save(_make_manifest(agent_id="alpha"))
        assert store.exists("alpha") is True

    def test_agent_ids_lists_present_agents(self, tmp_path: Path):
        store = DirectoryManifestStore(tmp_path)
        assert store.agent_ids() == []

        store.save(_make_manifest(agent_id="alpha"))
        store.save(_make_manifest(agent_id="beta"))
        store.save(_make_manifest(agent_id="gamma"))
        assert store.agent_ids() == ["alpha", "beta", "gamma"]

    def test_agent_ids_skips_non_manifest_dirs(self, tmp_path: Path):
        store = DirectoryManifestStore(tmp_path)
        store.save(_make_manifest(agent_id="alpha"))
        # A subdirectory without a manifest.yaml shouldn't be listed
        (tmp_path / "scratch").mkdir()
        # A loose file at the root shouldn't be listed
        (tmp_path / "README.md").write_text("hi")
        assert store.agent_ids() == ["alpha"]

    def test_custom_manifest_filename(self, tmp_path: Path):
        store = DirectoryManifestStore(tmp_path, manifest_filename="agent.yaml")
        store.save(_make_manifest(agent_id="alpha"))
        assert (tmp_path / "alpha" / "agent.yaml").exists()
        assert store.load("alpha").agent_id == "alpha"


# ── apply_decision ─────────────────────────────────────────────────────────


def _approved_decision(
    agent_id: str = "test-agent",
    *,
    target: LifecycleStage = LifecycleStage.VALIDATE,
    current: LifecycleStage = LifecycleStage.BUILD,
) -> PromotionDecision:
    return PromotionDecision(
        request=PromotionRequest(
            agent_id=agent_id,
            current_stage=current,
            target_stage=target,
            requester="alice@team",
            justification="ok",
        ),
        outcome=PromotionOutcome.APPROVED,
        gate_results=[],
        reason="all gates passed",
    )


def _rejected_decision(agent_id: str = "test-agent") -> PromotionDecision:
    return PromotionDecision(
        request=PromotionRequest(
            agent_id=agent_id,
            current_stage=LifecycleStage.BUILD,
            target_stage=LifecycleStage.VALIDATE,
            requester="alice@team",
            justification="ok",
        ),
        outcome=PromotionOutcome.REJECTED,
        gate_results=[],
        reason="failed gates",
    )


def _deferred_decision(agent_id: str = "test-agent") -> PromotionDecision:
    return PromotionDecision(
        request=PromotionRequest(
            agent_id=agent_id,
            current_stage=LifecycleStage.GOVERN,
            target_stage=LifecycleStage.SCALE,
            requester="alice@team",
            justification="ok",
        ),
        outcome=PromotionOutcome.DEFERRED,
        gate_results=[],
        reason="awaiting human",
    )


class TestApplyDecision:
    def test_approved_decision_advances_stage_in_local_store(self, tmp_path: Path):
        path = tmp_path / "manifest.yaml"
        save_manifest(_make_manifest(stage=LifecycleStage.BUILD), path)

        store = LocalFileManifestStore(path)
        updated = apply_decision(_approved_decision(), store)

        assert updated is not None
        assert updated.lifecycle_stage == LifecycleStage.VALIDATE
        # Persistence: re-read from disk
        assert store.load().lifecycle_stage == LifecycleStage.VALIDATE

    def test_approved_decision_advances_stage_in_directory_store(self, tmp_path: Path):
        store = DirectoryManifestStore(tmp_path)
        store.save(_make_manifest(agent_id="alpha", stage=LifecycleStage.BUILD))

        apply_decision(_approved_decision(agent_id="alpha"), store)

        assert store.load("alpha").lifecycle_stage == LifecycleStage.VALIDATE

    def test_rejected_decision_is_no_op(self, tmp_path: Path):
        path = tmp_path / "manifest.yaml"
        save_manifest(_make_manifest(stage=LifecycleStage.BUILD), path)

        store = LocalFileManifestStore(path)
        result = apply_decision(_rejected_decision(), store)

        assert result is None
        # Stage unchanged
        assert store.load().lifecycle_stage == LifecycleStage.BUILD

    def test_deferred_decision_is_no_op(self, tmp_path: Path):
        path = tmp_path / "manifest.yaml"
        save_manifest(_make_manifest(stage=LifecycleStage.GOVERN), path)

        store = LocalFileManifestStore(path)
        result = apply_decision(_deferred_decision(), store)

        assert result is None
        # Stage stays at GOVERN — write-back deferred until human approves
        assert store.load().lifecycle_stage == LifecycleStage.GOVERN

    def test_full_pipeline_promote_then_apply(self, tmp_path: Path):
        """End-to-end: PromotionService.promote() → apply_decision() → reload."""
        path = tmp_path / "manifest.yaml"
        save_manifest(_make_manifest(stage=LifecycleStage.BUILD), path)
        store = LocalFileManifestStore(path)

        checker = GateChecker()
        checker.register(LifecycleStage.VALIDATE, stage_order_check())
        service = PromotionService(checker)

        decision = service.promote(PromotionRequest(
            agent_id="test-agent",
            current_stage=LifecycleStage.BUILD,
            target_stage=LifecycleStage.VALIDATE,
            requester="alice@team",
            justification="sandbox green",
        ))
        assert decision.approved
        apply_decision(decision, store)

        assert store.load().lifecycle_stage == LifecycleStage.VALIDATE

    def test_demote_then_apply_rolls_back_stage(self, tmp_path: Path):
        path = tmp_path / "manifest.yaml"
        save_manifest(_make_manifest(stage=LifecycleStage.SCALE), path)
        store = LocalFileManifestStore(path)

        service = PromotionService(GateChecker())
        decision = service.demote(
            agent_id="test-agent",
            from_stage=LifecycleStage.SCALE,
            to_stage=LifecycleStage.GOVERN,
            requester="anomaly-watcher",
            reason="error rate spike",
        )
        apply_decision(decision, store)

        assert store.load().lifecycle_stage == LifecycleStage.GOVERN
