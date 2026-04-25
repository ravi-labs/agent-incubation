"""
PegaCaseConnector — Pega Case Management connector for Arc.

Creates and manages ITSM cases in Pega Infinity via the Pega Case API.
Uses OAuth 2.0 client credentials for authentication.

Portable: only base_url changes per Pega instance — all other paths
are standard Pega API paths.

Usage:
    from arc.connectors.pega_case import PegaCaseConnector
    from arc.runtime.config import PegaCaseConfig

    connector = PegaCaseConnector(PegaCaseConfig.from_env())
    case_id = await connector.create_case(
        title="Portal 503 — all users affected",
        description="...",
        priority="P1",
        category="Incident",
        assigned_team="critical-incidents",
    )
"""

from __future__ import annotations

import logging
from typing import Any

from .base import ArcConnector, OAuthMixin

logger = logging.getLogger(__name__)

# Priority label mapping: internal P1-P4 → Pega urgency labels
_PRIORITY_MAP = {
    "P1": "Urgent",
    "P2": "High",
    "P3": "Medium",
    "P4": "Low",
}


class PegaCaseConnector(OAuthMixin, ArcConnector):
    """
    Pega Case Management API connector.

    Capabilities:
      - create_case()      — open a new ITSM case in Pega
      - get_case()         — fetch case details by ID
      - update_case()      — update case status and add notes
      - add_attachment()   — attach a document to a case

    Auth: OAuth 2.0 client credentials via Pega's own token endpoint.
    """

    def __init__(self, config: Any):
        """
        Args:
            config: PegaCaseConfig instance with base_url, client_id,
                    client_secret, case_type.
        """
        self._config = config

    # ── Authentication ────────────────────────────────────────────────────────

    async def _fetch_token(self) -> dict[str, Any]:
        """Acquire token from Pega OAuth2 endpoint."""
        import httpx

        url = f"{self._config.base_url}/PRRestService/oauth2/v1/token"
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
            resp.raise_for_status()
            return resp.json()

    # ── Public API ────────────────────────────────────────────────────────────

    async def create_case(
        self,
        title: str,
        description: str,
        priority: str = "P3",
        category: str = "Incident",
        assigned_team: str = "general-support",
    ) -> str:
        """
        Create a new Pega ITSM case.

        Args:
            title:          Short case title (appears in Pega case list).
            description:    Full case description / problem statement.
            priority:       P1/P2/P3/P4 (mapped to Pega urgency labels).
            category:       Case category (Incident/Request/Question/Complaint).
            assigned_team:  Team or work queue to assign the case to.

        Returns:
            The Pega case ID (e.g. "ITSM-WORK-1234").
        """
        import httpx

        url = f"{self._config.base_url}/api/application/v2/cases"
        payload = {
            "caseTypeID": self._config.case_type,
            "processID": "pyStartCase",
            "content": {
                "pxUrgencyAssigned": _PRIORITY_MAP.get(priority, "Medium"),
                "pyLabel": title[:200],
                "pxDescription": description[:4000],
                "pyCategory": category,
                "pxAssignedTeam": assigned_team,
                "pxInternalPriority": priority,
            },
        }

        token = await self._get_token()
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                url,
                json=payload,
                headers=self._auth_headers(token),
            )
            resp.raise_for_status()
            data = resp.json()
            case_id = data.get("ID", data.get("caseID", ""))
            logger.info("PegaCaseConnector: created case %s (priority=%s)", case_id, priority)
            return case_id

    async def get_case(self, case_id: str) -> dict[str, Any]:
        """
        Fetch case details from Pega.

        Args:
            case_id: The Pega case ID.

        Returns:
            Case details dict including status, assignments, and content.
        """
        import httpx

        url = f"{self._config.base_url}/api/application/v2/cases/{case_id}"
        token = await self._get_token()
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.get(url, headers=self._auth_headers(token))
            resp.raise_for_status()
            return resp.json()

    async def update_case(
        self,
        case_id: str,
        status: str | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        """
        Update case status and/or add work notes.

        Args:
            case_id: The Pega case ID.
            status:  New status string (e.g. "Resolved", "In Progress").
            notes:   Work notes to append to the case journal.

        Returns:
            Updated case details dict.
        """
        import httpx

        url = f"{self._config.base_url}/api/application/v2/cases/{case_id}"
        content: dict[str, Any] = {}
        if status:
            content["pyStatusWork"] = status
        if notes:
            content["pxNote"] = notes

        token = await self._get_token()
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.patch(
                url,
                json={"content": content},
                headers=self._auth_headers(token),
            )
            resp.raise_for_status()
            return resp.json()

    async def add_attachment(
        self,
        case_id: str,
        filename: str,
        content: bytes,
        content_type: str = "application/octet-stream",
    ) -> dict[str, Any]:
        """
        Attach a document to a Pega case.

        Args:
            case_id:      The Pega case ID.
            filename:     Filename for the attachment.
            content:      Binary content of the attachment.
            content_type: MIME type (default: application/octet-stream).

        Returns:
            Attachment metadata dict.
        """
        import httpx

        url = f"{self._config.base_url}/api/application/v2/cases/{case_id}/attachments"
        token = await self._get_token()
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Disposition": f'attachment; filename="{filename}"',
                    "Content-Type": content_type,
                },
                content=content,
            )
            resp.raise_for_status()
            return resp.json()

    # ── GatewayConnector-compatible interface ─────────────────────────────────

    async def _do_fetch(self, source: str, params: dict[str, Any]) -> Any:
        """GatewayConnector fetch interface."""
        if source.startswith("case:"):
            return await self.get_case(source.split(":", 1)[1])
        raise ValueError(f"PegaCaseConnector: unknown source {source!r}")

    async def _do_execute(self, action: str, params: dict[str, Any]) -> Any:
        """GatewayConnector execute interface."""
        if action == "create_case":
            return await self.create_case(
                title=params["title"],
                description=params["description"],
                priority=params.get("priority", "P3"),
                category=params.get("category", "Incident"),
                assigned_team=params.get("assigned_team", "general-support"),
            )
        if action == "update_case":
            return await self.update_case(
                case_id=params["case_id"],
                status=params.get("status"),
                notes=params.get("notes"),
            )
        raise ValueError(f"PegaCaseConnector: unknown action {action!r}")

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _auth_headers(token: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
