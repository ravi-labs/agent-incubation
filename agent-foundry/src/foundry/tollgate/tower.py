import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from .approvals import Approver, compute_request_hash
from .audit import AuditSink, CompositeAuditSink
from .exceptions import (
    TollgateApprovalDenied,
    TollgateConstraintViolation,
    TollgateDeferred,
    TollgateDenied,
    TollgateRateLimited,
)
from .grants import GrantStore
from .policy import PolicyEvaluator
from .registry import ToolRegistry
from .telemetry import TelemetryAuditSink
from .types import (
    AgentContext,
    ApprovalOutcome,
    AuditEvent,
    Decision,
    DecisionType,
    Intent,
    Outcome,
    ToolRequest,
)


class ControlTower:
    """Async-first control tower for tool execution enforcement.

    Enforcement pipeline (in order):
      0. Verify agent identity (if verify_fn configured)
      0.5. Check circuit breaker (if circuit_breaker configured)   [2.1]
      1. Check rate limits (if rate_limiter configured)
      2. Evaluate policy
      2.5. Check global network policy (if network_guard configured) [2.3]
      3. Validate parameters against schema (if registry configured)
      4. Check constraints (if registry configured)
      5. Handle DENY / ASK / ALLOW
      6. Execute tool → record success/failure in circuit breaker
      7. Audit
    """

    def __init__(
        self,
        policy: PolicyEvaluator,
        approver: Approver,
        audit: AuditSink,
        grant_store: GrantStore | None = None,
        redact_fn: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
        rate_limiter: Any | None = None,  # RateLimiter protocol
        registry: ToolRegistry | None = None,
        verify_fn: Callable[[AgentContext], bool] | None = None,
        circuit_breaker: Any | None = None,  # CircuitBreaker protocol
        network_guard: Any | None = None,  # NetworkGuard
        enable_telemetry: bool | None = None,
    ):
        self.policy = policy
        self.approver = approver

        # Wrap user's audit sink with telemetry for adoption metrics
        telemetry = TelemetryAuditSink(enabled=enable_telemetry)
        if telemetry.enabled:
            self.audit = CompositeAuditSink([audit, telemetry])
        else:
            self.audit = audit

        self.grant_store = grant_store
        self.redact_fn = redact_fn or self._default_redact
        self.rate_limiter = rate_limiter
        self.registry = registry
        self.verify_fn = verify_fn
        self.circuit_breaker = circuit_breaker
        self.network_guard = network_guard

    @staticmethod
    def _default_redact(params: dict[str, Any]) -> dict[str, Any]:
        """Redact sensitive keys by default."""
        sensitive_keys = {
            "password",
            "token",
            "secret",
            "authorization",
            "api_key",
            "key",
        }
        return {
            k: ("[REDACTED]" if k.lower() in sensitive_keys else v)
            for k, v in params.items()
        }

    async def execute_async(
        self,
        agent_ctx: AgentContext,
        intent: Intent,
        tool_request: ToolRequest,
        exec_async: Callable[[], Awaitable[Any]],
    ) -> Any:
        """
        Evaluate and execute a tool call asynchronously.
        """
        correlation_id = str(uuid.uuid4())
        request_hash = compute_request_hash(agent_ctx, intent, tool_request)

        # 0. Verify agent identity (roadmap 1.6)
        if self.verify_fn is not None and not self.verify_fn(agent_ctx):
            decision = Decision(
                decision=DecisionType.DENY,
                reason="Agent identity verification failed.",
            )
            self._log(
                correlation_id,
                request_hash,
                agent_ctx,
                intent,
                tool_request,
                decision,
                Outcome.BLOCKED,
            )
            raise TollgateDenied("Agent identity verification failed.")

        # 0.5. Check circuit breaker (roadmap 2.1)
        if self.circuit_breaker is not None:
            cb_allowed, cb_reason = await self.circuit_breaker.before_call(
                tool_request.tool, tool_request.action
            )
            if not cb_allowed:
                decision = Decision(
                    decision=DecisionType.DENY,
                    reason=cb_reason or "Circuit breaker open.",
                )
                self._log(
                    correlation_id,
                    request_hash,
                    agent_ctx,
                    intent,
                    tool_request,
                    decision,
                    Outcome.BLOCKED,
                )
                raise TollgateDenied(cb_reason or "Circuit breaker open.")

        # 1. Check rate limits (roadmap 1.2)
        if self.rate_limiter is not None:
            allowed, reason, retry_after = await self.rate_limiter.check_rate_limit(
                agent_ctx, tool_request
            )
            if not allowed:
                decision = Decision(
                    decision=DecisionType.DENY,
                    reason=reason or "Rate limit exceeded.",
                )
                self._log(
                    correlation_id,
                    request_hash,
                    agent_ctx,
                    intent,
                    tool_request,
                    decision,
                    Outcome.BLOCKED,
                )
                raise TollgateRateLimited(reason or "Rate limit exceeded.", retry_after)

        # 2. Evaluate Policy
        decision = self.policy.evaluate(agent_ctx, intent, tool_request)

        # 2.5. Check global network policy (roadmap 2.3)
        if decision.decision != DecisionType.DENY and self.network_guard is not None:
            net_violations = self.network_guard.check(tool_request.params)
            if net_violations:
                deny_reason = f"Network policy violation: {'; '.join(net_violations)}"
                decision = Decision(
                    decision=DecisionType.DENY,
                    reason=deny_reason,
                )
                self._log(
                    correlation_id,
                    request_hash,
                    agent_ctx,
                    intent,
                    tool_request,
                    decision,
                    Outcome.BLOCKED,
                )
                raise TollgateConstraintViolation(deny_reason)

        # 3. Validate parameters against schema (roadmap 1.1)
        if decision.decision != DecisionType.DENY and self.registry is not None:
            schema_errors = self.registry.validate_params(
                tool_request.tool, tool_request.params
            )
            if schema_errors:
                deny_reason = f"Parameter validation failed: {'; '.join(schema_errors)}"
                decision = Decision(
                    decision=DecisionType.DENY,
                    reason=deny_reason,
                )
                self._log(
                    correlation_id,
                    request_hash,
                    agent_ctx,
                    intent,
                    tool_request,
                    decision,
                    Outcome.BLOCKED,
                )
                raise TollgateDenied(deny_reason)

            # 3.5. Check per-tool constraints (roadmap 1.4)
            constraint_violations = self.registry.check_constraints(
                tool_request.tool, tool_request.params
            )
            if constraint_violations:
                deny_reason = (
                    f"Constraint violation: {'; '.join(constraint_violations)}"
                )
                decision = Decision(
                    decision=DecisionType.DENY,
                    reason=deny_reason,
                )
                self._log(
                    correlation_id,
                    request_hash,
                    agent_ctx,
                    intent,
                    tool_request,
                    decision,
                    Outcome.BLOCKED,
                )
                raise TollgateConstraintViolation(deny_reason)

        # 4. Handle DENY
        if decision.decision == DecisionType.DENY:
            self._log(
                correlation_id,
                request_hash,
                agent_ctx,
                intent,
                tool_request,
                decision,
                Outcome.BLOCKED,
            )
            raise TollgateDenied(decision.reason)

        # 5. Handle ASK
        if decision.decision == DecisionType.ASK:
            # 5.1 Check Grants
            if self.grant_store:
                matching_grant = await self.grant_store.find_matching_grant(
                    agent_ctx, tool_request
                )
                if matching_grant:
                    return await self._execute_and_log(
                        correlation_id,
                        request_hash,
                        agent_ctx,
                        intent,
                        tool_request,
                        decision,
                        exec_async,
                        grant_id=matching_grant.id,
                    )

            # 5.2 Request Approval if no grant found
            outcome = await self.approver.request_approval_async(
                agent_ctx, intent, tool_request, request_hash, decision.reason
            )

            if outcome == ApprovalOutcome.DEFERRED:
                self._log(
                    correlation_id,
                    request_hash,
                    agent_ctx,
                    intent,
                    tool_request,
                    decision,
                    Outcome.BLOCKED,
                )
                raise TollgateDeferred("pending")

            if outcome != ApprovalOutcome.APPROVED:
                final_outcome = (
                    Outcome.TIMEOUT
                    if outcome == ApprovalOutcome.TIMEOUT
                    else Outcome.APPROVAL_DENIED
                )
                self._log(
                    correlation_id,
                    request_hash,
                    agent_ctx,
                    intent,
                    tool_request,
                    decision,
                    final_outcome,
                )
                raise TollgateApprovalDenied(f"Approval failed: {outcome.value}")

        # 6. Execute tool (Policy ALLOW or Approval APPROVED)
        return await self._execute_and_log(
            correlation_id,
            request_hash,
            agent_ctx,
            intent,
            tool_request,
            decision,
            exec_async,
        )

    async def _execute_and_log(
        self,
        correlation_id: str,
        request_hash: str,
        agent_ctx: AgentContext,
        intent: Intent,
        tool_request: ToolRequest,
        decision: Decision,
        exec_async: Callable[[], Awaitable[Any]],
        grant_id: str | None = None,
    ) -> Any:
        """Internal helper to execute tool and log result."""
        result = None
        outcome = Outcome.EXECUTED
        try:
            result = await exec_async()
        except Exception as e:
            outcome = Outcome.FAILED
            # Record failure in circuit breaker (roadmap 2.1)
            if self.circuit_breaker is not None:
                await self.circuit_breaker.record_failure(
                    tool_request.tool, tool_request.action
                )
            result_summary = self._sanitize_exception(e)
            self._log(
                correlation_id,
                request_hash,
                agent_ctx,
                intent,
                tool_request,
                decision,
                outcome,
                grant_id=grant_id,
                result_summary=result_summary,
            )
            raise

        # Record success in circuit breaker (roadmap 2.1)
        if self.circuit_breaker is not None:
            await self.circuit_breaker.record_success(
                tool_request.tool, tool_request.action
            )

        # Final Audit
        result_summary = self._truncate_result(result)
        self._log(
            correlation_id,
            request_hash,
            agent_ctx,
            intent,
            tool_request,
            decision,
            outcome,
            grant_id=grant_id,
            result_summary=result_summary,
        )

        return result

    def execute(
        self,
        agent_ctx: AgentContext,
        intent: Intent,
        tool_request: ToolRequest,
        exec_sync: Callable[[], Any],
    ) -> Any:
        """Sync wrapper for execute_async. Safe only if no event loop is running."""
        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                raise RuntimeError(
                    "execute() called from within a running event loop. "
                    "Use execute_async() instead."
                )
        except RuntimeError:
            pass

        async def _exec():
            return exec_sync()

        return asyncio.run(self.execute_async(agent_ctx, intent, tool_request, _exec))

    def _log(
        self,
        correlation_id: str,
        request_hash: str,
        agent: AgentContext,
        intent: Intent,
        req: ToolRequest,
        decision: Decision,
        outcome: Outcome,
        approval_id: str | None = None,
        grant_id: str | None = None,
        result_summary: str | None = None,
    ):
        # Redact params before logging
        redacted_req = ToolRequest(
            tool=req.tool,
            action=req.action,
            resource_type=req.resource_type,
            effect=req.effect,
            params=self.redact_fn(req.params),
            metadata=req.metadata,
            manifest_version=req.manifest_version,
        )

        event = AuditEvent(
            timestamp=datetime.now(timezone.utc).isoformat(),
            correlation_id=correlation_id,
            request_hash=request_hash,
            agent=agent,
            intent=intent,
            tool_request=redacted_req,
            decision=decision,
            outcome=outcome,
            approval_id=approval_id,
            grant_id=grant_id,
            result_summary=result_summary,
            policy_version=decision.policy_version,
            manifest_version=req.manifest_version,
        )
        self.audit.emit(event)

    def _sanitize_exception(self, e: Exception) -> str:
        """Sanitize exception message to avoid leaking sensitive data."""
        return f"{type(e).__name__}: Execution failed"

    def _truncate_result(self, result: Any, max_chars: int = 200) -> str | None:
        if result is None:
            return None
        s = str(result)
        return s[:max_chars] + "..." if len(s) > max_chars else s
