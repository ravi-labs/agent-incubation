"""
foundry.tollgate.backends.dynamodb_store
─────────────────────────────────────────
DynamoDB-backed ApprovalStore for production deployments.

Replaces InMemoryApprovalStore (lost on Lambda restart) with a persistent,
serverless store suitable for ASK-decision effects (human review workflows).

Install:
    pip install "agent-foundry[aws]"

Usage:
    from foundry.tollgate.backends.dynamodb_store import DynamoDBApprovalStore
    from foundry.tollgate.approvals import AsyncQueueApprover

    store    = DynamoDBApprovalStore(table_name="foundry-approvals")
    approver = AsyncQueueApprover(store=store, timeout=3600.0)
    tower    = ControlTower(policy=policy, approver=approver, audit=audit)

DynamoDB Table Schema:
    Partition key:  approval_id  (String)
    Sort key:       none
    TTL attribute:  ttl          (Number — Unix timestamp)
    GSI:            status-index  on (status, created_at) for queue management

Create the table via CDK (see deploy/cdk/) or manually:
    aws dynamodb create-table \\
      --table-name foundry-approvals \\
      --attribute-definitions AttributeName=approval_id,AttributeType=S \\
      --key-schema AttributeName=approval_id,KeyType=HASH \\
      --billing-mode PAY_PER_REQUEST \\
      --time-to-live-specification AttributeName=ttl,Enabled=true
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any

from ..approvals import ApprovalStore
from ..types import AgentContext, ApprovalOutcome, Intent, ToolRequest

logger = logging.getLogger(__name__)

# Polling interval while waiting for a human decision (seconds)
DEFAULT_POLL_INTERVAL = 5.0


class DynamoDBApprovalStore(ApprovalStore):
    """
    Persistent, serverless approval store backed by Amazon DynamoDB.

    Approval requests survive Lambda cold starts, container restarts, and
    cross-process deployments. Human reviewers interact with the table
    directly (via console, internal tooling, or the SQS→Lambda review flow).

    wait_for_decision() polls DynamoDB — pair with SQSApprover to avoid
    polling entirely when using the SQS → DynamoDB decision callback pattern.
    """

    def __init__(
        self,
        table_name: str,
        region: str | None = None,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
    ):
        """
        Args:
            table_name:    DynamoDB table name (e.g., "foundry-approvals").
            region:        AWS region (defaults to boto3 default / AWS_DEFAULT_REGION).
            poll_interval: Seconds between DynamoDB polls while waiting for a decision.
        """
        self.table_name   = table_name
        self.region       = region
        self.poll_interval = poll_interval
        self._table: Any  = None  # lazy-init

    def _get_table(self) -> Any:
        if self._table is None:
            try:
                import boto3
            except ImportError as exc:
                raise ImportError(
                    "boto3 is not installed. Run: pip install 'agent-foundry[aws]'"
                ) from exc
            kwargs: dict[str, Any] = {}
            if self.region:
                kwargs["region_name"] = self.region
            dynamodb = boto3.resource("dynamodb", **kwargs)
            self._table = dynamodb.Table(self.table_name)
        return self._table

    # ── ApprovalStore interface ────────────────────────────────────────────────

    async def create_request(
        self,
        agent_ctx: AgentContext,
        intent: Intent,
        tool_request: ToolRequest,
        request_hash: str,
        reason: str,
        expiry: float,
    ) -> str:
        approval_id = str(uuid.uuid4())
        ttl = int(expiry) + 3600  # keep record 1h past expiry for audit

        item = {
            "approval_id":   approval_id,
            "agent_id":      agent_ctx.agent_id,
            "agent_version": agent_ctx.version,
            "owner":         agent_ctx.owner,
            "effect":        tool_request.effect.value,
            "tool":          tool_request.tool,
            "action":        tool_request.action,
            "resource_type": tool_request.resource_type,
            "intent_action": intent.action,
            "intent_reason": intent.reason,
            "params_json":   json.dumps(tool_request.params, default=str),
            "request_hash":  request_hash,
            "reason":        reason,
            "status":        "pending",
            "outcome":       ApprovalOutcome.DEFERRED.value,
            "created_at":    str(time.time()),
            "expiry":        str(expiry),
            "ttl":           ttl,
        }

        await asyncio.to_thread(
            self._get_table().put_item, Item=item
        )

        logger.info(
            "approval_created id=%s agent=%s effect=%s",
            approval_id, agent_ctx.agent_id, tool_request.effect.value,
        )
        return approval_id

    async def set_decision(
        self,
        approval_id: str,
        outcome: ApprovalOutcome,
        decided_by: str,
        decided_at: float,
        request_hash: str,
    ) -> None:
        # Verify hash for replay protection
        existing = await self.get_request(approval_id)
        if existing is None:
            raise ValueError(f"Approval request {approval_id!r} not found")
        if existing["request_hash"] != request_hash:
            raise ValueError("Request hash mismatch — approval bound to a different request")

        await asyncio.to_thread(
            self._get_table().update_item,
            Key={"approval_id": approval_id},
            UpdateExpression=(
                "SET #outcome = :outcome, #status = :status, "
                "decided_by = :decided_by, decided_at = :decided_at"
            ),
            ExpressionAttributeNames={
                "#outcome": "outcome",
                "#status":  "status",
            },
            ExpressionAttributeValues={
                ":outcome":    outcome.value,
                ":status":     outcome.value,
                ":decided_by": decided_by,
                ":decided_at": str(decided_at),
            },
        )

        logger.info(
            "approval_decided id=%s outcome=%s by=%s",
            approval_id, outcome.value, decided_by,
        )

    async def get_request(self, approval_id: str) -> dict[str, Any] | None:
        response = await asyncio.to_thread(
            self._get_table().get_item,
            Key={"approval_id": approval_id},
        )
        item = response.get("Item")
        if not item:
            return None
        return {
            "id":            item["approval_id"],
            "agent_id":      item.get("agent_id"),
            "effect":        item.get("effect"),
            "intent_action": item.get("intent_action"),
            "intent_reason": item.get("intent_reason"),
            "reason":        item.get("reason"),
            "request_hash":  item.get("request_hash"),
            "status":        item.get("status", "pending"),
            "outcome":       ApprovalOutcome(item.get("outcome", "deferred")),
            "expiry":        float(item.get("expiry", 0)),
            "created_at":    float(item.get("created_at", 0)),
            "decided_by":    item.get("decided_by"),
            "decided_at":    float(item["decided_at"]) if item.get("decided_at") else None,
        }

    async def wait_for_decision(
        self, approval_id: str, timeout: float
    ) -> ApprovalOutcome:
        """
        Poll DynamoDB until a decision is recorded or timeout expires.

        For lower latency, use SQSApprover which sends an SNS notification
        when a decision is made — eliminating the polling loop entirely.
        """
        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:
            req = await self.get_request(approval_id)

            if req is None:
                logger.warning("Approval request %s not found", approval_id)
                return ApprovalOutcome.TIMEOUT

            if req["expiry"] < time.time():
                return ApprovalOutcome.TIMEOUT

            if req["outcome"] != ApprovalOutcome.DEFERRED:
                return req["outcome"]

            remaining = deadline - time.monotonic()
            await asyncio.sleep(min(self.poll_interval, remaining))

        return ApprovalOutcome.TIMEOUT

    # ── Queue management helpers ───────────────────────────────────────────────

    async def list_pending(
        self,
        agent_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """
        List pending approval requests. Use in a human review dashboard or CLI.

        Args:
            agent_id: Filter to a specific agent's requests.
            limit:    Maximum number of results.

        Returns:
            List of request dicts with id, effect, intent_reason, created_at.
        """
        from boto3.dynamodb.conditions import Attr

        filter_expr = Attr("status").eq("pending")
        if agent_id:
            filter_expr = filter_expr & Attr("agent_id").eq(agent_id)

        response = await asyncio.to_thread(
            self._get_table().scan,
            FilterExpression=filter_expr,
            Limit=limit,
        )
        return [
            {
                "id":            item["approval_id"],
                "agent_id":      item.get("agent_id"),
                "effect":        item.get("effect"),
                "intent_reason": item.get("intent_reason"),
                "reason":        item.get("reason"),
                "created_at":    item.get("created_at"),
            }
            for item in response.get("Items", [])
        ]
