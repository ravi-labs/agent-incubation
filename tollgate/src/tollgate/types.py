import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class Effect(str, Enum):
    READ = "read"
    WRITE = "write"
    DELETE = "delete"
    NOTIFY = "notify"
    UNKNOWN = "unknown"


class DecisionType(str, Enum):
    ALLOW = "ALLOW"
    ASK = "ASK"
    DENY = "DENY"


class Outcome(str, Enum):
    EXECUTED = "executed"
    BLOCKED = "blocked"
    APPROVAL_DENIED = "approval_denied"
    FAILED = "failed"
    TIMEOUT = "timeout"


class ApprovalOutcome(str, Enum):
    APPROVED = "approved"
    DENIED = "denied"
    DEFERRED = "deferred"
    TIMEOUT = "timeout"


@dataclass(frozen=True)
class AgentContext:
    agent_id: str
    version: str
    owner: str
    metadata: dict[str, Any] = field(default_factory=dict)
    delegated_by: tuple[str, ...] = field(default_factory=tuple)

    @property
    def delegation_depth(self) -> int:
        """Number of agents in the delegation chain (0 = direct call)."""
        return len(self.delegated_by)

    @property
    def is_delegated(self) -> bool:
        """Whether this agent was delegated to by another agent."""
        return len(self.delegated_by) > 0

    @property
    def root_agent(self) -> str:
        """The original (root) agent that started the delegation chain."""
        return self.delegated_by[0] if self.delegated_by else self.agent_id

    @property
    def org_id(self) -> str | None:
        """Organization ID for multi-tenant deployments.

        Stored in metadata["org_id"]. Returns None if not set.

        Example:
            ctx = AgentContext(
                agent_id="agent-1",
                version="1.0",
                owner="user",
                metadata={"org_id": "org-123"}
            )
            assert ctx.org_id == "org-123"
        """
        return self.metadata.get("org_id")

    @classmethod
    def with_org(
        cls,
        agent_id: str,
        version: str,
        owner: str,
        org_id: str,
        **kwargs: Any,
    ) -> "AgentContext":
        """Factory method to create an AgentContext with org_id.

        Example:
            ctx = AgentContext.with_org(
                agent_id="agent-1",
                version="1.0",
                owner="user",
                org_id="org-123",
            )
        """
        metadata = dict(kwargs.pop("metadata", {}))
        metadata["org_id"] = org_id
        return cls(
            agent_id=agent_id,
            version=version,
            owner=owner,
            metadata=metadata,
            **kwargs,
        )

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["delegated_by"] = list(self.delegated_by)
        return d


@dataclass(frozen=True)
class Intent:
    action: str
    reason: str
    confidence: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ToolRequest:
    tool: str
    action: str
    resource_type: str
    effect: Effect
    params: dict[str, Any]
    metadata: dict[str, Any] = field(default_factory=dict)
    manifest_version: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["effect"] = self.effect.value
        return d


@dataclass(frozen=True)
class NormalizedToolCall:
    request: ToolRequest
    exec_async: Callable[[], Awaitable[Any]]
    exec_sync: Callable[[], Any] | None = None


@dataclass(frozen=True)
class Decision:
    decision: DecisionType
    reason: str
    policy_id: str | None = None
    policy_version: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["decision"] = self.decision.value
        return d


@dataclass(frozen=True)
class Grant:
    """A grant that allows bypassing human approval for specific actions."""

    agent_id: str | None  # None = any agent
    effect: Effect | None  # None = any effect
    tool: str | None  # None = any tool, supports prefix like "mcp:*"
    action: str | None  # None = any action
    resource_type: str | None  # None = any resource
    expires_at: float  # Unix timestamp
    granted_by: str
    created_at: float
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    reason: str | None = None
    org_id: str | None = None  # Organization scope (None = global)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if self.effect:
            d["effect"] = self.effect.value
        return d


@dataclass(frozen=True)
class AuditEvent:
    timestamp: str
    correlation_id: str
    request_hash: str
    agent: AgentContext
    intent: Intent
    tool_request: ToolRequest
    decision: Decision
    outcome: Outcome
    approval_id: str | None = None
    grant_id: str | None = None
    result_summary: str | None = None
    policy_version: str | None = None
    manifest_version: str | None = None
    schema_version: str = "1.0"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "timestamp": self.timestamp,
            "correlation_id": self.correlation_id,
            "request_hash": self.request_hash,
            "agent": self.agent.to_dict(),
            "intent": self.intent.to_dict(),
            "tool_request": self.tool_request.to_dict(),
            "decision": self.decision.to_dict(),
            "outcome": self.outcome.value,
            "approval_id": self.approval_id,
            "grant_id": self.grant_id,
            "result_summary": self.result_summary,
            "policy_version": self.policy_version,
            "manifest_version": self.manifest_version,
        }
