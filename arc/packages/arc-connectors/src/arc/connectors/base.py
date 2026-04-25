"""
ArcConnector — base class and token caching mixin for all Arc connectors.

Every connector inherits ArcConnector and optionally OAuthMixin for
token caching. The fetch()/execute() interface mirrors GatewayConnector
so MockGatewayConnector can substitute any connector in harness mode.
"""

from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import httpx


# ── Retry helpers ─────────────────────────────────────────────────────────────

_DEFAULT_RETRIES = 3
_DEFAULT_TIMEOUT = 30.0


async def _retry_request(
    fn,
    retries: int = _DEFAULT_RETRIES,
    base_delay: float = 0.5,
) -> Any:
    """
    Execute an async function with exponential-backoff retries.

    Args:
        fn:         Async callable that performs the HTTP request.
        retries:    Maximum retry attempts (default 3).
        base_delay: Base delay in seconds for exponential backoff.

    Returns:
        Whatever fn() returns on success.

    Raises:
        The last exception if all retries are exhausted.
    """
    last_exc: Exception | None = None
    for attempt in range(retries):
        try:
            return await fn()
        except (httpx.TimeoutException, httpx.ConnectError) as e:
            last_exc = e
            if attempt < retries - 1:
                delay = base_delay * (2 ** attempt)
                await asyncio.sleep(delay)
    raise last_exc  # type: ignore[misc]


# ── OAuth token cache ─────────────────────────────────────────────────────────

@dataclass
class _CachedToken:
    access_token: str
    expires_at: float   # Unix timestamp


class OAuthMixin:
    """
    Mixin for connectors that use OAuth 2.0 client credentials.

    Caches the access token and refreshes it 60 seconds before expiry.
    Subclasses must implement _fetch_token() which returns a dict with
    at least {"access_token": str, "expires_in": int}.
    """

    _token_cache: _CachedToken | None = None
    _token_lock: asyncio.Lock | None = None

    async def _get_token(self) -> str:
        """Return a valid access token, refreshing if necessary."""
        if self._token_lock is None:
            self._token_lock = asyncio.Lock()

        async with self._token_lock:
            now = time.time()
            if self._token_cache and now < self._token_cache.expires_at - 60:
                return self._token_cache.access_token

            token_data = await self._fetch_token()
            self._token_cache = _CachedToken(
                access_token=token_data["access_token"],
                expires_at=now + int(token_data.get("expires_in", 3600)),
            )
            return self._token_cache.access_token

    async def _fetch_token(self) -> dict[str, Any]:
        """Subclasses override this to call their OAuth token endpoint."""
        raise NotImplementedError

    def _clear_token_cache(self) -> None:
        """Force token refresh on next request."""
        self._token_cache = None


# ── Base connector ────────────────────────────────────────────────────────────

class ArcConnector(ABC):
    """
    Base class for all Arc data connectors.

    Provides:
      - fetch()   — read data from an external system
      - execute() — write/mutate data in an external system
      - retry logic and timeout handling
      - GatewayConnector-compatible interface for harness substitution

    Subclasses must implement _do_fetch() and _do_execute().
    """

    timeout: float = _DEFAULT_TIMEOUT
    retries: int = _DEFAULT_RETRIES

    @abstractmethod
    async def _do_fetch(self, source: str, params: dict[str, Any]) -> Any:
        """Internal fetch implementation — override in subclasses."""
        ...

    @abstractmethod
    async def _do_execute(self, action: str, params: dict[str, Any]) -> Any:
        """Internal execute implementation — override in subclasses."""
        ...

    async def fetch(self, source: str, params: dict[str, Any] | None = None) -> Any:
        """
        Fetch data from the connector's external system.

        Args:
            source: Logical source name (e.g. "inbox", "ticket/INC-001")
            params: Query parameters / filters

        Returns:
            Connector-specific response data (dict, list, etc.)
        """
        return await _retry_request(
            lambda: self._do_fetch(source, params or {}),
            retries=self.retries,
        )

    async def execute(self, action: str, params: dict[str, Any] | None = None) -> Any:
        """
        Execute a write/mutate action on the connector's external system.

        Args:
            action: Action name (e.g. "create_ticket", "send_draft")
            params: Action parameters

        Returns:
            Connector-specific response (created ID, updated record, etc.)
        """
        return await _retry_request(
            lambda: self._do_execute(action, params or {}),
            retries=self.retries,
        )

    def _build_client(self) -> httpx.AsyncClient:
        """Create an httpx async client with standard timeout."""
        return httpx.AsyncClient(timeout=self.timeout)
