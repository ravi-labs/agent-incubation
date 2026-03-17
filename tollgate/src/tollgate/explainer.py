"""Decision Explainer for Tollgate.

Provides detailed explanations of why a policy decision was made, helping
developers understand and debug policy behavior.

Usage:

    from tollgate import YamlPolicyEvaluator, AgentContext, Intent, ToolRequest
    from tollgate.explainer import DecisionExplainer

    evaluator = YamlPolicyEvaluator("policy.yaml")
    explainer = DecisionExplainer(evaluator)

    ctx = AgentContext(agent_id="agent-1", version="1.0", owner="user")
    intent = Intent(action="fetch", reason="user request")
    req = ToolRequest(tool="api:fetch", action="get", ...)

    # Get decision with full explanation
    result = explainer.explain(ctx, intent, req)
    print(result.summary())

    # Pretty-print the explanation
    result.print()
"""

from dataclasses import dataclass, field
from typing import Any

from .policy import YamlPolicyEvaluator
from .types import AgentContext, Decision, DecisionType, Effect, Intent, ToolRequest


@dataclass
class RuleMatchResult:
    """Result of evaluating a single rule."""

    rule_index: int
    rule: dict[str, Any]
    matched: bool
    match_details: list[str] = field(default_factory=list)
    fail_reasons: list[str] = field(default_factory=list)

    @property
    def rule_id(self) -> str | None:
        return self.rule.get("id")

    @property
    def decision_type(self) -> str:
        return self.rule.get("decision", "UNKNOWN")


@dataclass
class ExplanationResult:
    """Complete explanation of a policy decision."""

    decision: Decision
    agent_ctx: AgentContext
    intent: Intent
    tool_request: ToolRequest
    rule_evaluations: list[RuleMatchResult]
    matched_rule_index: int | None
    policy_version: str | None

    @property
    def matched_rule(self) -> RuleMatchResult | None:
        """The rule that matched and produced the decision."""
        if self.matched_rule_index is not None:
            return self.rule_evaluations[self.matched_rule_index]
        return None

    @property
    def rules_checked(self) -> int:
        """Number of rules evaluated before a decision was made."""
        if self.matched_rule_index is not None:
            return self.matched_rule_index + 1
        return len(self.rule_evaluations)

    def summary(self) -> str:
        """Return a short summary of the decision."""
        lines = [
            f"Decision: {self.decision.decision.value}",
            f"Reason: {self.decision.reason}",
        ]
        if self.matched_rule:
            rule_id = self.matched_rule.rule_id or f"#{self.matched_rule_index}"
            lines.append(f"Matched Rule: {rule_id}")
        else:
            lines.append("Matched Rule: None (default deny)")
        lines.append(f"Rules Checked: {self.rules_checked}")
        return "\n".join(lines)

    def detailed_report(self) -> str:
        """Return a detailed report of all rule evaluations."""
        lines = [
            "=" * 70,
            "  DECISION EXPLANATION",
            "=" * 70,
            "",
            "  REQUEST CONTEXT",
            "  ---------------",
            f"  Agent ID: {self.agent_ctx.agent_id}",
            f"  Agent Version: {self.agent_ctx.version}",
            f"  Agent Owner: {self.agent_ctx.owner}",
        ]
        if self.agent_ctx.org_id:
            lines.append(f"  Org ID: {self.agent_ctx.org_id}")
        if self.agent_ctx.is_delegated:
            lines.append(f"  Delegated By: {' -> '.join(self.agent_ctx.delegated_by)}")
            lines.append(f"  Delegation Depth: {self.agent_ctx.delegation_depth}")
        lines.extend(
            [
                "",
                f"  Intent Action: {self.intent.action}",
                f"  Intent Reason: {self.intent.reason}",
                "",
                f"  Tool: {self.tool_request.tool}",
                f"  Action: {self.tool_request.action}",
                f"  Resource Type: {self.tool_request.resource_type}",
                f"  Effect: {self.tool_request.effect.value}",
                f"  Manifest: {self.tool_request.manifest_version or 'Not set'}",
                "",
                "  POLICY EVALUATION",
                "  -----------------",
                f"  Policy Version: {self.policy_version or 'Unknown'}",
                "",
            ]
        )

        for i, rule_result in enumerate(self.rule_evaluations):
            is_match = rule_result.matched
            marker = "[MATCHED]" if is_match else "[SKIPPED]"
            rule_id = rule_result.rule_id or f"Rule #{i}"
            decision = rule_result.decision_type

            lines.append(f"  {marker} {rule_id} -> {decision}")

            if rule_result.match_details:
                for detail in rule_result.match_details:
                    lines.append(f"      + {detail}")

            if rule_result.fail_reasons:
                for reason in rule_result.fail_reasons:
                    lines.append(f"      - {reason}")

            lines.append("")

            # Stop after matched rule
            if is_match:
                break

        lines.extend(
            [
                "  FINAL DECISION",
                "  --------------",
                f"  Decision: {self.decision.decision.value}",
                f"  Reason: {self.decision.reason}",
            ]
        )

        if self.decision.policy_id:
            lines.append(f"  Policy ID: {self.decision.policy_id}")

        lines.append("=" * 70)
        return "\n".join(lines)

    def print(self) -> None:
        """Print the detailed report to stdout."""
        print(self.detailed_report())


