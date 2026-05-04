"""Shared fixtures for arc-platform tests.

Builds an isolated ``PlatformData`` against a tmp_path with a couple of
manifests + audit + promotion fixtures, so tests don't depend on the
monorepo's actual state.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from arc.core import (
    AgentManifest,
    DirectoryManifestStore,
    FinancialEffect,
    GateChecker,
    JsonlPendingApprovalStore,
    JsonlPromotionAuditLog,
    LifecycleStage,
    PromotionRequest,
    PromotionService,
)
from arc.platform.common import PlatformData, PlatformDataConfig


def _make_manifest(
    agent_id: str,
    *,
    stage: LifecycleStage = LifecycleStage.BUILD,
    owner: str = "team-a",
) -> AgentManifest:
    return AgentManifest(
        agent_id=agent_id,
        version="0.1.0",
        owner=owner,
        description=f"Test agent {agent_id}",
        lifecycle_stage=stage,
        allowed_effects=[FinancialEffect.PARTICIPANT_DATA_READ],
        data_access=[],
        policy_path="policy.yaml",
        success_metrics=["m"],
    )


@pytest.fixture
def platform_data(tmp_path: Path) -> PlatformData:
    """A fully-populated PlatformData rooted at tmp_path."""
    # ── Manifests ──────────────────────────────────────────────────────
    manifest_root = tmp_path / "agents"
    store = DirectoryManifestStore(manifest_root)
    store.save(_make_manifest("alpha", stage=LifecycleStage.BUILD,    owner="team-a"))
    store.save(_make_manifest("beta",  stage=LifecycleStage.VALIDATE, owner="team-a"))
    store.save(_make_manifest("gamma", stage=LifecycleStage.SCALE,    owner="team-b"))

    # ── Runtime audit (one row per decision) ───────────────────────────
    audit_path = tmp_path / "audit.jsonl"
    rows = [
        {"timestamp": "2026-04-26T10:00:00Z", "agent_id": "alpha",
         "resource_type": "participant.data.read",  "decision": "ALLOW", "reason": "ok"},
        {"timestamp": "2026-04-26T10:01:00Z", "agent_id": "alpha",
         "resource_type": "participant.communication.send", "decision": "ASK",
         "reason": "needs review"},
        {"timestamp": "2026-04-26T10:02:00Z", "agent_id": "beta",
         "resource_type": "fiduciary.advice.render", "decision": "DENY", "reason": "hard-deny"},
    ]
    with audit_path.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")

    # ── Promotion audit + pending approvals (APPROVED + DEFERRED) ──────
    promotion_path = tmp_path / "promotions.jsonl"
    pending_path   = tmp_path / "pending-approvals.jsonl"
    audit          = JsonlPromotionAuditLog(promotion_path)
    pending_store  = JsonlPendingApprovalStore(pending_path)
    service = PromotionService(
        GateChecker(),
        audit_log=audit,
        require_human={LifecycleStage.SCALE},
        approval_store=pending_store,
    )
    # APPROVED: BUILD → VALIDATE on `beta`
    service.promote(PromotionRequest(
        agent_id="beta",
        current_stage=LifecycleStage.BUILD,
        target_stage=LifecycleStage.VALIDATE,
        requester="alice@team",
        justification="sandbox green",
    ))
    # DEFERRED: GOVERN → SCALE on `gamma` — require_human kicks in.
    # Lands in audit log + pending-approvals store.
    service.promote(PromotionRequest(
        agent_id="gamma",
        current_stage=LifecycleStage.GOVERN,
        target_stage=LifecycleStage.SCALE,
        requester="bob@team",
        justification="ROI signed off",
    ))

    corrections_path = tmp_path / "corrections.jsonl"
    config = PlatformDataConfig(
        manifest_root=manifest_root,
        audit_log_path=audit_path,
        promotion_log_path=promotion_path,
        pending_approvals_path=pending_path,
        corrections_log_path=corrections_path,
    )
    return PlatformData(config)
