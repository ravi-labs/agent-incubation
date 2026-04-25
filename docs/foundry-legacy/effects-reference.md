# Agent Foundry — Financial Effect Reference

> Complete taxonomy of `FinancialEffect` values. Every agent action must declare one of these effects in its `AgentManifest`. The declared set is enforced at runtime — undeclared effects raise `PermissionError` before reaching the policy engine.

---

## Overview

Effects are organised into six tiers of increasing sensitivity. The tier determines the default `ControlTower` decision (`ALLOW` / `ASK` / `DENY`) and whether human review is required.

| Tier | Name | Default | Notes |
|------|------|---------|-------|
| 1 | Data Access | ALLOW | Read-only; broadly safe within declared scope |
| 2 | Computation | ALLOW | Internal processing; no external impact |
| 3 | Draft / Internal | ALLOW | Content created but not yet delivered |
| 4 | Output / External | ASK | Leaves the system — highest scrutiny |
| 5 | Persistence | ALLOW | Audit and log writes; always permitted |
| 6 | System Control | ASK / DENY | Platform-level operations; hard denies |

Import the metadata registry for runtime introspection:

```python
from foundry.policy.effects import FinancialEffect, EFFECT_METADATA, effects_by_tier, effects_requiring_review
```

---

## Tier 1 — Data Access

Read-only operations. Permitted automatically within an agent's declared scope.

| Enum Constant | Effect Value | Description |
|---------------|-------------|-------------|
| `PARTICIPANT_DATA_READ` | `participant.data.read` | Read participant PII, account balance, and contribution history |
| `PARTICIPANT_ACTIVITY_READ` | `participant.activity.read` | Read participant behavioral signals: login frequency, allocation changes |
| `PARTICIPANT_COHORT_READ` | `participant.cohort.read` | Read aggregated/anonymized peer cohort data — no individual PII |
| `PLAN_DATA_READ` | `plan.data.read` | Read plan configuration and design parameters |
| `PLAN_DEMOGRAPHICS_READ` | `plan.demographics.read` | Read workforce demographic distributions at plan level (not individual) |
| `FUND_PERFORMANCE_READ` | `fund.performance.read` | Read fund returns and benchmark performance data |
| `FUND_FEES_READ` | `fund.fees.read` | Read fund expense ratios and fee structure data |
| `EMPLOYER_FEED_READ` | `employer.feed.read` | Read employer HR/payroll data feeds (terminations, new hires) |
| `MARKET_DATA_READ` | `market.data.read` | Read market indices and benchmark data |
| `KNOWLEDGE_BASE_RETRIEVE` | `knowledge.base.retrieve` | Retrieve relevant passages from an Amazon Bedrock Knowledge Base for RAG |

**Manifest example:**

```yaml
allowed_effects:
  - participant.data.read
  - participant.activity.read
  - plan.data.read
  - knowledge.base.retrieve
```

---

## Tier 2 — Computation

Internal computation with no external side effects. All decisions default to ALLOW.

| Enum Constant | Effect Value | Description |
|---------------|-------------|-------------|
| `RISK_SCORE_COMPUTE` | `risk.score.compute` | Compute retirement risk or trajectory score for a participant |
| `SCENARIO_MODEL_EXECUTE` | `scenario.model.execute` | Execute a plan design scenario model via Code Interpreter |
| `COMPLIANCE_EVALUATE` | `compliance.evaluate` | Evaluate plan or fund data against a regulatory threshold (ERISA, DOL) |
| `LIFE_EVENT_SCORE` | `life.event.score` | Score the probability that a participant has experienced a life event |

**Manifest example:**

```yaml
allowed_effects:
  - risk.score.compute
  - compliance.evaluate
  - life.event.score
```

---

## Tier 3 — Draft / Internal

Content is created and stored internally but **not delivered**. Promotion to Tier 4 (send/emit/deliver) requires a separate declared effect.

| Enum Constant | Effect Value | Description |
|---------------|-------------|-------------|
| `INTERVENTION_DRAFT` | `intervention.draft` | Draft a participant intervention message — not yet sent |
| `OUTREACH_DRAFT` | `outreach.draft` | Draft participant outreach content for a life event — not yet sent |
| `FINDING_DRAFT` | `finding.draft` | Draft a compliance finding memo — not yet emitted |
| `RECOMMENDATION_DRAFT` | `recommendation.draft` | Draft a plan design recommendation — not yet delivered |

**Pattern — draft then review then send:**

```python
# Step 1 — draft (ALLOW, Tier 3)
await self.run_effect(FinancialEffect.FINDING_DRAFT, payload={"text": memo})

# Step 2 — emit (ASK, Tier 4) — ControlTower pauses for human approval
await self.run_effect(FinancialEffect.COMPLIANCE_FINDING_EMIT_HIGH, payload={"finding_id": fid})
```