class DecisionExplainer:
    """Explains policy decisions in detail.

    Wraps a YamlPolicyEvaluator and provides detailed explanations
    of why each rule matched or didn't match.
    """

    def __init__(self, evaluator: YamlPolicyEvaluator):
        """Initialize with a policy evaluator.

        Args:
            evaluator: A YamlPolicyEvaluator instance with loaded rules.
        """
        self._evaluator = evaluator

    def explain(
        self, agent_ctx: AgentContext, intent: Intent, tool_request: ToolRequest
    ) -> ExplanationResult:
        """Evaluate a request and explain the decision.

        Args:
            agent_ctx: The agent context.
            intent: The stated intent.
            tool_request: The tool request to evaluate.

        Returns:
            ExplanationResult with full decision explanation.
        """
        rule_evaluations: list[RuleMatchResult] = []
        matched_rule_index: int | None = None

        # Handle unknown effect (early return)
        if tool_request.effect == Effect.UNKNOWN:
            decision = Decision(
                decision=self._evaluator.default_if_unknown,
                reason="Unknown tool effect. Safe default applied.",
                policy_version=self._evaluator.version,
            )
            return ExplanationResult(
                decision=decision,
                agent_ctx=agent_ctx,
                intent=intent,
                tool_request=tool_request,
                rule_evaluations=[],
                matched_rule_index=None,
                policy_version=self._evaluator.version,
            )

        # Evaluate each rule
        for i, rule in enumerate(self._evaluator.rules):
            result = self._evaluate_rule(rule, i, agent_ctx, intent, tool_request)
            rule_evaluations.append(result)

            if result.matched:
                matched_rule_index = i
                break

        # Determine final decision
        if matched_rule_index is not None:
            matched_rule = self._evaluator.rules[matched_rule_index]
            decision_type = DecisionType(matched_rule["decision"])

            # Check trusted metadata requirement for ALLOW
            no_manifest = not tool_request.manifest_version
            if decision_type == DecisionType.ALLOW and no_manifest:
                decision = Decision(
                    decision=DecisionType.ASK,
                    reason="ALLOW requires trusted tool metadata from registry.",
                    policy_version=self._evaluator.version,
                )
            else:
                decision = Decision(
                    decision=decision_type,
                    reason=matched_rule.get("reason", "Rule matched"),
                    policy_id=matched_rule.get("id"),
                    policy_version=self._evaluator.version,
                    metadata=matched_rule.get("metadata", {}),
                )
        else:
            decision = Decision(
                decision=DecisionType.DENY,
                reason="No matching policy rule found. Defaulting to DENY.",
                policy_version=self._evaluator.version,
            )

        return ExplanationResult(
            decision=decision,
            agent_ctx=agent_ctx,
            intent=intent,
            tool_request=tool_request,
            rule_evaluations=rule_evaluations,
            matched_rule_index=matched_rule_index,
            policy_version=self._evaluator.version,
        )

    def _evaluate_rule(
        self,
        rule: dict[str, Any],
        index: int,
        agent_ctx: AgentContext,
        intent: Intent,
        req: ToolRequest,
    ) -> RuleMatchResult:
        """Evaluate a single rule and collect match/fail details."""
        match_details: list[str] = []
        fail_reasons: list[str] = []
        matched = True

        # Check tool
        if "tool" in rule:
            if rule["tool"] == req.tool:
                match_details.append(f"tool={req.tool}")
            else:
                fail_reasons.append(f"tool: expected '{rule['tool']}', got '{req.tool}'")  # noqa: E501
                matched = False

        # Check action
        if "action" in rule:
            if rule["action"] == req.action:
                match_details.append(f"action={req.action}")
            else:
                fail_reasons.append(
                    f"action: expected '{rule['action']}', got '{req.action}'"
                )
                matched = False

        # Check resource_type
        if "resource_type" in rule:
            if rule["resource_type"] == req.resource_type:
                match_details.append(f"resource_type={req.resource_type}")
            else:
                fail_reasons.append(
                    f"resource_type: expected '{rule['resource_type']}', "
                    f"got '{req.resource_type}'"
                )
                matched = False

        # Check effect
        if "effect" in rule:
            if rule["effect"] == req.effect.value:
                match_details.append(f"effect={req.effect.value}")
            else:
                fail_reasons.append(
                    f"effect: expected '{rule['effect']}', got '{req.effect.value}'"
                )
                matched = False

        # Check agent context
        if "agent" in rule:
            agent_rule = rule["agent"]

            # Check allowed agent attrs
            for key in self._evaluator.ALLOWED_AGENT_ATTRS:
                if key not in agent_rule:
                    continue

                expected = agent_rule[key]
                if key == "org_id":
                    actual = agent_ctx.org_id
                else:
                    actual = getattr(agent_ctx, key, None)

                if actual == expected:
                    match_details.append(f"agent.{key}={expected}")
                else:
                    fail_reasons.append(
                        f"agent.{key}: expected '{expected}', got '{actual}'"
                    )
                    matched = False

            # Check delegation constraints
            if agent_rule.get("deny_delegated") and agent_ctx.is_delegated:
                fail_reasons.append("deny_delegated=true but agent is delegated")
                matched = False
            elif agent_rule.get("deny_delegated") and not agent_ctx.is_delegated:
                match_details.append("deny_delegated=true, agent is not delegated")

            max_depth = agent_rule.get("max_delegation_depth")
            if max_depth is not None:
                if agent_ctx.delegation_depth > max_depth:
                    fail_reasons.append(
                        f"max_delegation_depth={max_depth}, "
                        f"actual depth={agent_ctx.delegation_depth}"
                    )
                    matched = False
                else:
                    match_details.append(
                        f"delegation_depth {agent_ctx.delegation_depth} <= {max_depth}"
                    )

            allowed = agent_rule.get("allowed_delegators")
            if allowed is not None:
                if not agent_ctx.is_delegated:
                    fail_reasons.append(
                        "allowed_delegators set but agent is not delegated"
                    )
                    matched = False
                elif any(d in allowed for d in agent_ctx.delegated_by):
                    match_details.append(f"delegator in allowed_delegators: {allowed}")
                else:
                    fail_reasons.append(
                        f"delegator not in allowed_delegators: {allowed}"
                    )
                    matched = False

            blocked = agent_rule.get("blocked_delegators")
            if (
                blocked is not None
                and agent_ctx.is_delegated
                and any(d in blocked for d in agent_ctx.delegated_by)
            ):
                fail_reasons.append(f"delegator in blocked_delegators: {blocked}")
                matched = False

        # Check intent
        if "intent" in rule:
            for key in self._evaluator.ALLOWED_INTENT_ATTRS:
                if key not in rule["intent"]:
                    continue

                expected = rule["intent"][key]
                actual = getattr(intent, key, None)

                if actual == expected:
                    match_details.append(f"intent.{key}={expected}")
                else:
                    fail_reasons.append(
                        f"intent.{key}: expected '{expected}', got '{actual}'"
                    )
                    matched = False

        # Check when conditions
        if "when" in rule:
            for key, condition in rule["when"].items():
                val = req.metadata.get(key)
                if self._evaluator._check_condition(val, condition):
                    match_details.append(f"when.{key} satisfied: {condition}")
                else:
                    fail_reasons.append(
                        f"when.{key}: condition {condition} failed, value={val}"
                    )
                    matched = False

        return RuleMatchResult(
            rule_index=index,
            rule=rule,
            matched=matched,
            match_details=match_details,
            fail_reasons=fail_reasons,
        )

    def explain_many(
        self,
        scenarios: list[tuple[AgentContext, Intent, ToolRequest]],
    ) -> list[ExplanationResult]:
        """Explain multiple scenarios at once.

        Args:
            scenarios: List of (agent_ctx, intent, tool_request) tuples.

        Returns:
            List of ExplanationResult for each scenario.
        """
        return [self.explain(ctx, intent, req) for ctx, intent, req in scenarios]
