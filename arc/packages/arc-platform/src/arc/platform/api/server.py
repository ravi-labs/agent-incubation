"""
arc.platform.api.server — FastAPI application factory.

The factory pattern (``build_app``) lets tests construct an app per-test
with their own ``PlatformData`` config. Production callers can use
``create_app()`` for a default-configured app.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from arc.platform.common import PlatformData

from .routes import get_data, router


def build_app(data: PlatformData | None = None) -> FastAPI:
    """Construct a FastAPI app, optionally with a custom data layer.

    In dev mode the React frontends run on different ports than the API
    (Vite default 5173/5174 vs FastAPI on 8000) so CORS is permissive on
    those origins. In production both frontends are typically mounted
    behind the same domain as the API and CORS becomes a no-op.
    """
    app = FastAPI(
        title="Arc Platform API",
        version="0.1.0",
        description="JSON backend for the arc-platform-ops + arc-platform-dev React dashboards.",
    )

    # Permissive CORS for local frontend dev servers; tighten in production
    # via ``ARC_CORS_ALLOW_ORIGINS`` (TODO when production deploy is real).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",  # frontend/ops Vite default
            "http://localhost:5174",  # frontend/dev Vite default
            "http://127.0.0.1:5173",
            "http://127.0.0.1:5174",
        ],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    if data is not None:
        # Caller supplied a configured data layer — make routes use it.
        app.dependency_overrides[get_data] = lambda: data

    app.include_router(router)
    return app


def create_app() -> FastAPI:
    """Default-configured app for production use."""
    return build_app()
