"""
Compliance Effect Taxonomy.

Covers agents operating in regulated plan/compliance contexts: ERISA, DOL, IRS,
SR 11-7 model risk management. Used by compliance review agents, audit automation,
and fiduciary monitoring agents.

Taxonomy tiers:
  Tier 1 — Data Access      (read-only; broadly safe)
  Tier 2 — Computation      (internal analysis; no external impact)
  Tier 3 — Draft/Internal   (content prepared but not yet sent/submitted)
  Tier 4 — Output/External  (leaves the system — highest scrutiny)
  Tier 5 — Persistence      (log/record writes)
  Tier 6 — System Control   (platform-level; hard denies)

Hard denies cover irreversible or legally-restricted actions:
  - Submitting regulatory filings (must be signed by plan administrator)
  - Executing plan amendments (legal sign-off required)
  - Plan termination (irreversible; requires DOL/IRS approval)
  - Prohibited transactions under ERISA §406

Usage:
    from arc.core.effects import ComplianceEffect, COMPLIANCE_EFFECT_METADATA

    manifest = AgentManifest(
        agent_id="compliance-monitor",
        allowed_effects=[
            ComplianceEffect.REGULATION_READ,
            ComplianceEffect.COMPLIANCE_GAP_IDENTIFY,
            ComplianceEffect.COMPLIANCE_REPORT_DRAFT,
            ComplianceEffect.COMPLIANCE_ALERT_SEND,
        ],
        ...
    )
"""

from enum import Enum

from tollgate.types import Effect as _BaseEffect

from .base import DefaultDecision, EffectMeta, EffectTier


class ComplianceEffect(str, Enum):
    """
    Complete effect taxonomy for ERISA/DOL/IRS compliance agents.

    Values are used as ``resource_type`` in Tollgate's ToolRequest,
    enabling fine-grained YAML policy rules per effect.
    Covers plan document review, nondiscrimination testing, RMD calculations,
    participant notices, and regulatory filing preparation.
    """

    # ─── TIER 1: Data Access ──────────────────────────────────────────────
    REGULATION_READ             = "regulation.read"
    PLAN_DOCUMENT_READ          = "plan.document.read"
    PARTICIPANT_ELIGIBILITY_READ = "participant.eligibility.read"
    CONTRIBUTION_RECORD_READ    = "contribution.record.read"
    VESTING_SCHEDULE_READ       = "vesting.schedule.read"
    PLAN_TESTING_DATA_READ      = "plan.testing.data.read"
    AUDIT_REPORT_READ           = "audit.report.read"
    RMD_RECORD_READ             = "rmd.record.read"

    # ─── TIER 2: Computation ──────────────────────────────────────────────
    COMPLIANCE_GAP_IDENTIFY     = "compliance.gap.identify"
    HCE_TEST_RUN                = "hce.test.run"
    ADP_ACP_TEST_RUN            = "adp.acp.test.run"
    RMD_CALCULATE               = "rmd.calculate"
    VESTING_CALCULATE           = "vesting.calculate"
    DEADLINE_CALCULATE          = "deadline.calculate"
    PLAN_LIMIT_CHECK            = "plan.limit.check"
    FIDELITY_BOND_CHECK         = "fidelity.bond.check"

    # ─── TIER 3: Draft / Internal ─────────────────────────────────────────
    COMPLIANCE_REPORT_DRAFT     = "compliance.report.draft"
    PARTICIPANT_NOTICE_DRAFT    = "participant.notice.draft"
    PLAN_AMENDMENT_DRAFT        = "plan.amendment.draft"
    IRS_FILING_DRAFT            = "irs.filing.draft"
    DOL_FILING_DRAFT            = "dol.filing.draft"

    # ─── TIER 4: Output / External ────────────────────────────────────────
    COMPLIANCE_ALERT_SEND       = "compliance.alert.send"
    PARTICIPANT_NOTICE_SEND     = "participant.notice.send"
    PLAN_SPONSOR_NOTIFY         = "plan.sponsor.notify"
    EXTERNAL_COUNSEL_NOTIFY     = "external.counsel.notify"
    HUMAN_REVIEW_QUEUE_ADD      = "compliance.human.review.queue.add"

    # ─── TIER 5: Persistence ──────────────────────────────────────────────
    COMPLIANCE_AUDIT_LOG_WRITE  = "compliance.audit.log.write"
    PLAN_INTERACTION_LOG_WRITE  = "plan.interaction.log.write"
    TEST_RESULT_SAVE            = "test.result.save"
    DEADLINE_RECORD             = "deadline.record"

    # ─── TIER 6: System Control ───────────────────────────────────────────
    AGENT_SUSPEND               = "compliance.agent.suspend"
    POLICY_RULE_MODIFY          = "compliance.policy.rule.modify"

    # ─── Hard Denies ──────────────────────────────────────────────────────
    REGULATORY_FILING_SUBMIT    = "regulatory.filing.submit"
    PLAN_AMENDMENT_EXECUTE      = "plan.amendment.execute"
    PLAN_TERMINATION_EXECUTE    = "plan.termination.execute"
    PROHIBITED_TRANSACTION_EXECUTE = "prohibited.transaction.execute"


