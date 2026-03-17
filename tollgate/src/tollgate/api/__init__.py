"""REST API module for Tollgate state queries.

Provides a FastAPI-based HTTP server for querying grants, approvals,
audit events, and metrics.

Requires: pip install tollgate[api]

Example:
    from tollgate import InMemoryGrantStore, InMemoryApprovalStore
    from tollgate.api import TollgateAPI

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

try:
    from .server import TollgateAPI, create_app

    __all__ = ["TollgateAPI", "create_app"]
except ImportError as e:
    raise ImportError(
        "FastAPI/Starlette not installed. Install with: pip install tollgate[api]"
    ) from e
