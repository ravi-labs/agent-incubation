"""API routes for Tollgate REST endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

router = APIRouter()


# ========== Pydantic Models ==========


class GrantResponse(BaseModel):
    id: str
    agent_id: str | None
    effect: str | None
    tool: str | None
    action: str | None
    resource_type: str | None
    expires_at: float
    granted_by: str
    created_at: float
    reason: str | None = None
    org_id: str | None = None
    usage_count: int = 0


class GrantListResponse(BaseModel):
    grants: list[GrantResponse]
    total: int


class ApprovalRequestResponse(BaseModel):
    id: str
    agent_id: str
    tool: str
    action: str
    reason: str
    status: str
    created_at: float | None = None


class ApprovalListResponse(BaseModel):
    approvals: list[ApprovalRequestResponse]
    total: int


class AuditEventResponse(BaseModel):
    timestamp: str
    correlation_id: str
    agent_id: str
    tool: str
    action: str
    decision: str
    outcome: str
    org_id: str | None = None


class AuditListResponse(BaseModel):
    events: list[AuditEventResponse]
    total: int
    has_more: bool


class MetricsResponse(BaseModel):
    active_grants: int
    pending_approvals: int


# ========== Dependencies ==========


def get_grant_store(request: Request):
    store = request.app.state.grant_store
    if store is None:
        raise HTTPException(status_code=503, detail="Grant store not configured")
    return store


def get_approval_store(request: Request):
    store = request.app.state.approval_store
    if store is None:
        raise HTTPException(status_code=503, detail="Approval store not configured")
    return store


def get_audit_store(request: Request):
    store = request.app.state.audit_store
    if store is None:
        raise HTTPException(status_code=503, detail="Audit store not configured")
    return store


# ========== Grant Routes ==========


@router.get("/grants", response_model=GrantListResponse)
async def list_grants(
    agent_id: str | None = Query(None, description="Filter by agent ID"),
    org_id: str | None = Query(None, description="Filter by organization ID"),
    store=Depends(get_grant_store),
):
    """List all active grants, optionally filtered by agent or org."""
    grants = await store.list_active_grants(agent_id=agent_id, org_id=org_id)

    grant_responses = []
    for g in grants:
        usage = 0
        if hasattr(store, "get_usage_count"):
            usage = await store.get_usage_count(g.id)
        grant_responses.append(
            GrantResponse(
                id=g.id,
                agent_id=g.agent_id,
                effect=g.effect.value if g.effect else None,
                tool=g.tool,
                action=g.action,
                resource_type=g.resource_type,
                expires_at=g.expires_at,
                granted_by=g.granted_by,
                created_at=g.created_at,
                reason=g.reason,
                org_id=g.org_id,
                usage_count=usage,
            )
        )

    return GrantListResponse(grants=grant_responses, total=len(grant_responses))


@router.get("/grants/{grant_id}", response_model=GrantResponse)
async def get_grant(grant_id: str, store=Depends(get_grant_store)):
    """Get a specific grant by ID."""
    grants = await store.list_active_grants()
    for g in grants:
        if g.id == grant_id:
            usage = 0
            if hasattr(store, "get_usage_count"):
                usage = await store.get_usage_count(g.id)
            return GrantResponse(
                id=g.id,
                agent_id=g.agent_id,
                effect=g.effect.value if g.effect else None,
                tool=g.tool,
                action=g.action,
                resource_type=g.resource_type,
                expires_at=g.expires_at,
                granted_by=g.granted_by,
                created_at=g.created_at,
                reason=g.reason,
                org_id=g.org_id,
                usage_count=usage,
            )
    raise HTTPException(status_code=404, detail="Grant not found")


@router.delete("/grants/{grant_id}")
async def revoke_grant(grant_id: str, store=Depends(get_grant_store)):
    """Revoke a grant by ID."""
    revoked = await store.revoke_grant(grant_id)
    if not revoked:
        raise HTTPException(status_code=404, detail="Grant not found")
    return {"status": "revoked", "grant_id": grant_id}


# ========== Approval Routes ==========


@router.get("/approvals", response_model=ApprovalListResponse)
async def list_approvals(
    status: str | None = Query(None, description="Filter by status"),
    org_id: str | None = Query(None, description="Filter by organization ID"),
    store=Depends(get_approval_store),
):
    """List approval requests."""
    requests: list[dict[str, Any]] = []
    if hasattr(store, "list_requests"):
        requests = await store.list_requests(status=status, org_id=org_id)

    responses = [
        ApprovalRequestResponse(
            id=r.get("id", ""),
            agent_id=r.get("agent", {}).get("agent_id", ""),
            tool=r.get("tool_request", {}).get("tool", ""),
            action=r.get("tool_request", {}).get("action", ""),
            reason=r.get("reason", ""),
            status=str(r.get("outcome", "pending")),
            created_at=r.get("created_at"),
        )
        for r in requests
    ]

    return ApprovalListResponse(approvals=responses, total=len(responses))


@router.get("/approvals/{approval_id}")
async def get_approval(approval_id: str, store=Depends(get_approval_store)):
    """Get a specific approval request."""
    if not hasattr(store, "get_request"):
        raise HTTPException(
            status_code=501, detail="Approval store does not support get_request"
        )

    request = await store.get_request(approval_id)
    if request is None:
        raise HTTPException(status_code=404, detail="Approval request not found")

    return ApprovalRequestResponse(
        id=request.get("id", approval_id),
        agent_id=request.get("agent", {}).get("agent_id", ""),
        tool=request.get("tool_request", {}).get("tool", ""),
        action=request.get("tool_request", {}).get("action", ""),
        reason=request.get("reason", ""),
        status=str(request.get("outcome", "pending")),
        created_at=request.get("created_at"),
    )


# ========== Audit Routes ==========


@router.get("/audit", response_model=AuditListResponse)
async def list_audit_events(
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    agent_id: str | None = Query(None),
    org_id: str | None = Query(None),
    outcome: str | None = Query(None),
    since: str | None = Query(None, description="ISO timestamp"),
    store=Depends(get_audit_store),
):
    """Query audit events with filters."""
    if not hasattr(store, "query"):
        raise HTTPException(
            status_code=501, detail="Audit store does not support queries"
        )

    events, total, has_more = await store.query(
        limit=limit,
        offset=offset,
        agent_id=agent_id,
        org_id=org_id,
        outcome=outcome,
        since=since,
    )

    responses = [
        AuditEventResponse(
            timestamp=e.timestamp,
            correlation_id=e.correlation_id,
            agent_id=e.agent.agent_id,
            tool=e.tool_request.tool,
            action=e.tool_request.action,
            decision=e.decision.decision.value,
            outcome=e.outcome.value,
            org_id=e.agent.org_id,
        )
        for e in events
    ]

    return AuditListResponse(events=responses, total=total, has_more=has_more)


# ========== Metrics Routes ==========


@router.get("/metrics", response_model=MetricsResponse)
async def get_metrics(request: Request):
    """Get aggregate metrics about Tollgate state."""
    grant_store = request.app.state.grant_store
    approval_store = request.app.state.approval_store

    active_grants = 0
    if grant_store:
        grants = await grant_store.list_active_grants()
        active_grants = len(grants)

    pending_approvals = 0
    if approval_store and hasattr(approval_store, "count_pending"):
        pending_approvals = await approval_store.count_pending()

    return MetricsResponse(
        active_grants=active_grants,
        pending_approvals=pending_approvals,
    )
