"""
Migrated to arc.core.gateway (see docs/migration-plan.md, module 6).

Thin re-export shim so existing `from foundry.gateway import …` keeps working.
New code should import from arc.core directly.
"""

from arc.core.gateway import (
    DataRequest,
    DataResponse,
    GatewayConnector,
    HttpGateway,
    MockGatewayConnector,
    MultiGateway,
)

__all__ = [
    "GatewayConnector",
    "DataRequest",
    "DataResponse",
    "MockGatewayConnector",
    "HttpGateway",
    "MultiGateway",
]
