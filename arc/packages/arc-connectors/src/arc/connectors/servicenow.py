"""
ServiceNowConnector — ServiceNow Table API connector for Arc.

Creates and manages ITSM incidents in ServiceNow via the Table API.
Supports OAuth 2.0 or basic auth depending on ServiceNow instance config.

Portable: only instance_url changes per ServiceNow instance — all
other paths are standard Table API paths.

Usage:
    from arc.connectors.servicenow import ServiceNowConnector
    from arc.runtime.config import ServiceNowConfig

    connector = ServiceNowConnector(ServiceNowConfig.from_env())
    sys_id = await connector.create_incident(
        short_description="Portal 503 — all users affected",
        description="...",
        urgency="P1",
        category="Software",
        assignment_group="critical-incidents",
    )
"""

from __future__ import annotations

import logging
from typing import Any

from .base import ArcConnector, OAuthMixin

logger = logging.getLogger(__name__)

# Urgency mapping: internal P1-P4 → ServiceNow urgency integer
_URGENCY_MAP = {
    "P1": 1,  # Critical
    "P2": 2,  # High
    "P3": 3,  # Medium
    "P4": 4,  # Low
}

# State codes for ServiceNow incidents
_STATE_MAP = {
    "new":        1,
    "in_progress": 2,
    "on_hold":    3,
    "resolved":   6,
    "closed":     7,
    "cancelled":  8,
}


class ServiceNowConnector(OAuthMixin, ArcConnector):
    """
    ServiceNow Table API connector.

    Capabilities:
      - create_incident()  — open a new incident
      - get_incident()     — fetch incident by sys_id
      - update_incident()  — update state and add work notes

    Auth: OAuth 2.0 client credentials (preferred) or HTTP Basic Auth.
    """

    def __init__(self, config: Any):
        """
        Args:
            config: ServiceNowConfig instance with instance_url,
                    client_id, client_secret, table.
        """
        self._config = config

    # ── Authentication ────────────────────────────────────────────────────────

    async def _fetch_token(self) -> dict[str, Any]:
        """Acquire token from ServiceNow OAuth2 endpoint."""
        import httpx

        url = f"{self._config.instance_url}/oauth_token.do"
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                url,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._config.client_id,
                    "client_secret": self._config.client_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if resp.status_code == 404:
                # Instance may not have OAuth endpoint — will use basic auth fallback
                raise RuntimeError("ServiceNow OAuth endpoint not available")
            resp.raise_for_status()
            return resp.json()

    async def _get_auth_headers(self) -> dict[str, str]:
        """Get authorization headers — tries OAuth first, falls back to basic."""
        try:
            token = await self._get_token()
            return {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        except Exception:
            # Fallback to basic auth using client_id/secret as username/password
            import base64
            creds = base64.b64encode(
                f"{self._config.client_id}:{self._config.client_secret}".encode()
            ).decode()
            return {
                "Authorization": f"Basic {creds}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }

    # ── Public API ────────────────────────────────────────────────────────────

    async def create_incident(
        self,
        short_description: str,
        description: str,
        urgency: str = "P3",
        category: str = "Software",
        assignment_group: str = "general-support",
    ) -> str:
        """
        Create a new ServiceNow incident.

        Args:
            short_description: Brief incident title (appears in list view).
            description:       Full incident description.
            urgency:           P1/P2/P3/P4 (mapped to ServiceNow urgency 1-4).
            category:          Incident category (Software/Hardware/Network/etc.).
            assignment_group:  Assignment group name or sys_id.

        Returns:
            The ServiceNow incident sys_id.
        """
        import httpx

        table = self._config.table
        url = f"{self._config.instance_url}/api/now/table/{table}"
        payload = {
            "short_description": short_description[:160],
            "description": description[:4000],
            "urgency": str(_URGENCY_MAP.get(urgency, 3)),
            "impact": str(_URGENCY_MAP.get(urgency, 3)),
            "category": category,
            "assignment_group": assignment_group,
            "caller_id": "arc-agent",
        }

        headers = await self._get_auth_headers()
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            result = data.get("result", {})
            sys_id = result.get("sys_id", "")
            number = result.get("number", "")
            logger.info(
                "ServiceNowConnector: created incident %s (sys_id=%s, urgency=%s)",
                number, sys_id, urgency,
            )
            return sys_id

    async def get_incident(self, sys_id: str) -> dict[str, Any]:
        """
        Fetch incident details from ServiceNow.

        Args:
            sys_id: The ServiceNow incident sys_id.

        Returns:
            Incident details dict.
        """
        import httpx

        table = self._config.table
        url = f"{self._config.instance_url}/api/now/table/{table}/{sys_id}"
        headers = await self._get_auth_headers()
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            return resp.json().get("result", {})

    async def update_incident(
        self,
        sys_id: str,
        state: str | None = None,
        work_notes: str | None = None,
    ) -> dict[str, Any]:
        """
        Update incident state and/or add work notes.

        Args:
            sys_id:      The ServiceNow incident sys_id.
            state:       New state string (e.g. "in_progress", "resolved").
            work_notes:  Work notes to add to the incident activity log.

        Returns:
            Updated incident details dict.
        """
        import httpx

        table = self._config.table
        url = f"{self._config.instance_url}/api/now/table/{table}/{sys_id}"
        payload: dict[str, Any] = {}
        if state:
            payload["state"] = str(_STATE_MAP.get(state, state))
        if work_notes:
            payload["work_notes"] = work_notes

        headers = await self._get_auth_headers()
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.patch(url, json=payload, headers=headers)
            resp.raise_for_status()
            return resp.json().get("result", {})

    # ── GatewayConnector-compatible interface ─────────────────────────────────

    async def _do_fetch(self, source: str, params: dict[str, Any]) -> Any:
        """GatewayConnector fetch interface."""
        if source.startswith("incident:"):
            return await self.get_incident(source.split(":", 1)[1])
        raise ValueError(f"ServiceNowConnector: unknown source {source!r}")

    async def _do_execute(self, action: str, params: dict[str, Any]) -> Any:
        """GatewayConnector execute interface."""
        if action == "create_incident":
            return await self.create_incident(
                short_description=params["short_description"],
                description=params["description"],
                urgency=params.get("urgency", "P3"),
                category=params.get("category", "Software"),
                assignment_group=params.get("assignment_group", "general-support"),
            )
        if action == "update_incident":
            return await self.update_incident(
                sys_id=params["sys_id"],
                state=params.get("state"),
                work_notes=params.get("work_notes"),
            )
        raise ValueError(f"ServiceNowConnector: unknown action {action!r}")
