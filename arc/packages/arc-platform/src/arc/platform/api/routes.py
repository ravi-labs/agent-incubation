"""
arc.platform.api.routes — JSON endpoints consumed by both React frontends.

Endpoint surface:

  GET  /api/agents                  → list[AgentSummary]
  GET  /api/agents/{agent_id}       → AgentSummary
  GET  /api/agents/by-stage         → { stage: list[AgentSummary] }

  GET  /api/audit                   → list[AuditEvent]      (?limit=100, ?agent_id=)
  GET  /api/audit/summary           → counts (total/ALLOW/ASK/DENY)

  GET  /api/promotions              → list[PromotionDecisionDTO]
  GET  /api/promotions/summary      → counts (total/APPROVED/REJECTED/DEFERRED)

  GET  /api/approvals               → list[PendingApproval]

  GET  /api/health                  → liveness probe

The ``PlatformData`` instance is injected via FastAPI's dependency-injection
so tests can swap in a config that points at fixtures.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from arc.platform.common import (
    AgentSummary,
    AuditEvent,
    PendingApproval,
    PlatformData,
)


# ── Dependency injection ────────────────────────────────────────────────────


_DEFAULT_DATA: PlatformData | None = None


def get_data() -> PlatformData:
    """Default dependency — returns a process-wide ``PlatformData`` singleton.

    Tests override this via ``app.dependency_overrides[get_data] = ...``.
    """
    global _DEFAULT_DATA
    if _DEFAULT_DATA is None:
        _DEFAULT_DATA = PlatformData()
    return _DEFAULT_DATA


# ── Router ──────────────────────────────────────────────────────────────────


router = APIRouter(prefix="/api")


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# ── Agents ──────────────────────────────────────────────────────────────────


@router.get("/agents", response_model=None)
def list_agents(data: PlatformData = Depends(get_data)) -> list[dict[str, Any]]:
    return [asdict(a) for a in data.list_agents()]


@router.get("/agents/by-stage", response_model=None)
def agents_by_stage(
    data: PlatformData = Depends(get_data),
) -> dict[str, list[dict[str, Any]]]:
    return {
        stage: [asdict(a) for a in agents]
        for stage, agents in data.agents_by_stage().items()
    }


@router.get("/agents/{agent_id}", response_model=None)
def get_agent(agent_id: str, data: PlatformData = Depends(get_data)) -> dict[str, Any]:
    summary = data.get_agent(agent_id)
    if summary is None:
        raise HTTPException(status_code=404, detail=f"agent not found: {agent_id}")
    return asdict(summary)


# ── Audit (per-tool-call decisions) ─────────────────────────────────────────


@router.get("/audit", response_model=None)
def list_audit(
    limit: int = Query(default=100, ge=1, le=10_000),
    agent_id: str | None = Query(default=None),
    data: PlatformData = Depends(get_data),
) -> list[dict[str, Any]]:
    return [asdict(e) for e in data.list_audit_events(limit=limit, agent_id=agent_id)]


@router.get("/audit/summary", response_model=None)
def audit_summary(data: PlatformData = Depends(get_data)) -> dict[str, Any]:
    return data.audit_summary()


# ── Promotions (per-stage-change decisions) ─────────────────────────────────


@router.get("/promotions", response_model=None)
def list_promotions(
    agent_id: str | None = Query(default=None),
    data: PlatformData = Depends(get_data),
) -> list[dict[str, Any]]:
    return [d.to_dict() for d in data.list_promotions(agent_id=agent_id)]


@router.get("/promotions/summary", response_model=None)
def promotions_summary(data: PlatformData = Depends(get_data)) -> dict[str, int]:
    return data.promotion_summary()


# ── Approvals (DEFERRED promotion decisions) ────────────────────────────────


@router.get("/approvals", response_model=None)
def pending_approvals(data: PlatformData = Depends(get_data)) -> list[dict[str, Any]]:
    return [asdict(a) for a in data.pending_approvals()]
