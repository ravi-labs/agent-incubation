"""
Healthcare Effect Taxonomy.

Covers HIPAA-regulated environments: clinical decision support, care
coordination, prior authorisation, claims processing, and population
health management agents.

Taxonomy tiers mirror the FinancialEffect model:
  Tier 1 — Data Access     (read-only, broadly safe)
  Tier 2 — Computation     (internal processing, no external impact)
  Tier 3 — Draft/Internal  (content created but not yet delivered)
  Tier 4 — Output/External (leaves the system — highest scrutiny)
  Tier 5 — Persistence     (log/record writes)
  Tier 6 — System Control  (platform-level, hard denies)

Usage:
    from arc.core.effects import HealthcareEffect, HEALTHCARE_EFFECT_METADATA

    manifest = AgentManifest(
        agent_id="care-coordinator",
        allowed_effects=[
            HealthcareEffect.PATIENT_RECORD_READ,
            HealthcareEffect.CLINICAL_SUMMARY_DRAFT,
            HealthcareEffect.CARE_GAP_ALERT_SEND,
        ],
        ...
    )
"""

from enum import Enum

from tollgate.types import Effect as _BaseEffect

from .base import DefaultDecision, EffectMeta, EffectTier


class HealthcareEffect(str, Enum):
    """
    Complete effect taxonomy for healthcare agents.

    Designed for HIPAA-covered entities and business associates.
    Values are used as ``resource_type`` in Tollgate's ToolRequest.
    """

    # ─── TIER 1: Data Access ──────────────────────────────────────────────
    PATIENT_RECORD_READ         = "patient.record.read"
    PATIENT_DEMOGRAPHICS_READ   = "patient.demographics.read"
    PATIENT_CLAIMS_READ         = "patient.claims.read"
    PATIENT_LAB_RESULTS_READ    = "patient.lab.results.read"
    PATIENT_MEDICATIONS_READ    = "patient.medications.read"
    PATIENT_VITALS_READ         = "patient.vitals.read"
    PROVIDER_DIRECTORY_READ     = "provider.directory.read"
    FORMULARY_READ              = "formulary.read"
    CLINICAL_GUIDELINES_READ    = "clinical.guidelines.read"
    KNOWLEDGE_BASE_RETRIEVE     = "knowledge.base.retrieve"

    # ─── TIER 2: Computation ──────────────────────────────────────────────
    RISK_STRATIFICATION_COMPUTE = "risk.stratification.compute"
    CARE_GAP_IDENTIFY           = "care.gap.identify"
    PRIOR_AUTH_EVALUATE         = "prior.auth.evaluate"
    READMISSION_RISK_SCORE      = "readmission.risk.score"
    DRUG_INTERACTION_CHECK      = "drug.interaction.check"

    # ─── TIER 3: Draft / Internal ─────────────────────────────────────────
    CLINICAL_SUMMARY_DRAFT      = "clinical.summary.draft"
    CARE_PLAN_DRAFT             = "care.plan.draft"
    PRIOR_AUTH_REQUEST_DRAFT    = "prior.auth.request.draft"
    REFERRAL_DRAFT              = "referral.draft"

    # ─── TIER 4: Output / External ────────────────────────────────────────
    CARE_GAP_ALERT_SEND         = "care.gap.alert.send"
    PRIOR_AUTH_SUBMIT           = "prior.auth.submit"
    REFERRAL_SEND               = "referral.send"
    PROVIDER_NOTIFICATION_SEND  = "provider.notification.send"
    POPULATION_REPORT_EMIT      = "population.report.emit"
    HUMAN_REVIEW_QUEUE_ADD      = "human.review.queue.add"

    # ─── TIER 5: Persistence ──────────────────────────────────────────────
    AUDIT_LOG_WRITE             = "audit.log.write"
    CARE_INTERACTION_LOG_WRITE  = "care.interaction.log.write"
    OUTCOME_LOG_WRITE           = "outcome.log.write"
    FOLLOWUP_SCHEDULE           = "followup.schedule"

    # ─── TIER 6: System Control ───────────────────────────────────────────
    AGENT_SUSPEND               = "agent.suspend"
    AGENT_PROMOTE               = "agent.promote"
    POLICY_RULE_MODIFY          = "policy.rule.modify"

    # ─── Hard Denies (HIPAA) ──────────────────────────────────────────────
    PATIENT_RECORD_WRITE        = "patient.record.write"
    PATIENT_RECORD_DELETE       = "patient.record.delete"
    CLINICAL_ORDER_EXECUTE      = "clinical.order.execute"


# ── Effect metadata registry ───────────────────────────────────────────────────

