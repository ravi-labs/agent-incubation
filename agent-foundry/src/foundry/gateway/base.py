"""
Gateway — data access abstraction layer for Foundry agents.

All agent data access goes through Gateway. Agents never connect
directly to databases or APIs — they declare what they need in their
manifest and fetch it through the connector interface.

This enforces:
  - Consistent access logging across all agents
  - Centralized permission model (one place to audit data access)
  - Clean separation between agent logic and data infrastructure
  - Easy substitution of real connectors with mock data in sandbox

Implementing a connector:
    class ParticipantDataConnector(GatewayConnector):
        async def fetch(self, request: DataRequest) -> DataResponse:
            # Call your real data source here
            data = await your_db.query(request.params)
            return DataResponse(source=request.source, data=data)
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DataRequest:
    """
    A structured request for data from a named source.

    Attributes:
        source:    Named data source (must match data_access in manifest).
        params:    Query parameters (filters, IDs, date ranges, etc.).
        metadata:  Optional context for access logging.
    """
    source: str
    params: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DataResponse:
    """
    Response from a Gateway data fetch.

    Attributes:
        source:  The source that was queried.
        data:    The returned data payload.
        cached:  Whether the response was served from cache.
        meta:    Optional response metadata (record count, latency, etc.).
    """
    source: str
    data: Any
    cached: bool = False
    meta: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class GatewayConnector(Protocol):
    """
    Protocol for all Gateway data connectors.

    Connectors implement this protocol and register against named
    data sources declared in agent manifests.
    """

    async def fetch(self, request: DataRequest) -> DataResponse:
        """
        Fetch data from the connector's source.

        Args:
            request: Structured data request with source name and params.

        Returns:
            DataResponse with the fetched data payload.

        Raises:
            PermissionError: If the requested source is not permitted.
            RuntimeError:    If the data source is unavailable.
        """
        ...


class MockGatewayConnector:
    """
    In-memory mock connector for sandbox testing.

    Pre-load with test data keyed by source name. Used in sandbox
    environments to avoid any production data exposure.

    Usage:
        gateway = MockGatewayConnector({
            "participant.data": {"p-001": {"balance": 84200, "contrib_rate": 0.03}},
            "plan.data": {"plan-001": {"auto_enroll_rate": 0.03}},
        })
    """

    def __init__(self, data_store: dict[str, Any] | None = None):
        self._store: dict[str, Any] = data_store or {}

    def register(self, source: str, data: Any) -> None:
        """Register test data for a named source."""
        self._store[source] = data

    async def fetch(self, request: DataRequest) -> DataResponse:
        if request.source not in self._store:
            raise PermissionError(
                f"MockGateway: source '{request.source}' not registered. "
                f"Available: {list(self._store.keys())}"
            )
        logger.debug("MockGateway fetch: source=%s params=%s", request.source, request.params)
        return DataResponse(
            source=request.source,
            data=self._store[request.source],
            cached=False,
            meta={"mock": True},
        )
