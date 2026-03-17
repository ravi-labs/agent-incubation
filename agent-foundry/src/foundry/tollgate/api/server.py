"""FastAPI server for Tollgate REST API."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routes import router

if TYPE_CHECKING:
    from ..approvals import ApprovalStore
    from ..grants import GrantStore

logger = logging.getLogger("tollgate.api")


class TollgateAPI:
    """REST API server for querying Tollgate state.

    Provides endpoints for:
    - /api/v1/grants - List and query grants
    - /api/v1/approvals - List and query approval requests
    - /api/v1/audit - Query audit log (if queryable sink configured)
    - /api/v1/metrics - Aggregate metrics
    - /health - Health check endpoint

    Example:
        from tollgate.api import TollgateAPI
        from tollgate import InMemoryGrantStore, InMemoryApprovalStore

        grant_store = InMemoryGrantStore()
        approval_store = InMemoryApprovalStore()

        api = TollgateAPI(
            grant_store=grant_store,
            approval_store=approval_store,
        )

        # Run with uvicorn
        import uvicorn
        uvicorn.run(api.app, host="0.0.0.0", port=8080)
    """

    def __init__(
        self,
        grant_store: GrantStore | None = None,
        approval_store: ApprovalStore | None = None,
        audit_store: Any | None = None,
        title: str = "Tollgate API",
        version: str = "1.0.0",
        enable_cors: bool = True,
        cors_origins: list[str] | None = None,
    ):
        """Initialize the Tollgate API server.

        Args:
            grant_store: GrantStore implementation for /grants endpoints
            approval_store: ApprovalStore implementation for /approvals endpoints
            audit_store: Queryable audit store for /audit endpoints
            title: API title for OpenAPI docs
            version: API version
            enable_cors: Enable CORS middleware
            cors_origins: Allowed CORS origins (default: ["*"])
        """
        self.grant_store = grant_store
        self.approval_store = approval_store
        self.audit_store = audit_store

        self.app = FastAPI(
            title=title,
            version=version,
            description="REST API for querying Tollgate enforcement state",
        )

        # Store references in app state for route access
        self.app.state.grant_store = grant_store
        self.app.state.approval_store = approval_store
        self.app.state.audit_store = audit_store

        # Add CORS middleware
        if enable_cors:
            self.app.add_middleware(
                CORSMiddleware,
                allow_origins=cors_origins or ["*"],
                allow_credentials=True,
                allow_methods=["*"],
                allow_headers=["*"],
            )

        # Include routes
        self.app.include_router(router, prefix="/api/v1")

        # Health endpoint at root
        @self.app.get("/health")
        async def health():
            return {
                "status": "healthy",
                "stores": {
                    "grants": grant_store is not None,
                    "approvals": approval_store is not None,
                    "audit": audit_store is not None,
                },
            }


def create_app(
    grant_store: GrantStore | None = None,
    approval_store: ApprovalStore | None = None,
    audit_store: Any | None = None,
    **kwargs,
) -> FastAPI:
    """Factory function to create a Tollgate API application.

    Returns the FastAPI app instance for use with ASGI servers.
    """
    api = TollgateAPI(
        grant_store=grant_store,
        approval_store=approval_store,
        audit_store=audit_store,
        **kwargs,
    )
    return api.app