HEALTHCARE_EFFECT_METADATA: dict[HealthcareEffect, EffectMeta] = {

    # Tier 1 — Data Access (ALLOW)
    HealthcareEffect.PATIENT_RECORD_READ: EffectMeta(
        effect=HealthcareEffect.PATIENT_RECORD_READ,
        tier=EffectTier.DATA_ACCESS,
        base_effect=_BaseEffect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Read patient clinical record including diagnoses, conditions, and history.",
        requires_human_review=False,
    ),
    HealthcareEffect.PATIENT_DEMOGRAPHICS_READ: EffectMeta(
        effect=HealthcareEffect.PATIENT_DEMOGRAPHICS_READ,
        tier=EffectTier.DATA_ACCESS,
        base_effect=_BaseEffect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Read patient demographic and contact information.",
        requires_human_review=False,
    ),
    HealthcareEffect.PATIENT_CLAIMS_READ: EffectMeta(
        effect=HealthcareEffect.PATIENT_CLAIMS_READ,
        tier=EffectTier.DATA_ACCESS,
        base_effect=_BaseEffect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Read patient insurance claims and adjudication history.",
        requires_human_review=False,
    ),
    HealthcareEffect.PATIENT_LAB_RESULTS_READ: EffectMeta(
        effect=HealthcareEffect.PATIENT_LAB_RESULTS_READ,
        tier=EffectTier.DATA_ACCESS,
        base_effect=_BaseEffect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Read laboratory test results and diagnostic reports.",
        requires_human_review=False,
    ),
    HealthcareEffect.PATIENT_MEDICATIONS_READ: EffectMeta(
        effect=HealthcareEffect.PATIENT_MEDICATIONS_READ,
        tier=EffectTier.DATA_ACCESS,
        base_effect=_BaseEffect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Read current and historical medication lists.",
        requires_human_review=False,
    ),
    HealthcareEffect.PATIENT_VITALS_READ: EffectMeta(
        effect=HealthcareEffect.PATIENT_VITALS_READ,
        tier=EffectTier.DATA_ACCESS,
        base_effect=_BaseEffect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Read patient vital signs and biometric measurements.",
        requires_human_review=False,
    ),
    HealthcareEffect.PROVIDER_DIRECTORY_READ: EffectMeta(
        effect=HealthcareEffect.PROVIDER_DIRECTORY_READ,
        tier=EffectTier.DATA_ACCESS,
        base_effect=_BaseEffect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Read provider network directory and speciality information.",
        requires_human_review=False,
    ),
    HealthcareEffect.FORMULARY_READ: EffectMeta(
        effect=HealthcareEffect.FORMULARY_READ,
        tier=EffectTier.DATA_ACCESS,
        base_effect=_BaseEffect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Read drug formulary, tier status, and coverage rules.",
        requires_human_review=False,
    ),
    HealthcareEffect.CLINICAL_GUIDELINES_READ: EffectMeta(
        effect=HealthcareEffect.CLINICAL_GUIDELINES_READ,
        tier=EffectTier.DATA_ACCESS,
        base_effect=_BaseEffect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Read clinical practice guidelines and evidence-based protocols.",
        requires_human_review=False,
    ),
    HealthcareEffect.KNOWLEDGE_BASE_RETRIEVE: EffectMeta(
        effect=HealthcareEffect.KNOWLEDGE_BASE_RETRIEVE,
        tier=EffectTier.DATA_ACCESS,
        base_effect=_BaseEffect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Retrieve passages from a clinical knowledge base via RAG.",
        requires_human_review=False,
    ),

    # Tier 2 — Computation (ALLOW)
    HealthcareEffect.RISK_STRATIFICATION_COMPUTE: EffectMeta(
        effect=HealthcareEffect.RISK_STRATIFICATION_COMPUTE,
        tier=EffectTier.COMPUTATION,
        base_effect=_BaseEffect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Compute patient risk stratification score for care management prioritisation.",
        requires_human_review=False,
    ),
    HealthcareEffect.CARE_GAP_IDENTIFY: EffectMeta(
        effect=HealthcareEffect.CARE_GAP_IDENTIFY,
        tier=EffectTier.COMPUTATION,
        base_effect=_BaseEffect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Identify gaps in preventive care against quality measure specifications.",
        requires_human_review=False,
    ),
    HealthcareEffect.PRIOR_AUTH_EVALUATE: EffectMeta(
        effect=HealthcareEffect.PRIOR_AUTH_EVALUATE,
        tier=EffectTier.COMPUTATION,
        base_effect=_BaseEffect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Evaluate prior authorisation criteria against clinical evidence.",
        requires_human_review=False,
    ),
    HealthcareEffect.READMISSION_RISK_SCORE: EffectMeta(
        effect=HealthcareEffect.READMISSION_RISK_SCORE,
        tier=EffectTier.COMPUTATION,
        base_effect=_BaseEffect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Score 30-day readmission risk for discharge planning.",
        requires_human_review=False,
    ),
    HealthcareEffect.DRUG_INTERACTION_CHECK: EffectMeta(
        effect=HealthcareEffect.DRUG_INTERACTION_CHECK,
        tier=EffectTier.COMPUTATION,
        base_effect=_BaseEffect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Check for drug-drug or drug-allergy interactions in medication list.",
        requires_human_review=False,
    ),

    # Tier 3 — Draft / Internal (ALLOW)
    HealthcareEffect.CLINICAL_SUMMARY_DRAFT: EffectMeta(
        effect=HealthcareEffect.CLINICAL_SUMMARY_DRAFT,
        tier=EffectTier.DRAFT,
        base_effect=_BaseEffect.WRITE,
        default_decision=DefaultDecision.ALLOW,
        description="Draft a clinical summary note for clinician review. Not yet delivered.",
        requires_human_review=False,
    ),
    HealthcareEffect.CARE_PLAN_DRAFT: EffectMeta(
        effect=HealthcareEffect.CARE_PLAN_DRAFT,
        tier=EffectTier.DRAFT,
        base_effect=_BaseEffect.WRITE,
        default_decision=DefaultDecision.ALLOW,
        description="Draft a personalised care plan. Requires clinician sign-off before activation.",
        requires_human_review=False,
    ),
    HealthcareEffect.PRIOR_AUTH_REQUEST_DRAFT: EffectMeta(
        effect=HealthcareEffect.PRIOR_AUTH_REQUEST_DRAFT,
        tier=EffectTier.DRAFT,
        base_effect=_BaseEffect.WRITE,
        default_decision=DefaultDecision.ALLOW,
        description="Draft a prior authorisation request package. Not yet submitted.",
        requires_human_review=False,
    ),
    HealthcareEffect.REFERRAL_DRAFT: EffectMeta(
        effect=HealthcareEffect.REFERRAL_DRAFT,
        tier=EffectTier.DRAFT,
        base_effect=_BaseEffect.WRITE,
        default_decision=DefaultDecision.ALLOW,
        description="Draft a specialist referral. Not yet sent to provider.",
        requires_human_review=False,
    ),

    # Tier 4 — Output / External (ASK or ALLOW depending on severity)
    HealthcareEffect.CARE_GAP_ALERT_SEND: EffectMeta(
        effect=HealthcareEffect.CARE_GAP_ALERT_SEND,
        tier=EffectTier.OUTPUT,
        base_effect=_BaseEffect.NOTIFY,
        default_decision=DefaultDecision.ASK,
        description="Send a care gap outreach message to patient or care team.",
        requires_human_review=True,
    ),
    HealthcareEffect.PRIOR_AUTH_SUBMIT: EffectMeta(
        effect=HealthcareEffect.PRIOR_AUTH_SUBMIT,
        tier=EffectTier.OUTPUT,
        base_effect=_BaseEffect.NOTIFY,
        default_decision=DefaultDecision.ASK,
        description="Submit a prior authorisation request to the payer. Requires clinical review.",
        requires_human_review=True,
    ),
    HealthcareEffect.REFERRAL_SEND: EffectMeta(
        effect=HealthcareEffect.REFERRAL_SEND,
        tier=EffectTier.OUTPUT,
        base_effect=_BaseEffect.NOTIFY,
        default_decision=DefaultDecision.ASK,
        description="Send a specialist referral to the receiving provider.",
        requires_human_review=True,
    ),
    HealthcareEffect.PROVIDER_NOTIFICATION_SEND: EffectMeta(
        effect=HealthcareEffect.PROVIDER_NOTIFICATION_SEND,
        tier=EffectTier.OUTPUT,
        base_effect=_BaseEffect.NOTIFY,
        default_decision=DefaultDecision.ALLOW,
        description="Send an automated notification to a provider (e.g. care gap summary).",
        requires_human_review=False,
    ),
    HealthcareEffect.POPULATION_REPORT_EMIT: EffectMeta(
        effect=HealthcareEffect.POPULATION_REPORT_EMIT,
        tier=EffectTier.OUTPUT,
        base_effect=_BaseEffect.NOTIFY,
        default_decision=DefaultDecision.ALLOW,
        description="Emit a population health report to an internal analytics dashboard.",
        requires_human_review=False,
    ),
    HealthcareEffect.HUMAN_REVIEW_QUEUE_ADD: EffectMeta(
        effect=HealthcareEffect.HUMAN_REVIEW_QUEUE_ADD,
        tier=EffectTier.OUTPUT,
        base_effect=_BaseEffect.NOTIFY,
        default_decision=DefaultDecision.ALLOW,
        description="Add a clinical item to the human review queue for clinician sign-off.",
        requires_human_review=False,
    ),

    # Tier 5 — Persistence (ALLOW)
    HealthcareEffect.AUDIT_LOG_WRITE: EffectMeta(
        effect=HealthcareEffect.AUDIT_LOG_WRITE,
        tier=EffectTier.PERSISTENCE,
        base_effect=_BaseEffect.WRITE,
        default_decision=DefaultDecision.ALLOW,
        description="Write a HIPAA-compliant audit entry. Always permitted, always required.",
        requires_human_review=False,
    ),
    HealthcareEffect.CARE_INTERACTION_LOG_WRITE: EffectMeta(
        effect=HealthcareEffect.CARE_INTERACTION_LOG_WRITE,
        tier=EffectTier.PERSISTENCE,
        base_effect=_BaseEffect.WRITE,
        default_decision=DefaultDecision.ALLOW,
        description="Log a care interaction event for quality and outcomes tracking.",
        requires_human_review=False,
    ),
    HealthcareEffect.OUTCOME_LOG_WRITE: EffectMeta(
        effect=HealthcareEffect.OUTCOME_LOG_WRITE,
        tier=EffectTier.PERSISTENCE,
        base_effect=_BaseEffect.WRITE,
        default_decision=DefaultDecision.ALLOW,
        description="Log a clinical outcome for population health analytics.",
        requires_human_review=False,
    ),
    HealthcareEffect.FOLLOWUP_SCHEDULE: EffectMeta(
        effect=HealthcareEffect.FOLLOWUP_SCHEDULE,
        tier=EffectTier.PERSISTENCE,
        base_effect=_BaseEffect.WRITE,
        default_decision=DefaultDecision.ALLOW,
        description="Schedule a follow-up touchpoint in the care coordination pipeline.",
        requires_human_review=False,
    ),

    # Tier 6 — System Control
    HealthcareEffect.AGENT_SUSPEND: EffectMeta(
        effect=HealthcareEffect.AGENT_SUSPEND,
        tier=EffectTier.SYSTEM_CONTROL,
        base_effect=_BaseEffect.WRITE,
        default_decision=DefaultDecision.ASK,
        description="Suspend an agent via circuit breaker. Requires operator approval.",
        requires_human_review=True,
    ),
    HealthcareEffect.AGENT_PROMOTE: EffectMeta(
        effect=HealthcareEffect.AGENT_PROMOTE,
        tier=EffectTier.SYSTEM_CONTROL,
        base_effect=_BaseEffect.WRITE,
        default_decision=DefaultDecision.DENY,
        description="Promote agent from sandbox to production. Lifecycle manager only.",
        requires_human_review=True,
    ),
    HealthcareEffect.POLICY_RULE_MODIFY: EffectMeta(
        effect=HealthcareEffect.POLICY_RULE_MODIFY,
        tier=EffectTier.SYSTEM_CONTROL,
        base_effect=_BaseEffect.WRITE,
        default_decision=DefaultDecision.DENY,
        description="Modify a policy rule at runtime. Never permitted from an agent.",
        requires_human_review=True,
    ),

    # Hard Denies — HIPAA prohibitions
    HealthcareEffect.PATIENT_RECORD_WRITE: EffectMeta(
        effect=HealthcareEffect.PATIENT_RECORD_WRITE,
        tier=EffectTier.SYSTEM_CONTROL,
        base_effect=_BaseEffect.WRITE,
        default_decision=DefaultDecision.DENY,
        description="Modify patient clinical records directly. Hard blocked — requires EHR workflow.",
        requires_human_review=True,
    ),
    HealthcareEffect.PATIENT_RECORD_DELETE: EffectMeta(
        effect=HealthcareEffect.PATIENT_RECORD_DELETE,
        tier=EffectTier.SYSTEM_CONTROL,
        base_effect=_BaseEffect.DELETE,
        default_decision=DefaultDecision.DENY,
        description="Delete patient records. Hard blocked — HIPAA retention requirements apply.",
        requires_human_review=True,
    ),
    HealthcareEffect.CLINICAL_ORDER_EXECUTE: EffectMeta(
        effect=HealthcareEffect.CLINICAL_ORDER_EXECUTE,
        tier=EffectTier.SYSTEM_CONTROL,
        base_effect=_BaseEffect.WRITE,
        default_decision=DefaultDecision.DENY,
        description="Execute a clinical order (lab, medication, imaging). Hard blocked — clinician must order.",
        requires_human_review=True,
    ),
}
