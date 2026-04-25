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

Connectors provided:
  - MockGatewayConnector  — in-memory dict (testing / sandbox)
  - HttpGateway           — async HTTP REST connector (httpx)
  - MultiGateway          — routes by source prefix to different connectors

Implementing a custom connector:
    class ParticipantDataConnector(GatewayConnector):
        async def fetch(self, request: DataRequest) -> DataResponse:
            data = await your_db.query(request.params)
            return DataResponse(source=request.source, data=data)

Usage — HttpGateway (single REST backend):

    from arc.core.gateway import HttpGateway

    gateway = HttpGateway(
        base_url="https://api.internal.company.com",
        headers={"Authorization": "Bearer {token}"},
        timeout=10.0,
    )
    # In execute(): data = await self.gateway.fetch(DataRequest("participant.data", {"id": "p-001"}))

Usage — MultiGateway (route to different backends by source prefix):

    from arc.core.gateway import MultiGateway, HttpGateway, MockGatewayConnector

    gateway = MultiGateway({
        "participant": HttpGateway("https://participant-api.internal.com"),
        "plan":        HttpGateway("https://plan-api.internal.com"),
        "market":      MockGatewayConnector({"market.data": {...}}),
    })
    # Sources "participant.data", "participant.account" → participant connector
    # Sources "plan.data"                              → plan connector
"""

import asyncio
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


# ── HttpGateway ────────────────────────────────────────────────────────────────


class HttpGateway:
    """
    Async HTTP REST connector for Foundry agents.

    Maps a DataRequest to a GET/POST call against a REST API.
    Requires httpx: pip install httpx (or pip install 'arc-core[http]').

    Request mapping:
        - source is appended to base_url as a path segment:
            base_url="https://api.example.com", source="participant.data"
            → GET https://api.example.com/participant.data?<params>
        - All params are sent as query string parameters (GET) by default.
        - Set method="POST" to send params as a JSON body instead.

    Args:
        base_url:    Root URL (no trailing slash needed).
        headers:     Default headers included in every request.
        timeout:     Request timeout in seconds (default: 30).
        method:      HTTP method: "GET" (default) or "POST".
        source_key:  If set, overrides how source is added to the URL.
                     "path" (default): appended as URL path segment.
                     "param": sent as a query parameter named `source`.
        retries:     Number of retry attempts on transient errors (default: 2).
        verify_ssl:  Whether to verify TLS certificates (default: True).

    Usage:
        gateway = HttpGateway(
            base_url="https://participant-api.internal.company.com/v1",
            headers={"Authorization": "Bearer my-service-token"},
            timeout=10.0,
        )
        agent = MyAgent(manifest, tower, gateway=gateway)
    """

    def __init__(
        self,
        base_url: str,
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
        method: str = "GET",
        source_key: str = "path",
        retries: int = 2,
        verify_ssl: bool = True,
    ):
        self.base_url   = base_url.rstrip("/")
        self.headers    = headers or {}
        self.timeout    = timeout
        self.method     = method.upper()
        self.source_key = source_key
        self.retries    = retries
        self.verify_ssl = verify_ssl
        self._client: Any = None

    def _get_client(self) -> Any:
        """Lazy-init httpx.AsyncClient."""
        if self._client is None:
            try:
                import httpx
            except ImportError as exc:
                raise ImportError(
                    "httpx is required for HttpGateway. "
                    "Run: pip install httpx  or  pip install 'arc-core[http]'"
                ) from exc
            self._client = httpx.AsyncClient(
                headers=self.headers,
                timeout=self.timeout,
                verify=self.verify_ssl,
            )
        return self._client

    def _build_url(self, request: "DataRequest") -> str:
        if self.source_key == "path":
            return f"{self.base_url}/{request.source}"
        return self.base_url

    async def fetch(self, request: "DataRequest") -> "DataResponse":
        """
        Fetch data via HTTP.

        GET  → params sent as query string: ?param1=val&param2=val
        POST → params sent as JSON body

        Raises:
            RuntimeError: On HTTP errors or non-2xx responses.
        """
        client = self._get_client()
        url    = self._build_url(request)
        params = request.params

        last_exc: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                if self.method == "GET":
                    response = await client.get(url, params=params)
                else:
                    response = await client.post(url, json=params)

                response.raise_for_status()
                data = response.json()

                logger.debug(
                    "HttpGateway fetch: source=%s url=%s status=%d",
                    request.source, url, response.status_code,
                )
                return DataResponse(
                    source=request.source,
                    data=data,
                    cached=False,
                    meta={
                        "status_code": response.status_code,
                        "url":         url,
                        "attempt":     attempt + 1,
                    },
                )

            except Exception as exc:
                last_exc = exc
                if attempt < self.retries:
                    wait = 0.5 * (2 ** attempt)   # exponential back-off: 0.5s, 1s
                    logger.warning(
                        "HttpGateway attempt %d/%d failed for source=%s: %s — retrying in %.1fs",
                        attempt + 1, self.retries + 1, request.source, exc, wait,
                    )
                    await asyncio.sleep(wait)

        raise RuntimeError(
            f"HttpGateway: all {self.retries + 1} attempts failed for source='{request.source}': {last_exc}"
        ) from last_exc

    async def close(self) -> None:
        """Close the underlying httpx client. Call on agent shutdown."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def __repr__(self) -> str:
        return f"HttpGateway(base_url={self.base_url!r}, method={self.method})"


