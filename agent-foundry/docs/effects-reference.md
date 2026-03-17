# FinancialEffect Reference

Complete reference for all declared `FinancialEffect` values, their tiers, default policy decisions, and intended usage.

Import: `from foundry.policy.effects import FinancialEffect`

---

## Tier 1 — Data Access (READ)

Default decision: **ALLOW**

These effects are read-only. They access existing data without modifying system state. Safe to run without human review.

| Effect Value | Constant | Description |
|--------------|----------|-------------|
| `data.participant.read` | `DATA_PARTICIPANT_READ` | Read participant account, balance, contribution data |
| `data.plan.read` | `DATA_PLAN_READ` | Read plan configuration, fund lineup, fee schedules |
| `data.market.read` | `DATA_MARKET_READ` | Read market prices, benchmark indices, returns data |
| `data.compliance.read` | `DATA_COMPLIANCE_READ` | Read regulatory filings, audit records, disclosures |
| `knowledge.base.retrieve` | `KNOWLEDGE_BASE_RETRIEVE` | Retrieve passages from a Bedrock Knowledge Base |

---

## Tier 2 — Compute & Analysis

Default decision: **ALLOW**

These effects run computations against data. They produce scores, projections, or analyses but do not output results externally.

| Effect Value | Constant | Description |
|--------------|----------|-------------|
| `risk.score.compute` | `RISK_SCORE_COMPUTE` | Compute participant risk score or retirement readiness index |
| `data.analysis.run` | `DATA_ANALYSIS_RUN` | Run statistical analysis or model inference |
| `fee.analysis.run` | `FEE_ANALYSIS_RUN` | Compare fund fees against benchmarks |
| `performance.analysis.run` | `PERFORMANCE_ANALYSIS_RUN` | Evaluate fund performance vs. index |
| `projection.compute` | `PROJECTION_COMPUTE` | Generate retirement income or trajectory projections |

---

## Tier 3 — Internal Draft

Default decision: **ALLOW**

These effects generate internal drafts, findings, or reports. Output stays within the agent system — nothing is sent externally.

| Effect Value | Constant | Description |
|--------------|----------|-------------|
| `intervention.draft` | `INTERVENTION_DRAFT` | Draft a personalised participant intervention message |
| `finding.generate` | `FINDING_GENERATE` | Generate a fiduciary finding or risk flag |
| `report.draft` | `REPORT_DRAFT` | Draft an internal compliance or monitoring report |
| `recommendation.draft` | `RECOMMENDATION_DRAFT` | Draft a fund or allocation recommendation (not yet sent) |

---

## Tier 4 — Output / External

Default decision: **ASK** _(requires human review by default)_

These effects produce external-facing output or invoke external services. Every invocation is queued for human approval unless a policy rule explicitly overrides to ALLOW.

| Effect Value | Constant | Description |
|--------------|----------|-------------|
| `participant.communication.send` | `PARTICIPANT_COMMUNICATION_SEND` | Send a message, email, or notification to a plan participant |
| `advisor.alert.send` | `ADVISOR_ALERT_SEND` | Send an alert or recommendation to a plan advisor |
| `compliance.report.publish` | `COMPLIANCE_REPORT_PUBLISH` | Publish a compliance report to a regulatory or internal system |
| `bedrock.agent.invoke` | `BEDROCK_AGENT_INVOKE` | Invoke another Bedrock Agent (cross-agent delegation) |
| `webhook.call` | `WEBHOOK_CALL` | Make an outbound webhook call to an external system |

---

## Tier 5 — State Change

Default decision: **ASK** _(requires human review)_

These effects modify persistent state in production systems. High consequence — always requires explicit approval unless the policy specifically permits auto-approval for narrow, well-understood cases.

| Effect Value | Constant | Description |
|--------------|----------|-------------|
| `account.transaction.execute` | `ACCOUNT_TRANSACTION_EXECUTE` | Execute a financial transaction on a participant account |
| `contribution.rate.update` | `CONTRIBUTION_RATE_UPDATE` | Update a participant's contribution rate |
| `fund.election.update` | `FUND_ELECTION_UPDATE` | Change a participant's fund elections or allocations |
| `policy.override` | `POLICY_OVERRIDE` | Override a default policy rule for a specific case |
| `alert.escalate` | `ALERT_ESCALATE` | Escalate an alert to the next review level |

---

## Tier 6 — System Control

Default decision: **DENY** _(always blocked unless explicitly enabled in policy)_

These effects modify the agent platform itself. They cannot be enabled by an individual agent's manifest — they require explicit system-level policy grants.

| Effect Value | Constant | Description |
|--------------|----------|-------------|
| `agent.promote` | `AGENT_PROMOTE` | Promote an agent from SANDBOX → STAGING → PRODUCTION |
| `agent.suspend` | `AGENT_SUSPEND` | Suspend or deactivate a running agent |
| `system.config.change` | `SYSTEM_CONFIG_CHANGE` | Modify system-level configuration |
| `audit.log.read` | `AUDIT_LOG_READ` | Read audit trail records |

---

## Declaring Effects in a Manifest

An agent can only invoke effects listed in its `allowed_effects`. Attempting an undeclared effect raises `PermissionError` before the policy engine is consulted.

```python
manifest = AgentManifest(
    agent_id        = 'my-agent',
    allowed_effects = [
        FinancialEffect.DATA_PARTICIPANT_READ,    # Tier 1 — auto-allowed
        FinancialEffect.RISK_SCORE_COMPUTE,       # Tier 2 — auto-allowed
        FinancialEffect.INTERVENTION_DRAFT,       # Tier 3 — auto-allowed
        FinancialEffect.PARTICIPANT_COMMUNICATION_SEND,  # Tier 4 — will ASK
    ],
    # ...
)
```

---

## Handling ASK (TollgateDeferred)

When the policy engine returns `ASK`, a `TollgateDeferred` exception is raised. The request is logged to the audit trail and routed to the human review queue (SQS by default).

```python
from foundry.tollgate.exceptions import TollgateDeferred

try:
    await self.run_effect(
        effect = FinancialEffect.PARTICIPANT_COMMUNICATION_SEND,
        # ...
    )
except TollgateDeferred as e:
    # Request is queued — agent should surface this to the caller
    return {'status': 'pending_review', 'review_id': e.review_id}
```

In eval scenarios, use `expect_effects_asked` to assert that an effect correctly routes to human review without treating `TollgateDeferred` as a failure.

---

*Agent Foundry · FinancialEffect Reference · v0.1.0 · March 2026*
