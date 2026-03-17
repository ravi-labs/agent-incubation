"""Policy Versioning & Rollback for Tollgate.

Provides Git-like versioning for policies with:
- Version history tracking
- Rollback to previous versions
- Diff between versions
- Audit trail for policy changes

Example:
    from tollgate.policy_versioning import PolicyVersionStore, SQLitePolicyVersionStore

    # Create version store
    store = SQLitePolicyVersionStore("policies.db")

    # Save a new version
    version_id = await store.save_version(
        policy_content="version: v1\nrules: [...]",
        author="admin@example.com",
        message="Initial policy",
    )

    # List versions
    versions = await store.list_versions()

    # Rollback to a previous version
    await store.rollback(version_id)
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class PolicyVersion:
    """Represents a single policy version."""

    id: str
    version_number: int
    content_hash: str
    content: str
    author: str
    message: str
    created_at: float
    parent_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    is_active: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PolicyVersion:
        """Create from dictionary."""
        return cls(**data)


@dataclass
class PolicyDiff:
    """Represents differences between two policy versions."""

    old_version: str
    new_version: str
    added_rules: list[dict[str, Any]]
    removed_rules: list[dict[str, Any]]
    modified_rules: list[dict[str, Any]]
    version_changed: bool
    defaults_changed: bool
    summary: str


class PolicyVersionStore(Protocol):
    """Protocol for policy version storage backends."""

    async def save_version(
        self,
        content: str,
        author: str,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Save a new policy version. Returns version ID."""
        ...

    async def get_version(self, version_id: str) -> PolicyVersion | None:
        """Get a specific version by ID."""
        ...

    async def get_active_version(self) -> PolicyVersion | None:
        """Get the currently active version."""
        ...

    async def list_versions(
        self,
        limit: int = 50,
        offset: int = 0,
    ) -> list[PolicyVersion]:
        """List versions in reverse chronological order."""
        ...

    async def rollback(self, version_id: str, author: str) -> str:
        """Rollback to a specific version. Creates a new version. Returns new ID."""
        ...

    async def diff_versions(
        self,
        old_version_id: str,
        new_version_id: str,
    ) -> PolicyDiff:
        """Compare two versions."""
        ...


