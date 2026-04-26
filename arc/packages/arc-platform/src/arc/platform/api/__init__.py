"""arc.platform.api — FastAPI JSON backend for the arc dashboards.

Both the ops (business-user) and dev (engineer) React frontends consume
the endpoints defined here. There's no template rendering — every
response is JSON.
"""

from .server import build_app, create_app

__all__ = ["build_app", "create_app"]
