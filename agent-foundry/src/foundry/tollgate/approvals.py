import asyncio
import hashlib
import json
import time
import uuid
from abc import ABC, abstractmethod
from typing import Any, Protocol

from .types import AgentContext, ApprovalOutcome, Effect, Intent, ToolRequest


class ApprovalStore(ABC):
    """Interface for persistent storage of approval requests."""

    @abstractmethod
    async def create_request(
        self,
        agent_ctx: AgentContext,
        intent: Intent,
        tool_request: ToolRequest,
        request_hash: str,
        reason: str,
        expiry: float,
    ) -> str:
        """Create an approval request and return its ID."""
        pass

    @abstractmethod
    async def set_decision(
        self,
        approval_id: str,
        outcome: ApprovalOutcome,
        decided_by: str,
        decided_at: float,
        request_hash: str,
    ) -> None:
        """Record a decision for an approval request."""
        pass

    @abstractmethod
    async def get_request(self, approval_id: str) -> dict[str, Any] | None:
        """Load an approval request by ID."""
        pass

    @abstractmethod
    async def wait_for_decision(
        self, approval_id: str, timeout: float
    ) -> ApprovalOutcome:
        """Wait for a decision on an approval request."""
        pass


class InMemoryApprovalStore(ApprovalStore):
    """In-memory approval store with replay protection and expiry."""

    def __init__(self):
        self._requests: dict[str, dict[str, Any]] = {}
        self._events: dict[str, asyncio.Event] = {}
        self._lock = asyncio.Lock()

    async def create_request(
        self, agent_ctx, intent, tool_request, request_hash, reason, expiry
    ) -> str:
        approval_id = str(uuid.uuid4())
        self._requests[approval_id] = {
            "id": approval_id,
            "agent": agent_ctx.to_dict(),
            "intent": intent.to_dict(),
            "tool_request": tool_request.to_dict(),
            "request_hash": request_hash,
            "reason": reason,
            "expiry": expiry,
            "outcome": ApprovalOutcome.DEFERRED,
        }
        self._events[approval_id] = asyncio.Event()
        return approval_id

    async def set_decision(
        self, approval_id, outcome, decided_by, decided_at, request_hash
    ):
        # Security: Use lock for atomic read-modify-write operation
        async with self._lock:
            if approval_id in self._requests:
                req = self._requests[approval_id]
                # Replay protection: hash must match
                if req["request_hash"] != request_hash:
                    raise ValueError(
                        "Request hash mismatch. Approval bound to a different request."
                    )

                req["outcome"] = outcome
                req["decided_by"] = decided_by
                req["decided_at"] = decided_at
                if approval_id in self._events:
                    self._events[approval_id].set()

    async def get_request(self, approval_id):
        return self._requests.get(approval_id)

    async def wait_for_decision(self, approval_id, timeout):
        event = self._events.get(approval_id)
        if not event:
            return ApprovalOutcome.TIMEOUT

        req = self._requests.get(approval_id)
        if req and req["expiry"] < time.time():
            return ApprovalOutcome.TIMEOUT

        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
            return self._requests[approval_id]["outcome"]
        except asyncio.TimeoutError:
            return ApprovalOutcome.TIMEOUT

    async def list_requests(
        self,
        status: str | None = None,
        org_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """List approval requests with optional filters.

        Args:
            status: Filter by status ("pending", "approved", "denied")
            org_id: Filter by organization ID (from agent metadata)

        Returns:
            List of approval request dictionaries
        """
        results = []
        for req in self._requests.values():
            # Filter by status
            if status:
                outcome = req.get("outcome", ApprovalOutcome.DEFERRED)
                if status == "pending" and outcome != ApprovalOutcome.DEFERRED:
                    continue
                if status == "approved" and outcome != ApprovalOutcome.APPROVED:
                    continue
                if status == "denied" and outcome != ApprovalOutcome.DENIED:
                    continue

            # Filter by org_id
            if org_id:
                agent_org = req.get("agent", {}).get("metadata", {}).get("org_id")
                if agent_org != org_id:
                    continue

            results.append(req)

        return results

    async def count_pending(self) -> int:
        """Count pending approval requests."""
        return sum(
            1
            for req in self._requests.values()
            if req.get("outcome") == ApprovalOutcome.DEFERRED
        )


class Approver(Protocol):
    """Async-first approver protocol."""

    async def request_approval_async(
        self,
        agent_ctx: AgentContext,
        intent: Intent,
        tool_request: ToolRequest,
        request_hash: str,
        reason: str,
    ) -> ApprovalOutcome:
        """Request approval from a human or another system."""
        ...


class AsyncQueueApprover:
    """An approver that queues requests in a store and waits for a decision."""

    def __init__(
        self,
        store: ApprovalStore,
        timeout: float = 3600.0,
        default_outcome: ApprovalOutcome = ApprovalOutcome.DENIED,
    ):
        self.store = store
        self.timeout = timeout
        self.default_outcome = default_outcome

    async def request_approval_async(
        self, agent_ctx, intent, tool_request, request_hash, reason
    ) -> ApprovalOutcome:
        expiry = time.time() + self.timeout
        approval_id = await self.store.create_request(
            agent_ctx, intent, tool_request, request_hash, reason, expiry
        )

        outcome = await self.store.wait_for_decision(approval_id, self.timeout)
        if outcome == ApprovalOutcome.TIMEOUT:
            return self.default_outcome
        return outcome


class AutoApprover:
    """Non-interactive approver for tests and examples."""

    async def request_approval_async(
        self,
        _agent_ctx: AgentContext,
        _intent: Intent,
        tool_request: ToolRequest,
        _request_hash: str,
        _reason: str,
    ) -> ApprovalOutcome:
        # Decision: approve ASK only when tool_request.effect == READ
        if tool_request.effect == Effect.READ:
            return ApprovalOutcome.APPROVED
        return ApprovalOutcome.DENIED


class CliApprover:
    """Async-wrapped CLI approver for development."""

    def __init__(self, show_emojis: bool = True, timeout: float = 300.0):
        """
        Initialize CliApprover.

        :param show_emojis: Whether to display emojis in prompts.
        :param timeout: Timeout in seconds for user input (default 5 minutes).
        """
        self.show_emojis = show_emojis
        self.timeout = timeout

    async def request_approval_async(
        self, agent_ctx, intent, tool_request, _hash, reason
    ) -> ApprovalOutcome:
        loop = asyncio.get_event_loop()
        try:
            return await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    self._sync_request,
                    agent_ctx,
                    intent,
                    tool_request,
                    reason,
                ),
                timeout=self.timeout,
            )
        except asyncio.TimeoutError:
            print("\nApproval request timed out.")
            return ApprovalOutcome.TIMEOUT

    def _sync_request(self, agent_ctx, intent, tool_request, reason) -> ApprovalOutcome:
        prefix = "🚦 " if self.show_emojis else ""
        print("\n" + "=" * 40)
        print(f"{prefix}TOLLGATE APPROVAL REQUESTED")
        print("=" * 40)
        print(f"Reason: {reason}")
        print(f"Agent:  {agent_ctx.agent_id} (v{agent_ctx.version})")
        print(f"Intent: {intent.action} - {intent.reason}")
        print(f"Tool:   {tool_request.tool}.{tool_request.action}")
        print(f"Params: {tool_request.params}")
        print("-" * 40)
        choice = input("Approve this tool call? (y/N): ").strip().lower()
        return ApprovalOutcome.APPROVED if choice == "y" else ApprovalOutcome.DENIED


def compute_request_hash(
    agent_ctx: AgentContext, intent: Intent, tool_request: ToolRequest
) -> str:
    """Compute a deterministic hash for a tool request."""

    def canonicalize(d: dict[str, Any]) -> str:
        return json.dumps(d, sort_keys=True)

    payload = "|".join(
        [
            agent_ctx.agent_id,
            agent_ctx.version,
            intent.action,
            tool_request.tool,
            tool_request.action,
            tool_request.effect.value,
            tool_request.resource_type,
            canonicalize(tool_request.params),
            canonicalize(tool_request.metadata),
        ]
    )
    return hashlib.sha256(payload.encode()).hexdigest()