class SQLitePolicyVersionStore:
    """SQLite-backed policy version store."""

    def __init__(self, db_path: str | Path):
        """Initialize the store.

        Args:
            db_path: Path to SQLite database file.
        """
        self.db_path = Path(db_path)
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        """Create database schema if it doesn't exist."""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS policy_versions (
                    id TEXT PRIMARY KEY,
                    version_number INTEGER NOT NULL,
                    content_hash TEXT NOT NULL,
                    content TEXT NOT NULL,
                    author TEXT NOT NULL,
                    message TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    parent_id TEXT,
                    metadata TEXT DEFAULT '{}',
                    is_active INTEGER DEFAULT 0,
                    FOREIGN KEY (parent_id) REFERENCES policy_versions(id)
                );

                CREATE INDEX IF NOT EXISTS idx_versions_created_at
                ON policy_versions(created_at DESC);

                CREATE INDEX IF NOT EXISTS idx_versions_active
                ON policy_versions(is_active);

                CREATE INDEX IF NOT EXISTS idx_versions_hash
                ON policy_versions(content_hash);
            """)
            conn.commit()
        finally:
            conn.close()

    def _generate_version_id(self, content: str, timestamp: float) -> str:
        """Generate a unique version ID."""
        data = f"{content}:{timestamp}".encode()
        return hashlib.sha256(data).hexdigest()[:16]

    def _compute_content_hash(self, content: str) -> str:
        """Compute SHA-256 hash of content."""
        return hashlib.sha256(content.encode()).hexdigest()

    async def save_version(
        self,
        content: str,
        author: str,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Save a new policy version."""
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.cursor()

            # Get current active version for parent reference
            cursor.execute(
                "SELECT id, version_number FROM policy_versions WHERE is_active = 1"
            )
            row = cursor.fetchone()
            parent_id = row[0] if row else None
            version_number = (row[1] + 1) if row else 1

            # Check if content is identical to current active version
            content_hash = self._compute_content_hash(content)
            if parent_id:
                cursor.execute(
                    "SELECT content_hash FROM policy_versions WHERE id = ?",
                    (parent_id,)
                )
                parent_row = cursor.fetchone()
                if parent_row and parent_row[0] == content_hash:
                    # No changes, return existing version
                    return parent_id

            # Create new version
            timestamp = time.time()
            version_id = self._generate_version_id(content, timestamp)

            # Deactivate current active version
            cursor.execute("UPDATE policy_versions SET is_active = 0")

            # Insert new version
            cursor.execute("""
                INSERT INTO policy_versions
                (id, version_number, content_hash, content, author, message,
                 created_at, parent_id, metadata, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
            """, (
                version_id,
                version_number,
                content_hash,
                content,
                author,
                message,
                timestamp,
                parent_id,
                json.dumps(metadata or {}),
            ))

            conn.commit()
            return version_id

        finally:
            conn.close()

    async def get_version(self, version_id: str) -> PolicyVersion | None:
        """Get a specific version by ID."""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute(
                "SELECT * FROM policy_versions WHERE id = ?",
                (version_id,)
            )
            row = cursor.fetchone()

            if not row:
                return None

            return PolicyVersion(
                id=row["id"],
                version_number=row["version_number"],
                content_hash=row["content_hash"],
                content=row["content"],
                author=row["author"],
                message=row["message"],
                created_at=row["created_at"],
                parent_id=row["parent_id"],
                metadata=json.loads(row["metadata"]),
                is_active=bool(row["is_active"]),
            )

        finally:
            conn.close()

    async def get_active_version(self) -> PolicyVersion | None:
        """Get the currently active version."""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute(
                "SELECT * FROM policy_versions WHERE is_active = 1"
            )
            row = cursor.fetchone()

            if not row:
                return None

            return PolicyVersion(
                id=row["id"],
                version_number=row["version_number"],
                content_hash=row["content_hash"],
                content=row["content"],
                author=row["author"],
                message=row["message"],
                created_at=row["created_at"],
                parent_id=row["parent_id"],
                metadata=json.loads(row["metadata"]),
                is_active=bool(row["is_active"]),
            )

        finally:
            conn.close()

    async def list_versions(
        self,
        limit: int = 50,
        offset: int = 0,
    ) -> list[PolicyVersion]:
        """List versions in reverse chronological order."""
        conn = sqlite3.connect(self.db_path)
        try:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            cursor.execute("""
                SELECT * FROM policy_versions
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
            """, (limit, offset))

            versions = []
            for row in cursor.fetchall():
                versions.append(PolicyVersion(
                    id=row["id"],
                    version_number=row["version_number"],
                    content_hash=row["content_hash"],
                    content=row["content"],
                    author=row["author"],
                    message=row["message"],
                    created_at=row["created_at"],
                    parent_id=row["parent_id"],
                    metadata=json.loads(row["metadata"]),
                    is_active=bool(row["is_active"]),
                ))

            return versions

        finally:
            conn.close()

    async def rollback(self, version_id: str, author: str) -> str:
        """Rollback to a specific version. Creates a new version."""
        target = await self.get_version(version_id)
        if not target:
            raise ValueError(f"Version not found: {version_id}")

        # Create a new version with the old content
        return await self.save_version(
            content=target.content,
            author=author,
            message=f"Rollback to version {target.version_number} ({version_id})",
            metadata={"rollback_from": version_id},
        )

    async def diff_versions(
        self,
        old_version_id: str,
        new_version_id: str,
    ) -> PolicyDiff:
        """Compare two versions."""
        import yaml

        old_version = await self.get_version(old_version_id)
        new_version = await self.get_version(new_version_id)

        if not old_version:
            raise ValueError(f"Old version not found: {old_version_id}")
        if not new_version:
            raise ValueError(f"New version not found: {new_version_id}")

        old_policy = yaml.safe_load(old_version.content)
        new_policy = yaml.safe_load(new_version.content)

        # Compare versions
        old_ver = old_policy.get("version", "unversioned")
        new_ver = new_policy.get("version", "unversioned")
        version_changed = old_ver != new_ver

        # Compare defaults
        old_defaults = old_policy.get("defaults", {})
        new_defaults = new_policy.get("defaults", {})
        defaults_changed = old_defaults != new_defaults

        # Compare rules
        old_rules = {
            r.get("id", f"rule-{i}"): r
            for i, r in enumerate(old_policy.get("rules", []))
        }
        new_rules = {
            r.get("id", f"rule-{i}"): r
            for i, r in enumerate(new_policy.get("rules", []))
        }

        old_ids = set(old_rules.keys())
        new_ids = set(new_rules.keys())

        added_rules = [new_rules[rid] for rid in (new_ids - old_ids)]
        removed_rules = [old_rules[rid] for rid in (old_ids - new_ids)]
        modified_rules = [
            {"id": rid, "old": old_rules[rid], "new": new_rules[rid]}
            for rid in (old_ids & new_ids)
            if old_rules[rid] != new_rules[rid]
        ]

        # Create summary
        changes = []
        if version_changed:
            changes.append(f"version: {old_ver} → {new_ver}")
        if defaults_changed:
            changes.append("defaults changed")
        if added_rules:
            changes.append(f"+{len(added_rules)} rules")
        if removed_rules:
            changes.append(f"-{len(removed_rules)} rules")
        if modified_rules:
            changes.append(f"~{len(modified_rules)} rules")

        summary = ", ".join(changes) if changes else "No changes"

        return PolicyDiff(
            old_version=old_version_id,
            new_version=new_version_id,
            added_rules=added_rules,
            removed_rules=removed_rules,
            modified_rules=modified_rules,
            version_changed=version_changed,
            defaults_changed=defaults_changed,
            summary=summary,
        )

    async def get_version_count(self) -> int:
        """Get total number of versions."""
        conn = sqlite3.connect(self.db_path)
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM policy_versions")
            return cursor.fetchone()[0]
        finally:
            conn.close()

    async def get_version_history(
        self,
        version_id: str,
        depth: int = 10,
    ) -> list[PolicyVersion]:
        """Get version history (ancestors) for a specific version."""
        history = []
        current_id = version_id

        for _ in range(depth):
            version = await self.get_version(current_id)
            if not version:
                break
            history.append(version)
            if not version.parent_id:
                break
            current_id = version.parent_id

        return history