---

## Tier 4 — Output / External

Actions that **leave the system** or reach real people. All require explicit declaration. Most default to ASK or require human review.

| Enum Constant | Effect Value | Default | Human Review | Description |
|---------------|-------------|---------|--------------|-------------|
| `PARTICIPANT_COMMUNICATION_SEND` | `participant.communication.send` | ASK | ✅ Yes | Send a message to a participant via email, push, or in-app |
| `COMPLIANCE_FINDING_EMIT_LOW` | `compliance.finding.emit.low` | ALLOW | ❌ No | Emit a low-severity compliance finding to the monitoring dashboard |
| `COMPLIANCE_FINDING_EMIT_HIGH` | `compliance.finding.emit.high` | ASK | ✅ Yes | Emit a high-severity compliance finding — always requires human review |
| `RECOMMENDATION_DELIVER` | `recommendation.deliver` | ALLOW | ❌ No | Deliver a plan design recommendation to an internal relationship manager |
| `ADVISOR_ESCALATION_TRIGGER` | `advisor.escalation.trigger` | ASK | ✅ Yes | Trigger routing to an advisor or call center |
| `HUMAN_REVIEW_QUEUE_ADD` | `human.review.queue.add` | ALLOW | ❌ No | Add an item to the human compliance review queue |
| `BEDROCK_AGENT_INVOKE` | `bedrock.agent.invoke` | ASK | ❌ No | Delegate a task to another Amazon Bedrock Agent via `invoke_agent` |

> **Note on `BEDROCK_AGENT_INVOKE`:** The delegate agent may take further external actions of its own. Foundry defaults to ASK to ensure the orchestrating agent explicitly approves any cross-agent delegation.

**Manifest example:**

```yaml
allowed_effects:
  - compliance.finding.emit.low
  - compliance.finding.emit.high
  - human.review.queue.add
```

**YAML policy override — promote low findings to ALLOW, keep high at ASK:**

```yaml
rules:
  - effect: compliance.finding.emit.low
    decision: ALLOW
  - effect: compliance.finding.emit.high
    decision: ASK
    notify: [compliance-team@example.com]
```

---

## Tier 5 — Persistence

Audit and operational log writes. All default to ALLOW. Logging is always permitted and required — you cannot policy-deny a Tier 5 write.

| Enum Constant | Effect Value | Description |
|---------------|-------------|-------------|
| `AUDIT_LOG_WRITE` | `audit.log.write` | Write a cryptographic audit entry — always permitted, always required |
| `INTERVENTION_LOG_WRITE` | `intervention.log.write` | Log that an intervention was generated and/or delivered |
| `FINDING_LOG_WRITE` | `finding.log.write` | Log a compliance finding with full context for audit purposes |
| `OUTCOME_LOG_WRITE` | `outcome.log.write` | Log an outcome for ROI tracking (did participant act? was finding resolved?) |
| `FOLLOWUP_SCHEDULE` | `followup.schedule` | Schedule a follow-up action in the tracker pipeline |

> `BaseAgent.log_outcome()` automatically invokes `OUTCOME_LOG_WRITE`. You do not need to declare it separately unless you want fine-grained YAML policy control over it.

---

## Tier 6 — System Control

Platform-level operations. `AGENT_SUSPEND` defaults to ASK; the remaining effects and all hard denies default to DENY and cannot be granted to regular agents via YAML policy.

| Enum Constant | Effect Value | Default | Human Review | Description |
|---------------|-------------|---------|--------------|-------------|
| `AGENT_SUSPEND` | `agent.suspend` | ASK | ✅ Yes | Suspend an agent via circuit breaker — requires operator approval |
| `AGENT_PROMOTE` | `agent.promote` | DENY | ✅ Yes | Promote agent from sandbox to production — lifecycle manager only |
| `POLICY_RULE_MODIFY` | `policy.rule.modify` | DENY | ✅ Yes | Modify a policy rule at runtime — never permitted from an agent |

### Hard Denies

These effects exist in the taxonomy to make the prohibition explicit and auditable. No YAML policy rule can override them.

| Enum Constant | Effect Value | Description |
|---------------|-------------|-------------|
| `PARTICIPANT_DATA_WRITE` | `participant.data.write` | Modify participant records directly — hard blocked for all agents |
| `PLAN_DATA_WRITE` | `plan.data.write` | Modify plan configuration directly — hard blocked for all agents |
| `ACCOUNT_TRANSACTION_EXECUTE` | `account.transaction.execute` | Execute a financial transaction — hard blocked; no agent ever does this |

