"""
arc.core.memory.store
─────────────────────
Long-term persisted key-value memory for arc agents.

AgentMemoryStore provides durable agent memory that survives Lambda cold starts
and spans multiple sessions. It stores arbitrary JSON-serialisable facts keyed by
(agent_id, namespace, key).

Backends:
  - LocalJsonStore  — local JSON file (development / single-process)
  - DynamoDBStore   — AWS DynamoDB with TTL (production)

Usage in a BaseAgent:

    from arc.core.memory import AgentMemoryStore, LocalJsonStore

    class FiduciaryWatchdogAgent(BaseAgent):

        def __init__(self, manifest, tower, gateway, tracker=None):
            super().__init__(manifest, tower, gateway, tracker)
            backend = LocalJsonStore("/tmp/watchdog-memory.json")
            self.memory = AgentMemoryStore(
                agent_id=manifest.agent_id,
                backend=backend,
            )

        async def execute(self, fund_id: str, **kwargs) -> dict:
            # Recall previous finding for this fund
            last_finding = await self.memory.get("findings", fund_id)
            if last_finding:
                print(f"Previous finding: {last_finding}")

            # ... run analysis ...
            finding = {"severity": "low", "score": 0.42}

            # Persist for next run
            await self.memory.set("findings", fund_id, finding, ttl_days=90)
            return finding

Production setup (DynamoDB):

    from arc.core.memory import AgentMemoryStore, DynamoDBMemoryBackend

    backend = DynamoDBMemoryBackend(
        table_name="arc-agent-memory",
        region="us-east-1",
    )
    memory = AgentMemoryStore(agent_id="fiduciary-watchdog", backend=backend)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ── Data model ─────────────────────────────────────────────────────────────────


@dataclass
class MemoryEntry:
    """
    A single persisted memory fact.

    Attributes:
        namespace:  Logical group (e.g. "findings", "participant_state").
        key:        Entry identifier (e.g. fund_id, participant_id).
        value:      The stored value (JSON-serialisable).
        agent_id:   ID of the agent that wrote this entry.
        created_at: Unix timestamp of creation.
        updated_at: Unix timestamp of last update.
        expires_at: Unix timestamp when this entry expires (None = no TTL).
        metadata:   Arbitrary extra context.
    """
    namespace:  str
    key:        str
    value:      Any
    agent_id:   str
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    expires_at: float | None = None
    metadata:   dict  = field(default_factory=dict)

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return time.time() > self.expires_at

    def to_dict(self) -> dict:
        return {
            "namespace":  self.namespace,
            "key":        self.key,
            "value":      self.value,
            "agent_id":   self.agent_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "expires_at": self.expires_at,
            "metadata":   self.metadata,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "MemoryEntry":
        return cls(
            namespace=d["namespace"],
            key=d["key"],
            value=d["value"],
            agent_id=d["agent_id"],
            created_at=d.get("created_at", time.time()),
            updated_at=d.get("updated_at", time.time()),
            expires_at=d.get("expires_at"),
            metadata=d.get("metadata", {}),
        )


# ── Backend protocol ───────────────────────────────────────────────────────────


class MemoryBackend(ABC):
    """Abstract persistence backend for AgentMemoryStore."""

    @abstractmethod
    async def get(self, agent_id: str, namespace: str, key: str) -> MemoryEntry | None:
        ...

    @abstractmethod
    async def set(self, entry: MemoryEntry) -> None:
        ...

    @abstractmethod
    async def delete(self, agent_id: str, namespace: str, key: str) -> None:
        ...

    @abstractmethod
    async def list_keys(self, agent_id: str, namespace: str) -> list[str]:
        ...

    @abstractmethod
    async def list_namespace(self, agent_id: str, namespace: str) -> list[MemoryEntry]:
        ...


# ── Local JSON backend ─────────────────────────────────────────────────────────


class LocalJsonStore(MemoryBackend):
    """
    File-backed memory backend using a local JSON file.

    Suitable for development, single-process Lambda cold starts,
    and unit tests. Not safe for concurrent multi-process writes.

    Args:
        path: Path to the JSON file. Created if it does not exist.
    """

    def __init__(self, path: str | Path):
        self._path = Path(path)
        self._data: dict = {}
        self._loaded = False

    def _load(self) -> None:
        if not self._loaded:
            if self._path.exists():
                with open(self._path) as f:
                    self._data = json.load(f)
            self._loaded = True

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._path, "w") as f:
            json.dump(self._data, f, indent=2, default=str)

    def _pk(self, agent_id: str, namespace: str, key: str) -> str:
        return f"{agent_id}::{namespace}::{key}"

    async def get(self, agent_id: str, namespace: str, key: str) -> MemoryEntry | None:
        self._load()
        pk = self._pk(agent_id, namespace, key)
        raw = self._data.get(pk)
        if raw is None:
            return None
        entry = MemoryEntry.from_dict(raw)
        if entry.is_expired:
            await self.delete(agent_id, namespace, key)
            return None
        return entry

    async def set(self, entry: MemoryEntry) -> None:
        self._load()
        pk = self._pk(entry.agent_id, entry.namespace, entry.key)
        self._data[pk] = entry.to_dict()
        await asyncio.to_thread(self._save)

    async def delete(self, agent_id: str, namespace: str, key: str) -> None:
        self._load()
        pk = self._pk(agent_id, namespace, key)
        self._data.pop(pk, None)
        await asyncio.to_thread(self._save)

    async def list_keys(self, agent_id: str, namespace: str) -> list[str]:
        self._load()
        prefix = f"{agent_id}::{namespace}::"
        return [
            pk.split("::", 2)[2]
            for pk in self._data
            if pk.startswith(prefix)
        ]

    async def list_namespace(self, agent_id: str, namespace: str) -> list[MemoryEntry]:
        keys = await self.list_keys(agent_id, namespace)
        entries = []
        for key in keys:
            entry = await self.get(agent_id, namespace, key)
            if entry is not None:
                entries.append(entry)
        return entries


# ── DynamoDB backend ───────────────────────────────────────────────────────────


class DynamoDBMemoryBackend(MemoryBackend):
    """
    DynamoDB-backed memory for production agents.

    Table schema (create once, shared across all agents):
        PK: "agent_id#namespace"   (partition key — string)
        SK: "key"                  (sort key — string)
        value:                     JSON string
        created_at, updated_at:    Unix timestamps (Number)
        expires_at:                TTL attribute (DynamoDB native TTL — Number)
        metadata:                  JSON string

    Enable DynamoDB TTL on the `expires_at` attribute so expired entries
    are automatically deleted by AWS.

    Args:
        table_name: DynamoDB table name.
        region:     AWS region (default: from environment).
    """

    def __init__(self, table_name: str, region: str | None = None):
        self._table_name = table_name
        self._region     = region
        self._table: Any = None

    def _get_table(self) -> Any:
        if self._table is None:
            try:
                import boto3
            except ImportError as exc:
                raise ImportError(
                    "boto3 is required for DynamoDBMemoryBackend. "
                    "Run: pip install 'arc-core[aws]'"
                ) from exc
            dynamodb = boto3.resource("dynamodb", region_name=self._region)
            self._table = dynamodb.Table(self._table_name)
        return self._table

    @staticmethod
    def _pk(agent_id: str, namespace: str) -> str:
        return f"{agent_id}#{namespace}"

    def _to_item(self, entry: MemoryEntry) -> dict:
        item: dict[str, Any] = {
            "PK":         self._pk(entry.agent_id, entry.namespace),
            "SK":         entry.key,
            "value":      json.dumps(entry.value, default=str),
            "agent_id":   entry.agent_id,
            "namespace":  entry.namespace,
            "created_at": int(entry.created_at),
            "updated_at": int(entry.updated_at),
            "metadata":   json.dumps(entry.metadata),
        }
        if entry.expires_at is not None:
            item["expires_at"] = int(entry.expires_at)
        return item

    def _from_item(self, item: dict) -> MemoryEntry:
        return MemoryEntry(
            namespace=item["namespace"],
            key=item["SK"],
            value=json.loads(item["value"]),
            agent_id=item["agent_id"],
            created_at=float(item.get("created_at", time.time())),
            updated_at=float(item.get("updated_at", time.time())),
            expires_at=float(item["expires_at"]) if "expires_at" in item else None,
            metadata=json.loads(item.get("metadata", "{}")),
        )

    async def get(self, agent_id: str, namespace: str, key: str) -> MemoryEntry | None:
        table = self._get_table()
        response = await asyncio.to_thread(
            table.get_item,
            Key={"PK": self._pk(agent_id, namespace), "SK": key},
        )
        item = response.get("Item")
        if item is None:
            return None
        entry = self._from_item(item)
        if entry.is_expired:
            return None
        return entry

    async def set(self, entry: MemoryEntry) -> None:
        table = self._get_table()
        await asyncio.to_thread(table.put_item, Item=self._to_item(entry))

    async def delete(self, agent_id: str, namespace: str, key: str) -> None:
        table = self._get_table()
        await asyncio.to_thread(
            table.delete_item,
            Key={"PK": self._pk(agent_id, namespace), "SK": key},
        )

    async def list_keys(self, agent_id: str, namespace: str) -> list[str]:
        entries = await self.list_namespace(agent_id, namespace)
        return [e.key for e in entries]

    async def list_namespace(self, agent_id: str, namespace: str) -> list[MemoryEntry]:
        from boto3.dynamodb.conditions import Key as DDBKey
        table = self._get_table()
        response = await asyncio.to_thread(
            table.query,
            KeyConditionExpression=DDBKey("PK").eq(self._pk(agent_id, namespace)),
        )
        return [
            self._from_item(item)
            for item in response.get("Items", [])
            if not self._from_item(item).is_expired
        ]


# ── High-level store ───────────────────────────────────────────────────────────


class AgentMemoryStore:
    """
    High-level long-term memory store for arc agents.

    Wraps a MemoryBackend with a clean get/set/delete API, automatic TTL
    calculation from days, and structured logging.

    Args:
        agent_id: The agent's ID (from manifest.agent_id).
        backend:  A MemoryBackend instance (LocalJsonStore or DynamoDBMemoryBackend).
    """

    def __init__(self, agent_id: str, backend: MemoryBackend):
        self.agent_id = agent_id
        self._backend = backend

    async def get(self, namespace: str, key: str) -> Any | None:
        """
        Retrieve a stored value.

        Args:
            namespace: Logical group (e.g. "findings", "participant_state").
            key:       Entry identifier.

        Returns:
            The stored value, or None if not found or expired.
        """
        entry = await self._backend.get(self.agent_id, namespace, key)
        if entry is None:
            logger.debug("memory.get agent=%s ns=%s key=%s → miss", self.agent_id, namespace, key)
            return None
        logger.debug("memory.get agent=%s ns=%s key=%s → hit", self.agent_id, namespace, key)
        return entry.value

    async def set(
        self,
        namespace: str,
        key: str,
        value: Any,
        ttl_days: int | None = None,
        metadata: dict | None = None,
    ) -> None:
        """
        Store a value with optional TTL.

        Args:
            namespace: Logical group.
            key:       Entry identifier.
            value:     JSON-serialisable value to store.
            ttl_days:  Days until expiry (None = never expires).
            metadata:  Extra context stored alongside the value.
        """
        now        = time.time()
        expires_at = now + (ttl_days * 86400) if ttl_days is not None else None
        existing   = await self._backend.get(self.agent_id, namespace, key)
        created_at = existing.created_at if existing else now

        entry = MemoryEntry(
            namespace=namespace,
            key=key,
            value=value,
            agent_id=self.agent_id,
            created_at=created_at,
            updated_at=now,
            expires_at=expires_at,
            metadata=metadata or {},
        )
        await self._backend.set(entry)
        logger.debug(
            "memory.set agent=%s ns=%s key=%s ttl_days=%s",
            self.agent_id, namespace, key, ttl_days,
        )

    async def delete(self, namespace: str, key: str) -> None:
        """Delete a stored entry."""
        await self._backend.delete(self.agent_id, namespace, key)
        logger.debug("memory.delete agent=%s ns=%s key=%s", self.agent_id, namespace, key)

    async def keys(self, namespace: str) -> list[str]:
        """List all keys in a namespace."""
        return await self._backend.list_keys(self.agent_id, namespace)

    async def all(self, namespace: str) -> list[MemoryEntry]:
        """Retrieve all non-expired entries in a namespace."""
        return await self._backend.list_namespace(self.agent_id, namespace)

    async def get_or_set(
        self,
        namespace: str,
        key: str,
        default_fn,
        ttl_days: int | None = None,
    ) -> Any:
        """
        Return a stored value, computing and caching it if not present.

        Args:
            namespace:   Logical group.
            key:         Entry identifier.
            default_fn:  Async callable returning the value if not cached.
            ttl_days:    TTL for the cached value.

        Returns:
            The stored or freshly computed value.

        Usage:
            score = await memory.get_or_set(
                "risk_scores", participant_id,
                default_fn=lambda: self._compute_risk_score(participant_id),
                ttl_days=7,
            )
        """
        existing = await self.get(namespace, key)
        if existing is not None:
            return existing
        value = await default_fn()
        await self.set(namespace, key, value, ttl_days=ttl_days)
        return value