class InMemoryPolicyVersionStore:
    """In-memory policy version store for testing."""

    def __init__(self):
        self._versions: dict[str, PolicyVersion] = {}
        self._active_id: str | None = None
        self._next_version_number = 1

    def _generate_version_id(self, content: str, timestamp: float) -> str:
        """Generate a unique version ID."""
        data = f"{content}:{timestamp}".encode()
        return hashlib.sha256(data).hexdigest()[:16]

    def _compute_content_hash(self, content: str) -> str:
        """Compute SHA-256 hash of content."""
        return hashlib.sha256(content.encode()).hexdigest()

    async def save_version(
        self,
        content: str,
        author: str,
        message: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Save a new policy version."""
        content_hash = self._compute_content_hash(content)

        # Check if content is identical to current active version
        if self._active_id:
            active = self._versions[self._active_id]
            if active.content_hash == content_hash:
                return self._active_id

        timestamp = time.time()
        version_id = self._generate_version_id(content, timestamp)

        # Deactivate current active version
        if self._active_id:
            old_active = self._versions[self._active_id]
            self._versions[self._active_id] = PolicyVersion(
                id=old_active.id,
                version_number=old_active.version_number,
                content_hash=old_active.content_hash,
                content=old_active.content,
                author=old_active.author,
                message=old_active.message,
                created_at=old_active.created_at,
                parent_id=old_active.parent_id,
                metadata=old_active.metadata,
                is_active=False,
            )

        version = PolicyVersion(
            id=version_id,
            version_number=self._next_version_number,
            content_hash=content_hash,
            content=content,
            author=author,
            message=message,
            created_at=timestamp,
            parent_id=self._active_id,
            metadata=metadata or {},
            is_active=True,
        )

        self._versions[version_id] = version
        self._active_id = version_id
        self._next_version_number += 1

        return version_id

    async def get_version(self, version_id: str) -> PolicyVersion | None:
        """Get a specific version by ID."""
        return self._versions.get(version_id)

    async def get_active_version(self) -> PolicyVersion | None:
        """Get the currently active version."""
        if not self._active_id:
            return None
        return self._versions.get(self._active_id)

    async def list_versions(
        self,
        limit: int = 50,
        offset: int = 0,
    ) -> list[PolicyVersion]:
        """List versions in reverse chronological order."""
        sorted_versions = sorted(
            self._versions.values(),
            key=lambda v: v.created_at,
            reverse=True,
        )
        return sorted_versions[offset:offset + limit]

    async def rollback(self, version_id: str, author: str) -> str:
        """Rollback to a specific version."""
        target = await self.get_version(version_id)
        if not target:
            raise ValueError(f"Version not found: {version_id}")

        return await self.save_version(
            content=target.content,
            author=author,
            message=f"Rollback to version {target.version_number} ({version_id})",
            metadata={"rollback_from": version_id},
        )

    async def diff_versions(
        self,
        old_version_id: str,
        new_version_id: str,
    ) -> PolicyDiff:
        """Compare two versions."""
        import yaml

        old_version = await self.get_version(old_version_id)
        new_version = await self.get_version(new_version_id)

        if not old_version:
            raise ValueError(f"Old version not found: {old_version_id}")
        if not new_version:
            raise ValueError(f"New version not found: {new_version_id}")

        old_policy = yaml.safe_load(old_version.content)
        new_policy = yaml.safe_load(new_version.content)

        old_ver = old_policy.get("version", "unversioned")
        new_ver = new_policy.get("version", "unversioned")

        old_defaults = old_policy.get("defaults", {})
        new_defaults = new_policy.get("defaults", {})

        old_rules = {
            r.get("id", f"rule-{i}"): r
            for i, r in enumerate(old_policy.get("rules", []))
        }
        new_rules = {
            r.get("id", f"rule-{i}"): r
            for i, r in enumerate(new_policy.get("rules", []))
        }

        old_ids = set(old_rules.keys())
        new_ids = set(new_rules.keys())

        added = [new_rules[rid] for rid in (new_ids - old_ids)]
        removed = [old_rules[rid] for rid in (old_ids - new_ids)]
        modified = [
            {"id": rid, "old": old_rules[rid], "new": new_rules[rid]}
            for rid in (old_ids & new_ids)
            if old_rules[rid] != new_rules[rid]
        ]

        changes = []
        if old_ver != new_ver:
            changes.append(f"version: {old_ver} → {new_ver}")
        if old_defaults != new_defaults:
            changes.append("defaults changed")
        if added:
            changes.append(f"+{len(added)} rules")
        if removed:
            changes.append(f"-{len(removed)} rules")
        if modified:
            changes.append(f"~{len(modified)} rules")

        return PolicyDiff(
            old_version=old_version_id,
            new_version=new_version_id,
            added_rules=added,
            removed_rules=removed,
            modified_rules=modified,
            version_changed=old_ver != new_ver,
            defaults_changed=old_defaults != new_defaults,
            summary=", ".join(changes) if changes else "No changes",
        )


class VersionedPolicyEvaluator:
    """Policy evaluator that uses versioned policies.

    Wraps YamlPolicyEvaluator with version awareness.

    Example:
        store = SQLitePolicyVersionStore("policies.db")
        evaluator = await VersionedPolicyEvaluator.create(store)

        # Evaluate policy
        decision = evaluator.evaluate(agent_ctx, intent, tool_request)

        # Hot-reload when policy changes
        await evaluator.reload()
    """

    def __init__(
        self,
        store: PolicyVersionStore,
        evaluator,  # YamlPolicyEvaluator
        version: PolicyVersion,
    ):
        self._store = store
        self._evaluator = evaluator
        self._version = version

    @classmethod
    async def create(
        cls,
        store: PolicyVersionStore,
    ) -> VersionedPolicyEvaluator:
        """Create a versioned policy evaluator from the active version."""
        import tempfile

        from .policy import YamlPolicyEvaluator

        version = await store.get_active_version()
        if not version:
            raise ValueError("No active policy version found")

        # Write to temp file for YamlPolicyEvaluator
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            f.write(version.content)
            temp_path = f.name

        try:
            evaluator = YamlPolicyEvaluator(temp_path)
        finally:
            Path(temp_path).unlink(missing_ok=True)

        return cls(store, evaluator, version)

    @property
    def version_id(self) -> str:
        """Get current version ID."""
        return self._version.id

    @property
    def version_number(self) -> int:
        """Get current version number."""
        return self._version.version_number

    async def reload(self) -> bool:
        """Reload from the active version if changed. Returns True if reloaded."""
        import tempfile

        from .policy import YamlPolicyEvaluator

        active = await self._store.get_active_version()
        if not active:
            return False

        if active.id == self._version.id:
            return False  # No change

        # Hot-reload new version
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False
        ) as f:
            f.write(active.content)
            temp_path = f.name

        try:
            self._evaluator = YamlPolicyEvaluator(temp_path)
        finally:
            Path(temp_path).unlink(missing_ok=True)

        self._version = active
        return True

    def evaluate(self, agent_ctx, intent, tool_request):
        """Evaluate the policy. Delegates to wrapped evaluator."""
        return self._evaluator.evaluate(agent_ctx, intent, tool_request)