---

## Runtime Introspection

```python
from foundry.policy.effects import (
    FinancialEffect,
    EffectTier,
    EFFECT_METADATA,
    effects_by_tier,
    effects_requiring_review,
    effect_meta,
)

# All Tier 4 effects
tier4 = effects_by_tier(EffectTier.OUTPUT)

# All effects that require human sign-off
review_required = effects_requiring_review()

# Metadata for a single effect
meta = effect_meta(FinancialEffect.COMPLIANCE_FINDING_EMIT_HIGH)
print(meta.default_decision)       # DefaultDecision.ASK
print(meta.requires_human_review)  # True
print(meta.description)            # "Emit a high-severity compliance finding..."

# Direct dict lookup
meta = EFFECT_METADATA[FinancialEffect.PARTICIPANT_DATA_READ]
```

---

## Declaring Effects in a Manifest

An agent can only invoke effects listed in its `allowed_effects`. Attempting an undeclared effect raises `PermissionError` before the policy engine is consulted.

```python
manifest = AgentManifest(
    agent_id        = "my-agent",
    allowed_effects = [
        FinancialEffect.PARTICIPANT_DATA_READ,          # Tier 1 — ALLOW
        FinancialEffect.RISK_SCORE_COMPUTE,             # Tier 2 — ALLOW
        FinancialEffect.FINDING_DRAFT,                  # Tier 3 — ALLOW
        FinancialEffect.COMPLIANCE_FINDING_EMIT_HIGH,   # Tier 4 — ASK
        FinancialEffect.FINDING_LOG_WRITE,              # Tier 5 — ALLOW
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
        effect=FinancialEffect.PARTICIPANT_COMMUNICATION_SEND,
        payload={"participant_id": pid, "message": msg},
    )
except TollgateDeferred as e:
    # Request is queued — surface this to the caller
    return {"status": "pending_review", "review_id": e.review_id}
```

In eval scenarios, use `expect_effects_asked` to assert that an effect correctly routes to human review without treating `TollgateDeferred` as a failure.

---

## Quick Lookup: All Effects by Value

| Effect Value | Tier | Default |
|-------------|------|---------|
| `participant.data.read` | 1 — Data Access | ALLOW |
| `participant.activity.read` | 1 — Data Access | ALLOW |
| `participant.cohort.read` | 1 — Data Access | ALLOW |
| `plan.data.read` | 1 — Data Access | ALLOW |
| `plan.demographics.read` | 1 — Data Access | ALLOW |
| `fund.performance.read` | 1 — Data Access | ALLOW |
| `fund.fees.read` | 1 — Data Access | ALLOW |
| `employer.feed.read` | 1 — Data Access | ALLOW |
| `market.data.read` | 1 — Data Access | ALLOW |
| `knowledge.base.retrieve` | 1 — Data Access | ALLOW |
| `risk.score.compute` | 2 — Computation | ALLOW |
| `scenario.model.execute` | 2 — Computation | ALLOW |
| `compliance.evaluate` | 2 — Computation | ALLOW |
| `life.event.score` | 2 — Computation | ALLOW |
| `intervention.draft` | 3 — Draft | ALLOW |
| `outreach.draft` | 3 — Draft | ALLOW |
| `finding.draft` | 3 — Draft | ALLOW |
| `recommendation.draft` | 3 — Draft | ALLOW |
| `participant.communication.send` | 4 — Output | ASK |
| `compliance.finding.emit.low` | 4 — Output | ALLOW |
| `compliance.finding.emit.high` | 4 — Output | ASK |
| `recommendation.deliver` | 4 — Output | ALLOW |
| `advisor.escalation.trigger` | 4 — Output | ASK |
| `human.review.queue.add` | 4 — Output | ALLOW |
| `bedrock.agent.invoke` | 4 — Output | ASK |
| `audit.log.write` | 5 — Persistence | ALLOW |
| `intervention.log.write` | 5 — Persistence | ALLOW |
| `finding.log.write` | 5 — Persistence | ALLOW |
| `outcome.log.write` | 5 — Persistence | ALLOW |
| `followup.schedule` | 5 — Persistence | ALLOW |
| `agent.suspend` | 6 — System Control | ASK |
| `agent.promote` | 6 — System Control | DENY |
| `policy.rule.modify` | 6 — System Control | DENY |
| `participant.data.write` | 6 — Hard Deny | DENY |
| `plan.data.write` | 6 — Hard Deny | DENY |
| `account.transaction.execute` | 6 — Hard Deny | DENY |

---

*Agent Foundry · Effects Reference · v0.1.0 · March 2026*
