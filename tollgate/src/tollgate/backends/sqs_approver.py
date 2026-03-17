"""
foundry.tollgate.backends.sqs_approver
────────────────────────────────────────
SQS-based Approver for async human review workflows.

When an agent hits an ASK-decision effect (e.g., compliance.finding.emit.high),
SQSApprover:
  1. Persists the request to DynamoDB (via DynamoDBApprovalStore)
  2. Sends a structured message to the human-review SQS queue
  3. A separate reviewer Lambda processes the queue, calls set_decision(),
     and the waiting agent receives the outcome

This decouples agent execution from human review — the agent Lambda can
return immediately (or wait up to its configured timeout), while reviewers
process the queue asynchronously.

Install:
    pip install "agent-foundry[aws]"

Architecture:

    Agent Lambda
      │ run_effect() → ASK decision
      ├── SQSApprover.request_approval_async()
      │     ├── DynamoDBApprovalStore.create_request()  → persists to DDB
      │     ├── SQS.send_message()                      → notifies reviewers
      │     └── DynamoDBApprovalStore.wait_for_decision() → polls DDB
      │
    Human Review Lambda (triggered by SQS)
      ├── Parses message, presents to reviewer (Slack, email, internal tool)
      └── On decision → DynamoDBApprovalStore.set_decision()
                      → Agent Lambda's poll returns the outcome

Usage:
    from tollgate.backends.dynamodb_store import DynamoDBApprovalStore
    from tollgate.backends.sqs_approver import SQSApprover
    from tollgate.approvals import AsyncQueueApprover

    store    = DynamoDBApprovalStore(table_name="foundry-approvals")
    approver = SQSApprover(
        queue_url="https://sqs.us-east-1.amazonaws.com/123456789/foundry-review",
        store=store,
        timeout=3600.0,
    )
    tower = ControlTower(policy=policy, approver=approver, audit=audit)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from ..types import AgentContext, ApprovalOutcome, Intent, ToolRequest

logger = logging.getLogger(__name__)


class SQSApprover:
    """
    Sends ASK-decision approval requests to an SQS queue for human review.

    Pairs with DynamoDBApprovalStore for persistence + outcome polling.
    The SQS message contains everything a reviewer needs to make a decision:
    agent context, intent, effect name, policy reason, and the approval ID
    they should use when calling set_decision().
    """

    def __init__(
        self,
        queue_url: str,
        store: Any,                         # DynamoDBApprovalStore
        timeout: float = 3600.0,            # 1 hour default
        default_outcome: ApprovalOutcome = ApprovalOutcome.DENIED,
        region: str | None = None,
        message_group_id: str = "foundry-approvals",  # for FIFO queues
    ):
        """
        Args:
            queue_url:        SQS queue URL (standard or FIFO).
            store:            DynamoDBApprovalStore for persistence.
            timeout:          Seconds to wait for a human decision.
            default_outcome:  Outcome if reviewer doesn't respond in time.
            region:           AWS region.
            message_group_id: Message group ID for FIFO queues.
        """
        self.queue_url        = queue_url
        self.store            = store
        self.timeout          = timeout
        self.default_outcome  = default_outcome
        self.region           = region
        self.message_group_id = message_group_id
        self._sqs: Any        = None  # lazy boto3 client

    def _get_sqs(self) -> Any:
        if self._sqs is None:
            try:
                import boto3
            except ImportError as exc:
                raise ImportError(
                    "boto3 is not installed. Run: pip install 'agent-foundry[aws]'"
                ) from exc
            kwargs: dict[str, Any] = {}
            if self.region:
                kwargs["region_name"] = self.region
            self._sqs = boto3.client("sqs", **kwargs)
        return self._sqs

    async def request_approval_async(
        self,
        agent_ctx: AgentContext,
        intent: Intent,
        tool_request: ToolRequest,
        request_hash: str,
        reason: str,
    ) -> ApprovalOutcome:
        """
        Persist the request, send to SQS, and wait for a human decision.

        The SQS message body is a JSON dict that your reviewer Lambda or
        notification system should parse to route the approval to the right person.
        """
        expiry = time.time() + self.timeout

        # 1. Persist to DynamoDB
        approval_id = await self.store.create_request(
            agent_ctx, intent, tool_request, request_hash, reason, expiry
        )

        # 2. Send SQS message with all context for the reviewer
        message = self._build_message(
            approval_id=approval_id,
            agent_ctx=agent_ctx,
            intent=intent,
            tool_request=tool_request,
            reason=reason,
            expiry=expiry,
        )

        await asyncio.to_thread(self._send_message, message, approval_id)

        logger.info(
            "approval_queued id=%s agent=%s effect=%s queue=%s",
            approval_id, agent_ctx.agent_id,
            tool_request.effect.value, self.queue_url.split("/")[-1],
        )

        # 3. Wait for decision via DynamoDB polling
        outcome = await self.store.wait_for_decision(approval_id, self.timeout)

        if outcome == ApprovalOutcome.TIMEOUT:
            logger.warning(
                "approval_timeout id=%s agent=%s — applying default: %s",
                approval_id, agent_ctx.agent_id, self.default_outcome.value,
            )
            return self.default_outcome

        logger.info(
            "approval_received id=%s outcome=%s",
            approval_id, outcome.value,
        )
        return outcome

    def _build_message(
        self,
        approval_id: str,
        agent_ctx: AgentContext,
        intent: Intent,
        tool_request: ToolRequest,
        reason: str,
        expiry: float,
    ) -> dict[str, Any]:
        """
        Build the SQS message body.

        Your reviewer Lambda should parse this and route it to Slack, email,
        or an internal review dashboard. The approval_id is the key the
        reviewer must pass back to set_decision().
        """
        return {
            "foundry_event": "approval_requested",
            "approval_id":   approval_id,
            "expires_at":    expiry,
            "agent": {
                "id":      agent_ctx.agent_id,
                "version": agent_ctx.version,
                "owner":   agent_ctx.owner,
            },
            "effect": tool_request.effect.value,
            "intent": {
                "action": intent.action,
                "reason": intent.reason,
            },
            "policy_reason": reason,
            # Guidance for the reviewer
            "review_guidance": (
                f"Agent '{agent_ctx.agent_id}' is requesting to perform "
                f"'{tool_request.effect.value}'. Reason: {reason}. "
                f"Approve or deny via the approval API before "
                f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(expiry))}."
            ),
        }

    def _send_message(self, message: dict[str, Any], approval_id: str) -> None:
        """Synchronous SQS send (called via asyncio.to_thread)."""
        sqs = self._get_sqs()
        kwargs: dict[str, Any] = {
            "QueueUrl":    self.queue_url,
            "MessageBody": json.dumps(message, default=str),
            "MessageAttributes": {
                "event_type": {
                    "DataType":    "String",
                    "StringValue": "approval_requested",
                },
                "agent_id": {
                    "DataType":    "String",
                    "StringValue": message["agent"]["id"],
                },
                "effect": {
                    "DataType":    "String",
                    "StringValue": message["effect"],
                },
            },
        }

        # FIFO queue support
        if self.queue_url.endswith(".fifo"):
            kwargs["MessageGroupId"]         = self.message_group_id
            kwargs["MessageDeduplicationId"] = approval_id

        sqs.send_message(**kwargs)