# ── MultiGateway ───────────────────────────────────────────────────────────────


class MultiGateway:
    """
    Routes DataRequests to different connectors by source prefix.

    Enables a single agent to draw data from multiple backends — for
    example, one REST API for participant data and another for market
    data, while using MockGatewayConnector in tests.

    Routing rules:
        The connector whose key is the longest prefix match of
        ``request.source`` is selected. A key of ``""`` (empty string)
        acts as a catch-all default if no prefix matches.

    Args:
        connectors:  Dict of {prefix: connector} where prefix is matched
                     against the start of DataRequest.source.
        default:     Optional fallback connector if no prefix matches.
                     (Equivalent to adding an empty-string key.)

    Usage:
        gateway = MultiGateway(
            connectors={
                "participant": HttpGateway("https://participant-api.internal.com"),
                "plan":        HttpGateway("https://plan-api.internal.com"),
                "benchmark":   MockGatewayConnector({"benchmark.returns": [...]}),
            },
            default=MockGatewayConnector({}),
        )

    Raises:
        KeyError: If no matching connector is found and no default is set.
    """

    def __init__(
        self,
        connectors: dict[str, Any],
        default: Any | None = None,
    ):
        self._connectors: dict[str, Any] = dict(connectors)
        if default is not None:
            self._connectors[""] = default

    def _route(self, source: str) -> Any:
        """Return the best-matching connector for the given source."""
        best_key    = None
        best_length = -1
        for prefix, connector in self._connectors.items():
            if source.startswith(prefix) and len(prefix) > best_length:
                best_key    = prefix
                best_length = len(prefix)
        if best_key is None:
            available = list(self._connectors.keys())
            raise KeyError(
                f"MultiGateway: no connector registered for source '{source}'. "
                f"Registered prefixes: {available}"
            )
        return self._connectors[best_key]

    async def fetch(self, request: "DataRequest") -> "DataResponse":
        """Route the request to the appropriate connector and fetch."""
        connector = self._route(request.source)
        logger.debug(
            "MultiGateway routing source=%s to connector=%r",
            request.source, connector,
        )
        return await connector.fetch(request)

    def register(self, prefix: str, connector: Any) -> None:
        """Add or replace a connector for a source prefix at runtime."""
        self._connectors[prefix] = connector
        logger.debug("MultiGateway registered prefix=%r connector=%r", prefix, connector)

    def __repr__(self) -> str:
        prefixes = list(self._connectors.keys())
        return f"MultiGateway(prefixes={prefixes})"


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
