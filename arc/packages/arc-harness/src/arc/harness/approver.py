"""
SandboxApprover — always-approve approver for harness runs.

In the harness every ASK decision is auto-approved so the full pipeline
runs without human intervention. In production swap this for:
  - CliApprover      — interactive terminal approval
  - AsyncQueueApprover — workflow-integrated approval queue

The SandboxApprover also records every ASK it saw, so the DecisionReport
can flag which decisions *would have* required human approval in production.
"""

from dataclasses import dataclass, field
from tollgate.types import ApprovalOutcome


@dataclass
class SandboxApprover:
    """
    Auto-approves every ASK decision in harness mode.

    Records all ASK decisions it processed so the DecisionReport
    can surface them as 'would require human approval in production'.
    """

    _ask_log: list[dict] = field(default_factory=list, init=False, repr=False)

    async def request_approval_async(
        self,
        agent_ctx,
        intent,
        tool_request,
        request_hash: str,
        reason: str,
    ) -> ApprovalOutcome:
        self._ask_log.append({
            "resource_type": tool_request.resource_type,
            "effect":        tool_request.effect.value,
            "intent_action": intent.action,
            "reason":        reason,
            "hash":          request_hash,
        })
        return ApprovalOutcome.APPROVED

    @property
    def ask_count(self) -> int:
        return len(self._ask_log)

    @property
    def ask_log(self) -> list[dict]:
        return list(self._ask_log)
