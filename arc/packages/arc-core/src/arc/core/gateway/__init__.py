"""arc.core.gateway — data access abstraction for arc agents."""

from .base import (
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