# ── Effect metadata registry ───────────────────────────────────────────────────

COMPLIANCE_EFFECT_METADATA: dict[ComplianceEffect, EffectMeta] = {

    # ── Tier 1: Data Access (ALLOW) ──────────────────────────────────────

    ComplianceEffect.REGULATION_READ: EffectMeta(
        effect=ComplianceEffect.REGULATION_READ,
        tier=EffectTier.DATA_ACCESS,
        base_effect=_BaseEffect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Read ERISA/DOL/IRS regulations and guidance documents.",
        requires_human_review=False,
    ),
    ComplianceEffect.PLAN_DOCUMENT_READ: EffectMeta(
        effect=ComplianceEffect.PLAN_DOCUMENT_READ,
        tier=EffectTier.DATA_ACCESS,
        base_effect=_BaseEffect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Read Summary Plan Description (SPD), plan amendments, and adoption agreements.",
        requires_human_review=False,
    ),
    ComplianceEffect.PARTICIPANT_ELIGIBILITY_READ: EffectMeta(
        effect=ComplianceEffect.PARTICIPANT_ELIGIBILITY_READ,
        tier=EffectTier.DATA_ACCESS,
        base_effect=_BaseEffect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Read participant eligibility data for compliance analysis.",
        requires_human_review=False,
    ),
    ComplianceEffect.CONTRIBUTION_RECORD_READ: EffectMeta(
        effect=ComplianceEffect.CONTRIBUTION_RECORD_READ,
        tier=EffectTier.DATA_ACCESS,
        base_effect=_BaseEffect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Read participant and employer contribution history records.",
        requires_human_review=False,
    ),
    ComplianceEffect.VESTING_SCHEDULE_READ: EffectMeta(
        effect=ComplianceEffect.VESTING_SCHEDULE_READ,
        tier=EffectTier.DATA_ACCESS,
        base_effect=_BaseEffect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Read vesting schedules and service credit records.",
        requires_human_review=False,
    ),
    ComplianceEffect.PLAN_TESTING_DATA_READ: EffectMeta(
        effect=ComplianceEffect.PLAN_TESTING_DATA_READ,
        tier=EffectTier.DATA_ACCESS,
        base_effect=_BaseEffect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Read HCE/ADP/ACP nondiscrimination test input data.",
        requires_human_review=False,
    ),
    ComplianceEffect.AUDIT_REPORT_READ: EffectMeta(
        effect=ComplianceEffect.AUDIT_REPORT_READ,
        tier=EffectTier.DATA_ACCESS,
        base_effect=_BaseEffect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Read prior compliance audit reports and findings.",
        requires_human_review=False,
    ),
    ComplianceEffect.RMD_RECORD_READ: EffectMeta(
        effect=ComplianceEffect.RMD_RECORD_READ,
        tier=EffectTier.DATA_ACCESS,
        base_effect=_BaseEffect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Read Required Minimum Distribution calculation history and records.",
        requires_human_review=False,
    ),

    # ── Tier 2: Computation (ALLOW) ──────────────────────────────────────

    ComplianceEffect.COMPLIANCE_GAP_IDENTIFY: EffectMeta(
        effect=ComplianceEffect.COMPLIANCE_GAP_IDENTIFY,
        tier=EffectTier.COMPUTATION,
        base_effect=_BaseEffect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Identify compliance gaps against ERISA/DOL requirements (internal analysis only).",
        requires_human_review=False,
    ),
    ComplianceEffect.HCE_TEST_RUN: EffectMeta(
        effect=ComplianceEffect.HCE_TEST_RUN,
        tier=EffectTier.COMPUTATION,
        base_effect=_BaseEffect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Run Highly Compensated Employee (HCE) determination per IRC §414(q).",
        requires_human_review=False,
    ),
    ComplianceEffect.ADP_ACP_TEST_RUN: EffectMeta(
        effect=ComplianceEffect.ADP_ACP_TEST_RUN,
        tier=EffectTier.COMPUTATION,
        base_effect=_BaseEffect.READ,
        default_decision=DefaultDecision.ALLOW,
        description=(
            "Run ADP (Actual Deferral Percentage) and ACP (Actual Contribution Percentage) "
            "nondiscrimination tests per IRC §401(k) and §401(m)."
        ),
        requires_human_review=False,
    ),
    ComplianceEffect.RMD_CALCULATE: EffectMeta(
        effect=ComplianceEffect.RMD_CALCULATE,
        tier=EffectTier.COMPUTATION,
        base_effect=_BaseEffect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Calculate Required Minimum Distributions per IRC §401(a)(9) and SECURE Act rules.",
        requires_human_review=False,
    ),
    ComplianceEffect.VESTING_CALCULATE: EffectMeta(
        effect=ComplianceEffect.VESTING_CALCULATE,
        tier=EffectTier.COMPUTATION,
        base_effect=_BaseEffect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Compute participant vesting percentage based on service credits and plan schedule.",
        requires_human_review=False,
    ),
    ComplianceEffect.DEADLINE_CALCULATE: EffectMeta(
        effect=ComplianceEffect.DEADLINE_CALCULATE,
        tier=EffectTier.COMPUTATION,
        base_effect=_BaseEffect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Calculate ERISA/DOL/IRS filing and notice deadlines for the plan year.",
        requires_human_review=False,
    ),
    ComplianceEffect.PLAN_LIMIT_CHECK: EffectMeta(
        effect=ComplianceEffect.PLAN_LIMIT_CHECK,
        tier=EffectTier.COMPUTATION,
        base_effect=_BaseEffect.READ,
        default_decision=DefaultDecision.ALLOW,
        description=(
            "Check IRC §415 annual additions limit and §402(g) elective deferral limit "
            "against participant contribution records."
        ),
        requires_human_review=False,
    ),
    ComplianceEffect.FIDELITY_BOND_CHECK: EffectMeta(
        effect=ComplianceEffect.FIDELITY_BOND_CHECK,
        tier=EffectTier.COMPUTATION,
        base_effect=_BaseEffect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Verify ERISA §412 fidelity bond coverage adequacy against plan assets handled.",
        requires_human_review=False,
    ),

    # ── Tier 3: Draft / Internal (ALLOW) ─────────────────────────────────

    ComplianceEffect.COMPLIANCE_REPORT_DRAFT: EffectMeta(
        effect=ComplianceEffect.COMPLIANCE_REPORT_DRAFT,
        tier=EffectTier.DRAFT,
        base_effect=_BaseEffect.WRITE,
        default_decision=DefaultDecision.ALLOW,
        description="Draft compliance gap report identifying ERISA/DOL deficiencies. Not yet sent.",
        requires_human_review=False,
    ),
    ComplianceEffect.PARTICIPANT_NOTICE_DRAFT: EffectMeta(
        effect=ComplianceEffect.PARTICIPANT_NOTICE_DRAFT,
        tier=EffectTier.DRAFT,
        base_effect=_BaseEffect.WRITE,
        default_decision=DefaultDecision.ALLOW,
        description=(
            "Draft required participant notice (404(c), blackout notice, QDIA notice, etc.). "
            "Not yet distributed."
        ),
        requires_human_review=False,
    ),
    ComplianceEffect.PLAN_AMENDMENT_DRAFT: EffectMeta(
        effect=ComplianceEffect.PLAN_AMENDMENT_DRAFT,
        tier=EffectTier.DRAFT,
        base_effect=_BaseEffect.WRITE,
        default_decision=DefaultDecision.ALLOW,
        description=(
            "Draft plan amendment language for legal review. "
            "Amendment is NOT executed — requires legal sign-off before PLAN_AMENDMENT_EXECUTE."
        ),
        requires_human_review=False,
    ),
    ComplianceEffect.IRS_FILING_DRAFT: EffectMeta(
        effect=ComplianceEffect.IRS_FILING_DRAFT,
        tier=EffectTier.DRAFT,
        base_effect=_BaseEffect.WRITE,
        default_decision=DefaultDecision.ALLOW,
        description=(
            "Draft Form 5500, Form 8955-SSA, or other IRS filing for plan administrator review. "
            "Not yet submitted."
        ),
        requires_human_review=False,
    ),
    ComplianceEffect.DOL_FILING_DRAFT: EffectMeta(
        effect=ComplianceEffect.DOL_FILING_DRAFT,
        tier=EffectTier.DRAFT,
        base_effect=_BaseEffect.WRITE,
        default_decision=DefaultDecision.ALLOW,
        description=(
            "Draft DOL filing or Voluntary Correction Program (VCP) submission for review. "
            "Not yet submitted."
        ),
        requires_human_review=False,
    ),

    # ── Tier 4: Output / External ─────────────────────────────────────────
    # Key governance: external notices → ASK; internal alerts → ALLOW

    ComplianceEffect.COMPLIANCE_ALERT_SEND: EffectMeta(
        effect=ComplianceEffect.COMPLIANCE_ALERT_SEND,
        tier=EffectTier.OUTPUT,
        base_effect=_BaseEffect.NOTIFY,
        default_decision=DefaultDecision.ALLOW,
        description="Send compliance alert to plan sponsor (internal notification).",
        requires_human_review=False,
    ),
    ComplianceEffect.PARTICIPANT_NOTICE_SEND: EffectMeta(
        effect=ComplianceEffect.PARTICIPANT_NOTICE_SEND,
        tier=EffectTier.OUTPUT,
        base_effect=_BaseEffect.NOTIFY,
        default_decision=DefaultDecision.ASK,
        description=(
            "Send required notice to plan participants (404(c), blackout, QDIA, etc.). "
            "Always requires human approval — legally required notices must be verified by compliance officer."
        ),
        requires_human_review=True,
    ),
    ComplianceEffect.PLAN_SPONSOR_NOTIFY: EffectMeta(
        effect=ComplianceEffect.PLAN_SPONSOR_NOTIFY,
        tier=EffectTier.OUTPUT,
        base_effect=_BaseEffect.NOTIFY,
        default_decision=DefaultDecision.ALLOW,
        description="Notify plan sponsor of a compliance issue or upcoming deadline.",
        requires_human_review=False,
    ),
    ComplianceEffect.EXTERNAL_COUNSEL_NOTIFY: EffectMeta(
        effect=ComplianceEffect.EXTERNAL_COUNSEL_NOTIFY,
        tier=EffectTier.OUTPUT,
        base_effect=_BaseEffect.NOTIFY,
        default_decision=DefaultDecision.ASK,
        description=(
            "Notify external ERISA counsel of compliance finding. "
            "Always requires human approval — may trigger legal privilege or billing obligations."
        ),
        requires_human_review=True,
    ),
    ComplianceEffect.HUMAN_REVIEW_QUEUE_ADD: EffectMeta(
        effect=ComplianceEffect.HUMAN_REVIEW_QUEUE_ADD,
        tier=EffectTier.OUTPUT,
        base_effect=_BaseEffect.NOTIFY,
        default_decision=DefaultDecision.ALLOW,
        description="Add compliance finding or filing draft to compliance officer review queue.",
        requires_human_review=False,
    ),

    # ── Tier 5: Persistence (ALLOW) ──────────────────────────────────────

    ComplianceEffect.COMPLIANCE_AUDIT_LOG_WRITE: EffectMeta(
        effect=ComplianceEffect.COMPLIANCE_AUDIT_LOG_WRITE,
        tier=EffectTier.PERSISTENCE,
        base_effect=_BaseEffect.WRITE,
        default_decision=DefaultDecision.ALLOW,
        description="Write compliance audit entry. Always permitted.",
        requires_human_review=False,
    ),
    ComplianceEffect.PLAN_INTERACTION_LOG_WRITE: EffectMeta(
        effect=ComplianceEffect.PLAN_INTERACTION_LOG_WRITE,
        tier=EffectTier.PERSISTENCE,
        base_effect=_BaseEffect.WRITE,
        default_decision=DefaultDecision.ALLOW,
        description="Log agent interaction against a specific plan for audit and reporting.",
        requires_human_review=False,
    ),
    ComplianceEffect.TEST_RESULT_SAVE: EffectMeta(
        effect=ComplianceEffect.TEST_RESULT_SAVE,
        tier=EffectTier.PERSISTENCE,
        base_effect=_BaseEffect.WRITE,
        default_decision=DefaultDecision.ALLOW,
        description="Persist HCE/ADP/ACP test results for plan year record-keeping.",
        requires_human_review=False,
    ),
    ComplianceEffect.DEADLINE_RECORD: EffectMeta(
        effect=ComplianceEffect.DEADLINE_RECORD,
        tier=EffectTier.PERSISTENCE,
        base_effect=_BaseEffect.WRITE,
        default_decision=DefaultDecision.ALLOW,
        description="Record computed ERISA/DOL/IRS deadline in the compliance calendar.",
        requires_human_review=False,
    ),

    # ── Tier 6: System Control ────────────────────────────────────────────

    ComplianceEffect.AGENT_SUSPEND: EffectMeta(
        effect=ComplianceEffect.AGENT_SUSPEND,
        tier=EffectTier.SYSTEM_CONTROL,
        base_effect=_BaseEffect.WRITE,
        default_decision=DefaultDecision.ASK,
        description="Suspend compliance agent via circuit breaker. Requires operator approval.",
        requires_human_review=True,
    ),
    ComplianceEffect.POLICY_RULE_MODIFY: EffectMeta(
        effect=ComplianceEffect.POLICY_RULE_MODIFY,
        tier=EffectTier.SYSTEM_CONTROL,
        base_effect=_BaseEffect.WRITE,
        default_decision=DefaultDecision.DENY,
        description="Modify a compliance policy rule at runtime. Never permitted from an agent.",
        requires_human_review=True,
    ),

    # ── Hard Denies ───────────────────────────────────────────────────────

    ComplianceEffect.REGULATORY_FILING_SUBMIT: EffectMeta(
        effect=ComplianceEffect.REGULATORY_FILING_SUBMIT,
        tier=EffectTier.SYSTEM_CONTROL,
        base_effect=_BaseEffect.WRITE,
        default_decision=DefaultDecision.DENY,
        description=(
            "Submit Form 5500, PBGC, or DOL filing. Hard blocked — must be signed by "
            "plan administrator. Agent may only draft; human submits."
        ),
        requires_human_review=True,
    ),
    ComplianceEffect.PLAN_AMENDMENT_EXECUTE: EffectMeta(
        effect=ComplianceEffect.PLAN_AMENDMENT_EXECUTE,
        tier=EffectTier.SYSTEM_CONTROL,
        base_effect=_BaseEffect.WRITE,
        default_decision=DefaultDecision.DENY,
        description=(
            "Execute a plan amendment. Hard blocked — legal sign-off and plan sponsor "
            "authorization required. Agent may only draft the amendment language."
        ),
        requires_human_review=True,
    ),
    ComplianceEffect.PLAN_TERMINATION_EXECUTE: EffectMeta(
        effect=ComplianceEffect.PLAN_TERMINATION_EXECUTE,
        tier=EffectTier.SYSTEM_CONTROL,
        base_effect=_BaseEffect.WRITE,
        default_decision=DefaultDecision.DENY,
        description=(
            "Execute plan termination. Hard blocked — irreversible, requires DOL/IRS "
            "coordination and plan sponsor board resolution."
        ),
        requires_human_review=True,
    ),
    ComplianceEffect.PROHIBITED_TRANSACTION_EXECUTE: EffectMeta(
        effect=ComplianceEffect.PROHIBITED_TRANSACTION_EXECUTE,
        tier=EffectTier.SYSTEM_CONTROL,
        base_effect=_BaseEffect.WRITE,
        default_decision=DefaultDecision.DENY,
        description=(
            "Execute any ERISA §406 prohibited transaction between the plan and a "
            "disqualified person. Hard blocked — strict liability under ERISA."
        ),
        requires_human_review=True,
    ),
}
