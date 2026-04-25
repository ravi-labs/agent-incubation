"""
Financial Services Effect Taxonomy.

Effects are the atomic actions an agent can take. Every agent tool call must
declare an effect, which determines the default ALLOW/ASK/DENY decision and
how it is represented in the audit trail.

Six tiers (defined in arc.core.effects.base.EffectTier):
  Tier 1 — Data Access     (read-only, broadly safe)
  Tier 2 — Computation     (internal processing, no external impact)
  Tier 3 — Draft/Internal  (content created but not yet delivered)
  Tier 4 — Output/External (leaves the system — highest scrutiny)
  Tier 5 — Persistence     (log/record writes)
  Tier 6 — System Control  (platform-level operations)

Each effect maps to a base Tollgate Effect (READ/WRITE/NOTIFY/DELETE) so it
is fully compatible with Tollgate's ControlTower and YAML policy engine.
"""

from enum import Enum

from foundry.tollgate.types import Effect

from .base import DefaultDecision, EffectMeta, EffectTier


class FinancialEffect(str, Enum):
    """
    Complete effect taxonomy for financial services agents.

    Values are used as ``resource_type`` in Tollgate's ToolRequest, enabling
    fine-grained YAML policy rules per effect.
    """

    # ─── TIER 1: Data Access ──────────────────────────────────────────────
    PARTICIPANT_DATA_READ       = "participant.data.read"
    PARTICIPANT_ACTIVITY_READ   = "participant.activity.read"
    PARTICIPANT_COHORT_READ     = "participant.cohort.read"
    PLAN_DATA_READ              = "plan.data.read"
    PLAN_DEMOGRAPHICS_READ      = "plan.demographics.read"
    FUND_PERFORMANCE_READ       = "fund.performance.read"
    FUND_FEES_READ              = "fund.fees.read"
    EMPLOYER_FEED_READ          = "employer.feed.read"
    MARKET_DATA_READ            = "market.data.read"
    KNOWLEDGE_BASE_RETRIEVE     = "knowledge.base.retrieve"   # Bedrock KB RAG retrieval

    # ─── TIER 2: Computation ──────────────────────────────────────────────
    RISK_SCORE_COMPUTE          = "risk.score.compute"
    SCENARIO_MODEL_EXECUTE      = "scenario.model.execute"
    COMPLIANCE_EVALUATE         = "compliance.evaluate"
    LIFE_EVENT_SCORE            = "life.event.score"

    # ─── TIER 3: Draft / Internal ─────────────────────────────────────────
    INTERVENTION_DRAFT          = "intervention.draft"
    OUTREACH_DRAFT              = "outreach.draft"
    FINDING_DRAFT               = "finding.draft"
    RECOMMENDATION_DRAFT        = "recommendation.draft"

    # ─── TIER 4: Output / External ────────────────────────────────────────
    PARTICIPANT_COMMUNICATION_SEND  = "participant.communication.send"
    COMPLIANCE_FINDING_EMIT_LOW     = "compliance.finding.emit.low"
    COMPLIANCE_FINDING_EMIT_HIGH    = "compliance.finding.emit.high"
    RECOMMENDATION_DELIVER          = "recommendation.deliver"
    ADVISOR_ESCALATION_TRIGGER      = "advisor.escalation.trigger"
    HUMAN_REVIEW_QUEUE_ADD          = "human.review.queue.add"
    BEDROCK_AGENT_INVOKE            = "bedrock.agent.invoke"     # Delegate to another Bedrock Agent

    # ─── TIER 5: Persistence ──────────────────────────────────────────────
    AUDIT_LOG_WRITE             = "audit.log.write"
    INTERVENTION_LOG_WRITE      = "intervention.log.write"
    FINDING_LOG_WRITE           = "finding.log.write"
    OUTCOME_LOG_WRITE           = "outcome.log.write"
    FOLLOWUP_SCHEDULE           = "followup.schedule"

    # ─── TIER 6: System Control ───────────────────────────────────────────
    AGENT_SUSPEND               = "agent.suspend"
    AGENT_PROMOTE               = "agent.promote"
    POLICY_RULE_MODIFY          = "policy.rule.modify"

    # ─── Hard Denies (financial) ──────────────────────────────────────────
    PARTICIPANT_DATA_WRITE      = "participant.data.write"
    PLAN_DATA_WRITE             = "plan.data.write"
    ACCOUNT_TRANSACTION_EXECUTE = "account.transaction.execute"


