"""SQLite-backed persistent stores for Tollgate.

Zero additional dependencies — uses Python's built-in ``sqlite3`` module.
Suitable for single-process deployments. For multi-process or multi-host
deployments, use the Redis backends instead.

Usage:

    from tollgate.backends import SQLiteGrantStore, SQLiteApprovalStore

    grant_store = SQLiteGrantStore("tollgate.db")
    approval_store = SQLiteApprovalStore("tollgate.db")

    tower = ControlTower(
        ...,
        grant_store=grant_store,
    )
"""

import asyncio
import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from ..types import AgentContext, ApprovalOutcome, Effect, Grant, ToolRequest


class SQLiteGrantStore:
    """SQLite-backed GrantStore implementation.

    Satisfies the ``GrantStore`` protocol. Uses WAL mode for concurrent
    reads. All operations run in a thread executor to avoid blocking the
    event loop.

    Args:
        db_path: Path to the SQLite database file. Use ``:memory:`` for
            testing (non-persistent).
        table_name: Name of the grants table (default ``tollgate_grants``).
    """

    def __init__(
        self,
        db_path: str | Path = "tollgate_grants.db",
        *,
        table_name: str = "tollgate_grants",
    ):
        self._db_path = str(db_path)
        self._table = table_name
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._create_table()

    def _create_table(self):
        self._conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {self._table} (
                id TEXT PRIMARY KEY,
                agent_id TEXT,
                effect TEXT,
                tool TEXT,
                action TEXT,
                resource_type TEXT,
                expires_at REAL NOT NULL,
                granted_by TEXT NOT NULL,
                created_at REAL NOT NULL,
                reason TEXT,
                usage_count INTEGER DEFAULT 0,
                revoked INTEGER DEFAULT 0
            )
        """)
        # Index for fast expiry cleanup and matching
        self._conn.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_{self._table}_expires
            ON {self._table} (expires_at, revoked)
        """)
        self._conn.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_{self._table}_agent
            ON {self._table} (agent_id, revoked)
        """)
        self._conn.commit()

    def _grant_to_row(self, grant: Grant) -> dict[str, Any]:
        return {
            "id": grant.id,
            "agent_id": grant.agent_id,
            "effect": grant.effect.value if grant.effect else None,
            "tool": grant.tool,
            "action": grant.action,
            "resource_type": grant.resource_type,
            "expires_at": grant.expires_at,
            "granted_by": grant.granted_by,
            "created_at": grant.created_at,
            "reason": grant.reason,
        }

    def _row_to_grant(self, row: sqlite3.Row | tuple) -> Grant:
        # sqlite3.Row or tuple access
        if isinstance(row, sqlite3.Row):
            d = dict(row)
        else:
            cols = [
                "id",
                "agent_id",
                "effect",
                "tool",
                "action",
                "resource_type",
                "expires_at",
                "granted_by",
                "created_at",
                "reason",
                "usage_count",
                "revoked",
            ]
            d = dict(zip(cols, row, strict=False))

        return Grant(
            id=d["id"],
            agent_id=d["agent_id"],
            effect=Effect(d["effect"]) if d["effect"] else None,
            tool=d["tool"],
            action=d["action"],
            resource_type=d["resource_type"],
            expires_at=d["expires_at"],
            granted_by=d["granted_by"],
            created_at=d["created_at"],
            reason=d["reason"],
        )

    async def create_grant(self, grant: Grant) -> str:
        row = self._grant_to_row(grant)
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._insert_grant, row)
        return grant.id

    def _insert_grant(self, row: dict[str, Any]):
        self._conn.execute(
            f"""INSERT OR REPLACE INTO {self._table}
            (id, agent_id, effect, tool, action, resource_type,
             expires_at, granted_by, created_at, reason)
            VALUES (:id, :agent_id, :effect, :tool, :action, :resource_type,
                    :expires_at, :granted_by, :created_at, :reason)""",
            row,
        )
        self._conn.commit()

    async def find_matching_grant(
        self, agent_ctx: AgentContext, tool_request: ToolRequest
    ) -> Grant | None:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self._find_matching_grant_sync, agent_ctx, tool_request
        )

    def _find_matching_grant_sync(
        self, agent_ctx: AgentContext, tool_request: ToolRequest
    ) -> Grant | None:
        now = time.time()
        cursor = self._conn.execute(
            f"""SELECT * FROM {self._table}
            WHERE expires_at > ? AND revoked = 0
            ORDER BY created_at DESC""",
            (now,),
        )
        cursor.row_factory = sqlite3.Row

        for row in cursor:
            d = dict(row)

            # Match agent_id
            if d["agent_id"] is not None and d["agent_id"] != agent_ctx.agent_id:
                continue

            # Match effect
            if d["effect"] is not None and d["effect"] != tool_request.effect.value:
                continue

            # Match tool (exact or prefix with *)
            if d["tool"] is not None:
                if d["tool"].endswith("*"):
                    prefix = d["tool"][:-1]
                    if not tool_request.tool.startswith(prefix):
                        continue
                elif d["tool"] != tool_request.tool:
                    continue

            # Match action
            if d["action"] is not None and d["action"] != tool_request.action:
                continue

            # Match resource_type
            if (
                d["resource_type"] is not None
                and d["resource_type"] != tool_request.resource_type
            ):
                continue

            # Match found — increment usage
            self._conn.execute(
                f"UPDATE {self._table} SET usage_count = usage_count + 1 WHERE id = ?",
                (d["id"],),
            )
            self._conn.commit()
            return self._row_to_grant(row)

        return None

    async def revoke_grant(self, grant_id: str) -> bool:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._revoke_sync, grant_id)

    def _revoke_sync(self, grant_id: str) -> bool:
        cursor = self._conn.execute(
            f"UPDATE {self._table} SET revoked = 1 WHERE id = ? AND revoked = 0",
            (grant_id,),
        )
        self._conn.commit()
        return cursor.rowcount > 0

    async def list_active_grants(
        self, agent_id: str | None = None, org_id: str | None = None
    ) -> list[Grant]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self._list_active_sync, agent_id, org_id
        )

    def _list_active_sync(
        self, agent_id: str | None, org_id: str | None = None
    ) -> list[Grant]:
        now = time.time()
        query = f"SELECT * FROM {self._table} WHERE expires_at > ? AND revoked = 0"
        params: list = [now]

        if agent_id is not None:
            query += " AND agent_id = ?"
            params.append(agent_id)

        if org_id is not None:
            query += " AND org_id = ?"
            params.append(org_id)

        cursor = self._conn.execute(query, params)
        cursor.row_factory = sqlite3.Row
        return [self._row_to_grant(row) for row in cursor]

    async def cleanup_expired(self) -> int:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._cleanup_sync)

    def _cleanup_sync(self) -> int:
        now = time.time()
        cursor = self._conn.execute(
            f"DELETE FROM {self._table} WHERE expires_at <= ?", (now,)
        )
        self._conn.commit()
        return cursor.rowcount

    async def get_usage_count(self, grant_id: str) -> int:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._get_usage_sync, grant_id)

    def _get_usage_sync(self, grant_id: str) -> int:
        cursor = self._conn.execute(
            f"SELECT usage_count FROM {self._table} WHERE id = ?", (grant_id,)
        )
        row = cursor.fetchone()
        return row[0] if row else 0

    def close(self):
        """Close the database connection."""
        self._conn.close()


class SQLiteApprovalStore:
    """SQLite-backed ApprovalStore implementation.

    Satisfies the ``ApprovalStore`` ABC. Uses polling-based wait_for_decision
    since SQLite doesn't support notifications.

    Args:
        db_path: Path to the SQLite database file.
        table_name: Name of the approvals table.
        poll_interval: Seconds between polls when waiting for a decision.
    """

    def __init__(
        self,
        db_path: str | Path = "tollgate_approvals.db",
        *,
        table_name: str = "tollgate_approvals",
        poll_interval: float = 0.5,
    ):
        self._db_path = str(db_path)
        self._table = table_name
        self._poll_interval = poll_interval
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._create_table()

    def _create_table(self):
        self._conn.execute(f"""
            CREATE TABLE IF NOT EXISTS {self._table} (
                id TEXT PRIMARY KEY,
                agent_json TEXT NOT NULL,
                intent_json TEXT NOT NULL,
                tool_request_json TEXT NOT NULL,
                request_hash TEXT NOT NULL,
                reason TEXT NOT NULL,
                expiry REAL NOT NULL,
                outcome TEXT NOT NULL DEFAULT 'deferred',
                decided_by TEXT,
                decided_at REAL
            )
        """)
        self._conn.execute(f"""
            CREATE INDEX IF NOT EXISTS idx_{self._table}_hash
            ON {self._table} (request_hash)
        """)
        self._conn.commit()

    async def create_request(
        self,
        agent_ctx: AgentContext,
        intent: Any,
        tool_request: ToolRequest,
        request_hash: str,
        reason: str,
        expiry: float,
    ) -> str:
        approval_id = str(uuid.uuid4())
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            self._insert_request,
            approval_id,
            json.dumps(agent_ctx.to_dict()),
            json.dumps(intent.to_dict()),
            json.dumps(tool_request.to_dict()),
            request_hash,
            reason,
            expiry,
        )
        return approval_id

    def _insert_request(
        self,
        approval_id: str,
        agent_json: str,
        intent_json: str,
        tool_request_json: str,
        request_hash: str,
        reason: str,
        expiry: float,
    ):
        self._conn.execute(
            f"""INSERT INTO {self._table}
            (id, agent_json, intent_json, tool_request_json,
             request_hash, reason, expiry)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                approval_id,
                agent_json,
                intent_json,
                tool_request_json,
                request_hash,
                reason,
                expiry,
            ),
        )
        self._conn.commit()

    async def set_decision(
        self,
        approval_id: str,
        outcome: ApprovalOutcome,
        decided_by: str,
        decided_at: float,
        request_hash: str,
    ) -> None:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            self._set_decision_sync,
            approval_id,
            outcome,
            decided_by,
            decided_at,
            request_hash,
        )

    def _set_decision_sync(
        self,
        approval_id: str,
        outcome: ApprovalOutcome,
        decided_by: str,
        decided_at: float,
        request_hash: str,
    ):
        cursor = self._conn.execute(
            f"SELECT request_hash FROM {self._table} WHERE id = ?",
            (approval_id,),
        )
        row = cursor.fetchone()
        if row is None:
            return

        stored_hash = row[0]
        if stored_hash != request_hash:
            raise ValueError(
                "Request hash mismatch. Approval bound to a different request."
            )

        self._conn.execute(
            f"""UPDATE {self._table}
            SET outcome = ?, decided_by = ?, decided_at = ?
            WHERE id = ?""",
            (outcome.value, decided_by, decided_at, approval_id),
        )
        self._conn.commit()

    async def get_request(self, approval_id: str) -> dict[str, Any] | None:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._get_request_sync, approval_id)

    def _get_request_sync(self, approval_id: str) -> dict[str, Any] | None:
        cursor = self._conn.execute(
            f"SELECT * FROM {self._table} WHERE id = ?", (approval_id,)
        )
        cursor.row_factory = sqlite3.Row
        row = cursor.fetchone()
        if row is None:
            return None

        d = dict(row)
        return {
            "id": d["id"],
            "agent": json.loads(d["agent_json"]),
            "intent": json.loads(d["intent_json"]),
            "tool_request": json.loads(d["tool_request_json"]),
            "request_hash": d["request_hash"],
            "reason": d["reason"],
            "expiry": d["expiry"],
            "outcome": ApprovalOutcome(d["outcome"]),
            "decided_by": d.get("decided_by"),
            "decided_at": d.get("decided_at"),
        }

    async def wait_for_decision(
        self, approval_id: str, timeout: float
    ) -> ApprovalOutcome:
        """Poll the database for a decision, with timeout."""
        deadline = time.time() + timeout

        while time.time() < deadline:
            req = await self.get_request(approval_id)
            if req is None:
                return ApprovalOutcome.TIMEOUT

            if req["expiry"] < time.time():
                return ApprovalOutcome.TIMEOUT

            if req["outcome"] != ApprovalOutcome.DEFERRED:
                return req["outcome"]

            await asyncio.sleep(self._poll_interval)

        return ApprovalOutcome.TIMEOUT

    def close(self):
        """Close the database connection."""
        self._conn.close()
