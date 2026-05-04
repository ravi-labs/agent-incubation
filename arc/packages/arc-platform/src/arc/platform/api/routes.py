"""
arc.platform.api.routes — JSON endpoints consumed by both React frontends.

Endpoint surface:

  GET  /api/agents                       → list[AgentSummary]
  GET  /api/agents/{agent_id}            → AgentSummary
  GET  /api/agents/by-stage              → { stage: list[AgentSummary] }
  GET  /api/agents/{agent_id}/stats      → rolling-window stats (live header)
                                           ?window_minutes=60*24
  POST /api/agents/{agent_id}/suspend    body: { reviewer, reason }
  POST /api/agents/{agent_id}/resume     body: { reviewer, reason? }

  GET  /api/audit                        → list[AuditEvent]   (?limit=100, ?agent_id=)
  GET  /api/audit/summary                → counts (total/ALLOW/ASK/DENY)

  GET  /api/promotions                   → list[PromotionDecisionDTO]
  GET  /api/promotions/summary           → counts (total/APPROVED/REJECTED/DEFERRED)

  GET  /api/approvals                    → list[PendingApproval] (PENDING only)
  GET  /api/approvals/all                → list[PendingApproval] (incl. resolved)
  POST /api/approvals/{approval_id}/decide
       body: { approve: bool, reviewer: str, reason?: str }

  GET  /api/agents/{agent_id}/corrections           list[Correction]  ?limit=  ?since=
  POST /api/agents/{agent_id}/corrections           record one
       body: { audit_row_id, reviewer, severity, reason,
               original_decision, corrected_decision,
               schema_version?, metadata? }
  GET  /api/agents/{agent_id}/corrections/summary   roll-up for the dashboard

  GET  /api/health                       → liveness probe

The ``PlatformData`` instance is injected via FastAPI's dependency-injection
so tests can swap in a config that points at fixtures.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

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


# ── Live stats (top header card on /agents/<id>/live) ──────────────────────


@router.get("/agents/{agent_id}/stats", response_model=None)
def agent_stats(
    agent_id: str,
    window_minutes: int = Query(default=60 * 24, ge=1, le=60 * 24 * 30),
    data: PlatformData = Depends(get_data),
) -> dict[str, Any]:
    """Rolling-window stats for the live page's header card."""
    if data.get_agent(agent_id) is None:
        raise HTTPException(status_code=404, detail=f"agent not found: {agent_id}")
    return data.agent_stats(agent_id, window_minutes=window_minutes)


# ── Suspend / Resume — kill switch via dashboard ────────────────────────────


class SuspendAgentRequest(BaseModel):
    reviewer: str = Field(..., min_length=1, description="Username of the operator")
    reason:   str = Field(..., min_length=1, description="Why this kill switch was fired")


class ResumeAgentRequest(BaseModel):
    reviewer: str = Field(..., min_length=1)
    reason:   str = "resumed"


@router.post("/agents/{agent_id}/suspend", response_model=None)
def suspend_agent(
    agent_id: str,
    body: SuspendAgentRequest,
    data: PlatformData = Depends(get_data),
) -> dict[str, Any]:
    try:
        return data.suspend_agent(agent_id, reviewer=body.reviewer, reason=body.reason)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.post("/agents/{agent_id}/resume", response_model=None)
def resume_agent(
    agent_id: str,
    body: ResumeAgentRequest,
    data: PlatformData = Depends(get_data),
) -> dict[str, Any]:
    try:
        return data.resume_agent(agent_id, reviewer=body.reviewer, reason=body.reason)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


# ── Corrections (feedback loop layer 1+2) ──────────────────────────────────


class RecordCorrectionRequest(BaseModel):
    audit_row_id:        str
    reviewer:            str = Field(..., min_length=1)
    severity:            str = Field(..., description='one of: minor, moderate, critical')
    reason:              str = ""
    original_decision:   dict[str, Any]
    corrected_decision:  dict[str, Any]
    schema_version:      str = ""
    metadata:            dict[str, Any] = Field(default_factory=dict)


@router.post("/agents/{agent_id}/corrections", response_model=None)
def record_correction(
    agent_id: str,
    body: RecordCorrectionRequest,
    data: PlatformData = Depends(get_data),
) -> dict[str, Any]:
    """Capture one human flag — Layer 1 of the feedback loop.

    Pure capture. Does not feed back into the agent's runtime behaviour
    yet (Layer 3 in-context injection lands as a follow-up). Surfaces
    immediately in the corrections summary endpoint + dashboards.
    """
    if data.get_agent(agent_id) is None:
        raise HTTPException(status_code=404, detail=f"agent not found: {agent_id}")
    try:
        c = data.record_correction(
            agent_id           = agent_id,
            audit_row_id       = body.audit_row_id,
            reviewer           = body.reviewer,
            severity           = body.severity,
            reason             = body.reason,
            original_decision  = body.original_decision,
            corrected_decision = body.corrected_decision,
            schema_version     = body.schema_version,
            metadata           = body.metadata,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return c.to_dict()


@router.get("/agents/{agent_id}/corrections", response_model=None)
def list_corrections(
    agent_id: str,
    limit: int = Query(default=100, ge=1, le=10_000),
    since: str | None = Query(default=None, description="ISO 8601 — strictly after"),
    data: PlatformData = Depends(get_data),
) -> list[dict[str, Any]]:
    return [c.to_dict() for c in data.list_corrections(
        agent_id=agent_id, limit=limit, since=since,
    )]


@router.get("/agents/{agent_id}/corrections/summary", response_model=None)
def corrections_summary(
    agent_id: str,
    since: str | None = Query(default=None),
    data: PlatformData = Depends(get_data),
) -> dict[str, Any]:
    return data.corrections_summary(agent_id=agent_id, since=since)


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
    """Approvals still in PENDING state — what reviewers act on."""
    return [asdict(a) for a in data.pending_approvals()]


@router.get("/approvals/all", response_model=None)
def all_approvals(data: PlatformData = Depends(get_data)) -> list[dict[str, Any]]:
    """Pending + resolved. Useful for an audit view."""
    return [asdict(a) for a in data.all_approvals()]


class ResolveApprovalRequest(BaseModel):
    """Body of ``POST /api/approvals/{approval_id}/decide``."""
    approve: bool
    reviewer: str = Field(..., min_length=1, description="Username of the reviewer.")
    reason: str = ""


@router.post("/approvals/{approval_id}/decide", response_model=None)
def decide_approval(
    approval_id: str,
    body: ResolveApprovalRequest,
    data: PlatformData = Depends(get_data),
) -> dict[str, Any]:
    """Resolve a pending DEFERRED promotion (approve or reject).

    On approve + a manifest store configured, the agent's manifest is
    updated to the target stage in the same call. On reject, the
    manifest stays put; only the audit + pending-store entries change.
    """
    try:
        return data.resolve_approval(
            approval_id,
            approve=body.approve,
            reviewer=body.reviewer,
            reason=body.reason,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