# ─── Effect metadata registry ─────────────────────────────────────────────────
EFFECT_METADATA: dict[FinancialEffect, EffectMeta] = {

    # Tier 1 — Data Access (ALLOW within declared scope)
    FinancialEffect.PARTICIPANT_DATA_READ: EffectMeta(
        effect=FinancialEffect.PARTICIPANT_DATA_READ,
        tier=EffectTier.DATA_ACCESS,
        base_effect=Effect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Read participant PII, account balance, and contribution history.",
        requires_human_review=False,
    ),
    FinancialEffect.PARTICIPANT_ACTIVITY_READ: EffectMeta(
        effect=FinancialEffect.PARTICIPANT_ACTIVITY_READ,
        tier=EffectTier.DATA_ACCESS,
        base_effect=Effect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Read participant behavioral signals: login frequency, allocation changes.",
        requires_human_review=False,
    ),
    FinancialEffect.PARTICIPANT_COHORT_READ: EffectMeta(
        effect=FinancialEffect.PARTICIPANT_COHORT_READ,
        tier=EffectTier.DATA_ACCESS,
        base_effect=Effect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Read aggregated/anonymized peer cohort data. No individual PII.",
        requires_human_review=False,
    ),
    FinancialEffect.PLAN_DATA_READ: EffectMeta(
        effect=FinancialEffect.PLAN_DATA_READ,
        tier=EffectTier.DATA_ACCESS,
        base_effect=Effect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Read plan configuration and design parameters.",
        requires_human_review=False,
    ),
    FinancialEffect.PLAN_DEMOGRAPHICS_READ: EffectMeta(
        effect=FinancialEffect.PLAN_DEMOGRAPHICS_READ,
        tier=EffectTier.DATA_ACCESS,
        base_effect=Effect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Read workforce demographic distributions at plan level (not individual).",
        requires_human_review=False,
    ),
    FinancialEffect.FUND_PERFORMANCE_READ: EffectMeta(
        effect=FinancialEffect.FUND_PERFORMANCE_READ,
        tier=EffectTier.DATA_ACCESS,
        base_effect=Effect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Read fund returns and benchmark performance data.",
        requires_human_review=False,
    ),
    FinancialEffect.FUND_FEES_READ: EffectMeta(
        effect=FinancialEffect.FUND_FEES_READ,
        tier=EffectTier.DATA_ACCESS,
        base_effect=Effect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Read fund expense ratios and fee structure data.",
        requires_human_review=False,
    ),
    FinancialEffect.EMPLOYER_FEED_READ: EffectMeta(
        effect=FinancialEffect.EMPLOYER_FEED_READ,
        tier=EffectTier.DATA_ACCESS,
        base_effect=Effect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Read employer HR/payroll data feeds (terminations, new hires).",
        requires_human_review=False,
    ),
    FinancialEffect.MARKET_DATA_READ: EffectMeta(
        effect=FinancialEffect.MARKET_DATA_READ,
        tier=EffectTier.DATA_ACCESS,
        base_effect=Effect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Read market indices and benchmark data.",
        requires_human_review=False,
    ),
    FinancialEffect.KNOWLEDGE_BASE_RETRIEVE: EffectMeta(
        effect=FinancialEffect.KNOWLEDGE_BASE_RETRIEVE,
        tier=EffectTier.DATA_ACCESS,
        base_effect=Effect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Retrieve relevant passages from an Amazon Bedrock Knowledge Base for RAG.",
        requires_human_review=False,
    ),

    # Tier 2 — Computation (ALLOW, internal)
    FinancialEffect.RISK_SCORE_COMPUTE: EffectMeta(
        effect=FinancialEffect.RISK_SCORE_COMPUTE,
        tier=EffectTier.COMPUTATION,
        base_effect=Effect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Compute retirement risk or trajectory score for a participant.",
        requires_human_review=False,
    ),
    FinancialEffect.SCENARIO_MODEL_EXECUTE: EffectMeta(
        effect=FinancialEffect.SCENARIO_MODEL_EXECUTE,
        tier=EffectTier.COMPUTATION,
        base_effect=Effect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Execute a plan design scenario model via Code Interpreter.",
        requires_human_review=False,
    ),
    FinancialEffect.COMPLIANCE_EVALUATE: EffectMeta(
        effect=FinancialEffect.COMPLIANCE_EVALUATE,
        tier=EffectTier.COMPUTATION,
        base_effect=Effect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Evaluate plan or fund data against a regulatory threshold (ERISA, DOL).",
        requires_human_review=False,
    ),
    FinancialEffect.LIFE_EVENT_SCORE: EffectMeta(
        effect=FinancialEffect.LIFE_EVENT_SCORE,
        tier=EffectTier.COMPUTATION,
        base_effect=Effect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Score the probability that a participant has experienced a life event.",
        requires_human_review=False,
    ),

    # Tier 3 — Draft / Internal (ALLOW, nothing leaves the system)
    FinancialEffect.INTERVENTION_DRAFT: EffectMeta(
        effect=FinancialEffect.INTERVENTION_DRAFT,
        tier=EffectTier.DRAFT,
        base_effect=Effect.WRITE,
        default_decision=DefaultDecision.ALLOW,
        description="Draft a participant intervention message. Not yet sent.",
        requires_human_review=False,
    ),
    FinancialEffect.OUTREACH_DRAFT: EffectMeta(
        effect=FinancialEffect.OUTREACH_DRAFT,
        tier=EffectTier.DRAFT,
        base_effect=Effect.WRITE,
        default_decision=DefaultDecision.ALLOW,
        description="Draft participant outreach content for a life event. Not yet sent.",
        requires_human_review=False,
    ),
    FinancialEffect.FINDING_DRAFT: EffectMeta(
        effect=FinancialEffect.FINDING_DRAFT,
        tier=EffectTier.DRAFT,
        base_effect=Effect.WRITE,
        default_decision=DefaultDecision.ALLOW,
        description="Draft a compliance finding memo. Not yet emitted.",
        requires_human_review=False,
    ),
    FinancialEffect.RECOMMENDATION_DRAFT: EffectMeta(
        effect=FinancialEffect.RECOMMENDATION_DRAFT,
        tier=EffectTier.DRAFT,
        base_effect=Effect.WRITE,
        default_decision=DefaultDecision.ALLOW,
        description="Draft a plan design recommendation. Not yet delivered.",
        requires_human_review=False,
    ),

    # Tier 4 — Output / External (ASK by default — these reach real people)
    FinancialEffect.PARTICIPANT_COMMUNICATION_SEND: EffectMeta(
        effect=FinancialEffect.PARTICIPANT_COMMUNICATION_SEND,
        tier=EffectTier.OUTPUT,
        base_effect=Effect.NOTIFY,
        default_decision=DefaultDecision.ASK,
        description="Send a message to a participant via email, push, or in-app.",
        requires_human_review=True,
    ),
    FinancialEffect.COMPLIANCE_FINDING_EMIT_LOW: EffectMeta(
        effect=FinancialEffect.COMPLIANCE_FINDING_EMIT_LOW,
        tier=EffectTier.OUTPUT,
        base_effect=Effect.NOTIFY,
        default_decision=DefaultDecision.ALLOW,
        description="Emit a low-severity compliance finding to the monitoring dashboard.",
        requires_human_review=False,
    ),
    FinancialEffect.COMPLIANCE_FINDING_EMIT_HIGH: EffectMeta(
        effect=FinancialEffect.COMPLIANCE_FINDING_EMIT_HIGH,
        tier=EffectTier.OUTPUT,
        base_effect=Effect.NOTIFY,
        default_decision=DefaultDecision.ASK,
        description="Emit a high-severity compliance finding. Always requires human review.",
        requires_human_review=True,
    ),
    FinancialEffect.RECOMMENDATION_DELIVER: EffectMeta(
        effect=FinancialEffect.RECOMMENDATION_DELIVER,
        tier=EffectTier.OUTPUT,
        base_effect=Effect.NOTIFY,
        default_decision=DefaultDecision.ALLOW,
        description="Deliver a plan design recommendation to an internal relationship manager.",
        requires_human_review=False,
    ),
    FinancialEffect.ADVISOR_ESCALATION_TRIGGER: EffectMeta(
        effect=FinancialEffect.ADVISOR_ESCALATION_TRIGGER,
        tier=EffectTier.OUTPUT,
        base_effect=Effect.NOTIFY,
        default_decision=DefaultDecision.ASK,
        description="Trigger routing to an advisor or call center.",
        requires_human_review=True,
    ),
    FinancialEffect.HUMAN_REVIEW_QUEUE_ADD: EffectMeta(
        effect=FinancialEffect.HUMAN_REVIEW_QUEUE_ADD,
        tier=EffectTier.OUTPUT,
        base_effect=Effect.NOTIFY,
        default_decision=DefaultDecision.ALLOW,
        description="Add an item to the human compliance review queue.",
        requires_human_review=False,
    ),
    FinancialEffect.BEDROCK_AGENT_INVOKE: EffectMeta(
        effect=FinancialEffect.BEDROCK_AGENT_INVOKE,
        tier=EffectTier.OUTPUT,
        base_effect=Effect.WRITE,
        default_decision=DefaultDecision.ASK,
        description=(
            "Delegate a task to another Amazon Bedrock Agent via invoke_agent. "
            "The delegate agent may take further external actions; ASK by default."
        ),
        requires_human_review=False,
    ),

    # Tier 5 — Persistence (ALLOW, logging is always permitted and required)
    FinancialEffect.AUDIT_LOG_WRITE: EffectMeta(
        effect=FinancialEffect.AUDIT_LOG_WRITE,
        tier=EffectTier.PERSISTENCE,
        base_effect=Effect.WRITE,
        default_decision=DefaultDecision.ALLOW,
        description="Write a cryptographic audit entry. Always permitted, always required.",
        requires_human_review=False,
    ),
    FinancialEffect.INTERVENTION_LOG_WRITE: EffectMeta(
        effect=FinancialEffect.INTERVENTION_LOG_WRITE,
        tier=EffectTier.PERSISTENCE,
        base_effect=Effect.WRITE,
        default_decision=DefaultDecision.ALLOW,
        description="Log that an intervention was generated and/or delivered.",
        requires_human_review=False,
    ),
    FinancialEffect.FINDING_LOG_WRITE: EffectMeta(
        effect=FinancialEffect.FINDING_LOG_WRITE,
        tier=EffectTier.PERSISTENCE,
        base_effect=Effect.WRITE,
        default_decision=DefaultDecision.ALLOW,
        description="Log a compliance finding with full context for audit purposes.",
        requires_human_review=False,
    ),
    FinancialEffect.OUTCOME_LOG_WRITE: EffectMeta(
        effect=FinancialEffect.OUTCOME_LOG_WRITE,
        tier=EffectTier.PERSISTENCE,
        base_effect=Effect.WRITE,
        default_decision=DefaultDecision.ALLOW,
        description="Log an outcome for ROI tracking (did participant act? was finding resolved?).",
        requires_human_review=False,
    ),
    FinancialEffect.FOLLOWUP_SCHEDULE: EffectMeta(
        effect=FinancialEffect.FOLLOWUP_SCHEDULE,
        tier=EffectTier.PERSISTENCE,
        base_effect=Effect.WRITE,
        default_decision=DefaultDecision.ALLOW,
        description="Schedule a follow-up action in the tracker pipeline.",
        requires_human_review=False,
    ),

    # Tier 6 — System Control (restricted)
    FinancialEffect.AGENT_SUSPEND: EffectMeta(
        effect=FinancialEffect.AGENT_SUSPEND,
        tier=EffectTier.SYSTEM_CONTROL,
        base_effect=Effect.WRITE,
        default_decision=DefaultDecision.ASK,
        description="Suspend an agent via circuit breaker. Requires operator approval.",
        requires_human_review=True,
    ),
    FinancialEffect.AGENT_PROMOTE: EffectMeta(
        effect=FinancialEffect.AGENT_PROMOTE,
        tier=EffectTier.SYSTEM_CONTROL,
        base_effect=Effect.WRITE,
        default_decision=DefaultDecision.DENY,
        description="Promote agent from sandbox to production. Lifecycle manager only.",
        requires_human_review=True,
    ),
    FinancialEffect.POLICY_RULE_MODIFY: EffectMeta(
        effect=FinancialEffect.POLICY_RULE_MODIFY,
        tier=EffectTier.SYSTEM_CONTROL,
        base_effect=Effect.WRITE,
        default_decision=DefaultDecision.DENY,
        description="Modify a policy rule at runtime. Never permitted from an agent.",
        requires_human_review=True,
    ),

    # Hard Denies — financial operations agents must never perform
    FinancialEffect.PARTICIPANT_DATA_WRITE: EffectMeta(
        effect=FinancialEffect.PARTICIPANT_DATA_WRITE,
        tier=EffectTier.SYSTEM_CONTROL,
        base_effect=Effect.WRITE,
        default_decision=DefaultDecision.DENY,
        description="Modify participant records directly. Hard blocked for all agents.",
        requires_human_review=True,
    ),
    FinancialEffect.PLAN_DATA_WRITE: EffectMeta(
        effect=FinancialEffect.PLAN_DATA_WRITE,
        tier=EffectTier.SYSTEM_CONTROL,
        base_effect=Effect.WRITE,
        default_decision=DefaultDecision.DENY,
        description="Modify plan configuration directly. Hard blocked for all agents.",
        requires_human_review=True,
    ),
    FinancialEffect.ACCOUNT_TRANSACTION_EXECUTE: EffectMeta(
        effect=FinancialEffect.ACCOUNT_TRANSACTION_EXECUTE,
        tier=EffectTier.SYSTEM_CONTROL,
        base_effect=Effect.WRITE,
        default_decision=DefaultDecision.DENY,
        description="Execute a financial transaction. Hard blocked. No agent ever does this.",
        requires_human_review=True,
    ),
}


def effects_by_tier(tier: EffectTier) -> list[FinancialEffect]:
    """Return all FinancialEffect values in a given tier."""
    return [e for e, m in EFFECT_METADATA.items() if m.tier == tier]


def effects_requiring_review() -> list[FinancialEffect]:
    """Return all FinancialEffect values that require human review by default."""
    return [e for e, m in EFFECT_METADATA.items() if m.requires_human_review]
