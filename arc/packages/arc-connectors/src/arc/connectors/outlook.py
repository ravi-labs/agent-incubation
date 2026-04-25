"""
OutlookConnector — Microsoft Graph API connector for Arc.

Reads email from an Exchange Online / Microsoft 365 mailbox via
the Microsoft Graph API. Uses OAuth 2.0 client credentials flow
(app-only auth) via MSAL.

Implements the GatewayConnector-compatible interface so it can be
substituted by MockGatewayConnector in harness mode.

Usage (production):
    from arc.connectors.outlook import OutlookConnector
    from arc.runtime.config import OutlookConfig

    connector = OutlookConnector(OutlookConfig.from_env())
    messages = await connector.list_messages(max_results=25)

Usage (harness — swap to mock):
    from arc.core.gateway import MockGatewayConnector
    connector = MockGatewayConnector({"inbox": [...]})
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from .base import ArcConnector, OAuthMixin

logger = logging.getLogger(__name__)

_GRAPH_BASE = "https://graph.microsoft.com/v1.0"


class OutlookConnector(OAuthMixin, ArcConnector):
    """
    Microsoft Graph API connector for email triage.

    Capabilities:
      - list_messages()   — list inbox messages (with optional filter)
      - get_message()     — fetch full message with body
      - get_thread()      — fetch full conversation thread
      - create_draft()    — create a draft reply
      - send_draft()      — send an existing draft (Tier 4, goes through ControlTower)

    Auth: OAuth 2.0 client credentials (MSAL — app-only).
    All token management is handled by OAuthMixin.
    """

    def __init__(self, config: Any):
        """
        Args:
            config: OutlookConfig instance with tenant_id, client_id,
                    client_secret, inbox_user.
        """
        self._config = config
        self._msal_app = None   # Lazily initialised — avoids import error if msal not installed

    # ── Authentication ────────────────────────────────────────────────────────

    async def _fetch_token(self) -> dict[str, Any]:
        """Acquire token via MSAL client credentials."""
        try:
            import msal  # type: ignore[import]
        except ImportError as e:
            raise ImportError(
                "msal is required for OutlookConnector. "
                "Install with: pip install arc-connectors[outlook]"
            ) from e

        if self._msal_app is None:
            authority = f"https://login.microsoftonline.com/{self._config.tenant_id}"
            self._msal_app = msal.ConfidentialClientApplication(
                client_id=self._config.client_id,
                client_credential=self._config.client_secret,
                authority=authority,
            )

        result = self._msal_app.acquire_token_for_client(
            scopes=["https://graph.microsoft.com/.default"]
        )

        if "access_token" not in result:
            error = result.get("error_description", result.get("error", "Unknown error"))
            raise RuntimeError(f"OutlookConnector: MSAL token acquisition failed: {error}")

        return result   # MSAL returns expires_in as well

    # ── Public API ────────────────────────────────────────────────────────────

    async def list_messages(
        self,
        folder: str = "inbox",
        max_results: int = 50,
        filter_query: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        List messages from the specified folder.

        Args:
            folder:       Mailbox folder (default: "inbox")
            max_results:  Maximum number of messages to return (default: 50)
            filter_query: OData filter string, e.g. "isRead eq false"

        Returns:
            List of message dicts with id, subject, sender, receivedDateTime, bodyPreview.
        """
        user = self._config.inbox_user
        url = f"{_GRAPH_BASE}/users/{user}/mailFolders/{folder}/messages"
        params: dict[str, Any] = {"$top": max_results}
        if filter_query:
            params["$filter"] = filter_query

        token = await self._get_token()
        async with self._build_client() as client:
            resp = await client.get(url, headers=self._auth_headers(token), params=params)
            resp.raise_for_status()
            data = resp.json()
            return data.get("value", [])

    async def get_message(self, message_id: str) -> dict[str, Any]:
        """
        Fetch a full message including body content.

        Args:
            message_id: Graph API message ID.

        Returns:
            Full message dict including body.content.
        """
        user = self._config.inbox_user
        url = f"{_GRAPH_BASE}/users/{user}/messages/{message_id}"
        params = {"$select": "id,subject,from,body,receivedDateTime,conversationId,importance"}

        token = await self._get_token()
        async with self._build_client() as client:
            resp = await client.get(url, headers=self._auth_headers(token), params=params)
            resp.raise_for_status()
            return resp.json()

    async def get_thread(self, conversation_id: str) -> list[dict[str, Any]]:
        """
        Fetch all messages in a conversation thread.

        Args:
            conversation_id: The conversationId from any message in the thread.

        Returns:
            List of messages in the thread, ordered by receivedDateTime.
        """
        user = self._config.inbox_user
        url = f"{_GRAPH_BASE}/users/{user}/messages"
        params = {
            "$filter": f"conversationId eq '{conversation_id}'",
            "$orderby": "receivedDateTime asc",
            "$top": 100,
        }

        token = await self._get_token()
        async with self._build_client() as client:
            resp = await client.get(url, headers=self._auth_headers(token), params=params)
            resp.raise_for_status()
            return resp.json().get("value", [])

    async def create_draft(
        self,
        to: str | list[str],
        subject: str,
        body: str,
        reply_to_id: str | None = None,
    ) -> dict[str, Any]:
        """
        Create a draft message (not yet sent).

        Args:
            to:           Recipient email(s).
            subject:      Message subject.
            body:         HTML or plain text body.
            reply_to_id:  If replying to an existing message, provide its ID.

        Returns:
            Created draft message dict including id (use with send_draft()).
        """
        user = self._config.inbox_user
        recipients = [to] if isinstance(to, str) else to

        message_body = {
            "subject": subject,
            "body": {"contentType": "HTML", "content": body},
            "toRecipients": [
                {"emailAddress": {"address": addr}} for addr in recipients
            ],
        }

        token = await self._get_token()
        async with self._build_client() as client:
            if reply_to_id:
                url = f"{_GRAPH_BASE}/users/{user}/messages/{reply_to_id}/createReply"
                resp = await client.post(url, headers=self._auth_headers(token))
                resp.raise_for_status()
                draft = resp.json()
                # Update the draft with the provided body
                draft_id = draft["id"]
                update_url = f"{_GRAPH_BASE}/users/{user}/messages/{draft_id}"
                update_resp = await client.patch(
                    update_url,
                    headers=self._auth_headers(token),
                    json={"body": message_body["body"]},
                )
                update_resp.raise_for_status()
                return update_resp.json()
            else:
                url = f"{_GRAPH_BASE}/users/{user}/messages"
                resp = await client.post(
                    url,
                    headers=self._auth_headers(token),
                    json=message_body,
                )
                resp.raise_for_status()
                return resp.json()

    async def send_draft(self, draft_id: str) -> None:
        """
        Send a previously created draft message.

        This is a Tier 4 effect — MUST go through ControlTower before calling.

        Args:
            draft_id: The message ID of the draft to send.
        """
        user = self._config.inbox_user
        url = f"{_GRAPH_BASE}/users/{user}/messages/{draft_id}/send"

        token = await self._get_token()
        async with self._build_client() as client:
            resp = await client.post(url, headers=self._auth_headers(token))
            resp.raise_for_status()
            logger.info("OutlookConnector: sent draft %s", draft_id)

    # ── GatewayConnector-compatible interface ─────────────────────────────────

    async def _do_fetch(self, source: str, params: dict[str, Any]) -> Any:
        """GatewayConnector fetch interface."""
        if source in ("inbox", "email.inbox"):
            return await self.list_messages(
                max_results=params.get("max_results", 50),
                filter_query=params.get("filter_query"),
            )
        if source.startswith("message:"):
            return await self.get_message(source.split(":", 1)[1])
        if source.startswith("thread:"):
            return await self.get_thread(source.split(":", 1)[1])
        raise ValueError(f"OutlookConnector: unknown source {source!r}")

    async def _do_execute(self, action: str, params: dict[str, Any]) -> Any:
        """GatewayConnector execute interface."""
        if action == "send_draft":
            await self.send_draft(params["draft_id"])
            return {"status": "sent", "draft_id": params["draft_id"]}
        if action == "create_draft":
            return await self.create_draft(
                to=params["to"],
                subject=params["subject"],
                body=params["body"],
                reply_to_id=params.get("reply_to_id"),
            )
        raise ValueError(f"OutlookConnector: unknown action {action!r}")

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _auth_headers(token: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
