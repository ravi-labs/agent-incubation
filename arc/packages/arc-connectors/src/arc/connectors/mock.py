"""
MockTicketConnector — in-memory ticket store for harness testing.

Returns realistic mock responses matching the shape of Pega Case and
ServiceNow API responses. Used by HarnessBuilder and tests when no
real connector is available.

Usage:
    from arc.connectors.mock import MockTicketConnector

    connector = MockTicketConnector()
    case_id = await connector.create_case(
        title="Test incident",
        description="...",
        priority="P2",
    )
    case = await connector.get_case(case_id)
    assert case["status"] == "New"
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from .base import ArcConnector


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class MockTicketConnector(ArcConnector):
    """
    In-memory ticket store that mimics both Pega Case and ServiceNow APIs.

    All created tickets/incidents are stored in memory and can be
    retrieved, updated, and listed. Response shapes match the real
    connector response shapes so agent code works unchanged.

    Thread-safe for single-threaded asyncio use.
    """

    def __init__(self):
        self._cases: dict[str, dict[str, Any]] = {}
        self._incidents: dict[str, dict[str, Any]] = {}
        self._counter = 0

    def _next_id(self, prefix: str = "MOCK") -> str:
        self._counter += 1
        return f"{prefix}-{self._counter:04d}"

    # ── Pega Case API ─────────────────────────────────────────────────────────

    async def create_case(
        self,
        title: str,
        description: str,
        priority: str = "P3",
        category: str = "Incident",
        assigned_team: str = "general-support",
    ) -> str:
        """Create a mock Pega case. Returns case ID."""
        from .pega_case import _PRIORITY_MAP

        case_id = self._next_id("ITSM-WORK")
        self._cases[case_id] = {
            "ID": case_id,
            "caseID": case_id,
            "pyLabel": title,
            "pxDescription": description,
            "pxUrgencyAssigned": _PRIORITY_MAP.get(priority, "Medium"),
            "pxInternalPriority": priority,
            "pyCategory": category,
            "pxAssignedTeam": assigned_team,
            "pyStatusWork": "New",
            "pxCreateDateTime": _now_iso(),
            "pxUpdateDateTime": _now_iso(),
            "notes": [],
        }
        return case_id

    async def get_case(self, case_id: str) -> dict[str, Any]:
        """Fetch a mock Pega case. Raises KeyError if not found."""
        if case_id not in self._cases:
            raise KeyError(f"MockTicketConnector: case {case_id!r} not found")
        return dict(self._cases[case_id])

    async def update_case(
        self,
        case_id: str,
        status: str | None = None,
        notes: str | None = None,
    ) -> dict[str, Any]:
        """Update a mock Pega case. Raises KeyError if not found."""
        if case_id not in self._cases:
            raise KeyError(f"MockTicketConnector: case {case_id!r} not found")
        case = self._cases[case_id]
        if status:
            case["pyStatusWork"] = status
        if notes:
            case["notes"].append({"note": notes, "timestamp": _now_iso()})
        case["pxUpdateDateTime"] = _now_iso()
        return dict(case)

    # ── ServiceNow Table API ──────────────────────────────────────────────────

    async def create_incident(
        self,
        short_description: str,
        description: str,
        urgency: str = "P3",
        category: str = "Software",
        assignment_group: str = "general-support",
    ) -> str:
        """Create a mock ServiceNow incident. Returns sys_id."""
        from .servicenow import _URGENCY_MAP

        sys_id = str(uuid.uuid4()).replace("-", "")
        number = self._next_id("INC")
        self._incidents[sys_id] = {
            "sys_id": sys_id,
            "number": number,
            "short_description": short_description,
            "description": description,
            "urgency": str(_URGENCY_MAP.get(urgency, 3)),
            "impact": str(_URGENCY_MAP.get(urgency, 3)),
            "category": category,
            "assignment_group": assignment_group,
            "state": "1",  # New
            "state_label": "New",
            "sys_created_on": _now_iso(),
            "sys_updated_on": _now_iso(),
            "work_notes_list": [],
        }
        return sys_id

    async def get_incident(self, sys_id: str) -> dict[str, Any]:
        """Fetch a mock ServiceNow incident. Raises KeyError if not found."""
        if sys_id not in self._incidents:
            raise KeyError(f"MockTicketConnector: incident {sys_id!r} not found")
        return dict(self._incidents[sys_id])

    async def update_incident(
        self,
        sys_id: str,
        state: str | None = None,
        work_notes: str | None = None,
    ) -> dict[str, Any]:
        """Update a mock ServiceNow incident. Raises KeyError if not found."""
        from .servicenow import _STATE_MAP

        if sys_id not in self._incidents:
            raise KeyError(f"MockTicketConnector: incident {sys_id!r} not found")
        incident = self._incidents[sys_id]
        if state:
            incident["state"] = str(_STATE_MAP.get(state, state))
            incident["state_label"] = state.replace("_", " ").title()
        if work_notes:
            incident["work_notes_list"].append({
                "note": work_notes,
                "timestamp": _now_iso(),
            })
        incident["sys_updated_on"] = _now_iso()
        return dict(incident)

    # ── Shared helpers ────────────────────────────────────────────────────────

    def list_cases(self) -> list[dict[str, Any]]:
        """List all created mock cases."""
        return list(self._cases.values())

    def list_incidents(self) -> list[dict[str, Any]]:
        """List all created mock incidents."""
        return list(self._incidents.values())

    def reset(self) -> None:
        """Clear all stored cases and incidents."""
        self._cases.clear()
        self._incidents.clear()
        self._counter = 0

    # ── GatewayConnector-compatible interface ─────────────────────────────────

    async def _do_fetch(self, source: str, params: dict[str, Any]) -> Any:
        """GatewayConnector fetch interface — routes to Pega or ServiceNow shape."""
        if source.startswith("case:"):
            return await self.get_case(source.split(":", 1)[1])
        if source.startswith("incident:"):
            return await self.get_incident(source.split(":", 1)[1])
        if source == "cases":
            return self.list_cases()
        if source == "incidents":
            return self.list_incidents()
        raise ValueError(f"MockTicketConnector: unknown source {source!r}")

    async def _do_execute(self, action: str, params: dict[str, Any]) -> Any:
        """GatewayConnector execute interface."""
        if action == "create_case":
            return await self.create_case(
                title=params.get("title", params.get("short_description", "Mock ticket")),
                description=params.get("description", ""),
                priority=params.get("priority", "P3"),
                category=params.get("category", "Incident"),
                assigned_team=params.get("assigned_team", "general-support"),
            )
        if action == "create_incident":
            return await self.create_incident(
                short_description=params.get("short_description", params.get("title", "Mock incident")),
                description=params.get("description", ""),
                urgency=params.get("urgency", params.get("priority", "P3")),
                category=params.get("category", "Software"),
                assignment_group=params.get("assignment_group", params.get("assigned_team", "general-support")),
            )
        if action == "update_case":
            return await self.update_case(
                case_id=params["case_id"],
                status=params.get("status"),
                notes=params.get("notes"),
            )
        if action == "update_incident":
            return await self.update_incident(
                sys_id=params["sys_id"],
                state=params.get("state"),
                work_notes=params.get("work_notes"),
            )
        raise ValueError(f"MockTicketConnector: unknown action {action!r}")
