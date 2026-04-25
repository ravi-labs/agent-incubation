"""
Legal Effect Taxonomy.

Covers agents operating in legal-services contexts: contract review, regulatory
compliance, litigation support, document discovery, due diligence, and privilege
review.

Taxonomy tiers mirror the FinancialEffect / HealthcareEffect model:
  Tier 1 — Data Access      (read-only; broadly safe)
  Tier 2 — Computation      (internal analysis; no external impact)
  Tier 3 — Draft/Internal   (content created but not yet delivered)
  Tier 4 — Output/External  (leaves the system — highest scrutiny)
  Tier 5 — Persistence      (log/record writes)
  Tier 6 — System Control   (platform-level; hard denies)

Hard denies cover actions that constitute unauthorised practice of law or
irreversible legal acts that must always be performed by a licensed attorney.

Usage:
    from arc.core.effects import LegalEffect, LEGAL_EFFECT_METADATA

    manifest = AgentManifest(
        agent_id="contract-reviewer",
        allowed_effects=[
            LegalEffect.CONTRACT_READ,
            LegalEffect.CLAUSE_RISK_SCORE,
            LegalEffect.REDLINE_DRAFT,
            LegalEffect.REVIEW_SUMMARY_SEND,
        ],
        ...
    )
"""

from enum import Enum

from tollgate.types import Effect as _BaseEffect

from .base import DefaultDecision, EffectMeta, EffectTier


class LegalEffect(str, Enum):
    """
    Complete effect taxonomy for legal-services agents.

    Designed for law firms, in-house legal departments, and legal-tech
    platforms. Values are used as ``resource_type`` in Tollgate's ToolRequest.
    """

    # ─── TIER 1: Data Access ──────────────────────────────────────────────
    CONTRACT_READ                   = "contract.read"
    REGULATION_READ                 = "regulation.read"
    CASE_LAW_READ                   = "case.law.read"
    MATTER_FILE_READ                = "matter.file.read"
    PRECEDENT_READ                  = "precedent.read"
    PRIVILEGE_LOG_READ              = "privilege.log.read"
    ENTITY_REGISTRY_READ            = "entity.registry.read"
    COURT_DOCKET_READ               = "court.docket.read"

    # ─── TIER 2: Computation ──────────────────────────────────────────────
    CLAUSE_RISK_SCORE               = "clause.risk.score"
    COMPLIANCE_GAP_IDENTIFY         = "compliance.gap.identify"
    PRIVILEGE_CLASSIFY              = "privilege.classify"
    DOCUMENT_RELEVANCE_SCORE        = "document.relevance.score"
    DEADLINE_CALCULATE              = "deadline.calculate"
    ENTITY_EXTRACTION_RUN           = "entity.extraction.run"
    SIMILARITY_SEARCH_RUN           = "similarity.search.run"

    # ─── TIER 3: Draft / Internal ─────────────────────────────────────────
    REDLINE_DRAFT                   = "redline.draft"
    CONTRACT_SUMMARY_DRAFT          = "contract.summary.draft"
    LEGAL_MEMO_DRAFT                = "legal.memo.draft"
    COMPLIANCE_REPORT_DRAFT         = "compliance.report.draft"
    DISCOVERY_RESPONSE_DRAFT        = "discovery.response.draft"
    PRIVILEGE_LOG_DRAFT             = "privilege.log.draft"

    # ─── TIER 4: Output / External ────────────────────────────────────────
    REVIEW_SUMMARY_SEND             = "review.summary.send"
    COMPLIANCE_ALERT_SEND           = "compliance.alert.send"
    MATTER_UPDATE_NOTIFY            = "matter.update.notify"
    EXTERNAL_COUNSEL_NOTIFY         = "external.counsel.notify"
    REGULATORY_FILING_SUBMIT        = "regulatory.filing.submit"
    HUMAN_REVIEW_QUEUE_ADD          = "legal.human.review.queue.add"

    # ─── TIER 5: Persistence ──────────────────────────────────────────────
    AUDIT_LOG_WRITE                 = "legal.audit.log.write"
    MATTER_INTERACTION_LOG_WRITE    = "matter.interaction.log.write"
    DOCUMENT_CLASSIFICATION_SAVE    = "document.classification.save"
    DEADLINE_RECORD                 = "deadline.record"

    # ─── TIER 6: System Control ───────────────────────────────────────────
    AGENT_SUSPEND                   = "legal.agent.suspend"
    AGENT_PROMOTE                   = "legal.agent.promote"
    POLICY_RULE_MODIFY              = "legal.policy.rule.modify"

    # ─── Hard Denies (UPL / Privilege / Irreversible) ─────────────────────
    LEGAL_ADVICE_RENDER             = "legal.advice.render"
    PRIVILEGED_DOCUMENT_DISCLOSE    = "privileged.document.disclose"
    COURT_FILING_EXECUTE            = "court.filing.execute"
    SETTLEMENT_EXECUTE              = "settlement.execute"


# ── Effect metadata registry ───────────────────────────────────────────────────

LEGAL_EFFECT_METADATA: dict[LegalEffect, EffectMeta] = {

    # Tier 1 — Data Access (ALLOW)
    LegalEffect.CONTRACT_READ: EffectMeta(
        effect=LegalEffect.CONTRACT_READ,
        tier=EffectTier.DATA_ACCESS,
        base_effect=_BaseEffect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Read contract documents from matter repository.",
        requires_human_review=False,
    ),
    LegalEffect.REGULATION_READ: EffectMeta(
        effect=LegalEffect.REGULATION_READ,
        tier=EffectTier.DATA_ACCESS,
        base_effect=_BaseEffect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Read statutes, regulations, and regulatory guidance.",
        requires_human_review=False,
    ),
    LegalEffect.CASE_LAW_READ: EffectMeta(
        effect=LegalEffect.CASE_LAW_READ,
        tier=EffectTier.DATA_ACCESS,
        base_effect=_BaseEffect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Read judicial opinions and case law from a legal database.",
        requires_human_review=False,
    ),
    LegalEffect.MATTER_FILE_READ: EffectMeta(
        effect=LegalEffect.MATTER_FILE_READ,
        tier=EffectTier.DATA_ACCESS,
        base_effect=_BaseEffect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Read documents and correspondence in a client matter file.",
        requires_human_review=False,
    ),
    LegalEffect.PRECEDENT_READ: EffectMeta(
        effect=LegalEffect.PRECEDENT_READ,
        tier=EffectTier.DATA_ACCESS,
        base_effect=_BaseEffect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Read internal precedent library (standard clauses, playbooks, past deals).",
        requires_human_review=False,
    ),
    LegalEffect.PRIVILEGE_LOG_READ: EffectMeta(
        effect=LegalEffect.PRIVILEGE_LOG_READ,
        tier=EffectTier.DATA_ACCESS,
        base_effect=_BaseEffect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Read privilege log entries for review or QC purposes.",
        requires_human_review=False,
    ),
    LegalEffect.ENTITY_REGISTRY_READ: EffectMeta(
        effect=LegalEffect.ENTITY_REGISTRY_READ,
        tier=EffectTier.DATA_ACCESS,
        base_effect=_BaseEffect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Read corporate entity registry, ownership structure, and filings.",
        requires_human_review=False,
    ),
    LegalEffect.COURT_DOCKET_READ: EffectMeta(
        effect=LegalEffect.COURT_DOCKET_READ,
        tier=EffectTier.DATA_ACCESS,
        base_effect=_BaseEffect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Read public court docket entries and case status information.",
        requires_human_review=False,
    ),

    # Tier 2 — Computation (ALLOW)
    LegalEffect.CLAUSE_RISK_SCORE: EffectMeta(
        effect=LegalEffect.CLAUSE_RISK_SCORE,
        tier=EffectTier.COMPUTATION,
        base_effect=_BaseEffect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Score contract clause risk against a playbook or reference standard.",
        requires_human_review=False,
    ),
    LegalEffect.COMPLIANCE_GAP_IDENTIFY: EffectMeta(
        effect=LegalEffect.COMPLIANCE_GAP_IDENTIFY,
        tier=EffectTier.COMPUTATION,
        base_effect=_BaseEffect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Identify gaps between current practices and a regulatory framework.",
        requires_human_review=False,
    ),
    LegalEffect.PRIVILEGE_CLASSIFY: EffectMeta(
        effect=LegalEffect.PRIVILEGE_CLASSIFY,
        tier=EffectTier.COMPUTATION,
        base_effect=_BaseEffect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Classify documents as privileged, responsive, or non-responsive.",
        requires_human_review=False,
    ),
    LegalEffect.DOCUMENT_RELEVANCE_SCORE: EffectMeta(
        effect=LegalEffect.DOCUMENT_RELEVANCE_SCORE,
        tier=EffectTier.COMPUTATION,
        base_effect=_BaseEffect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Score document relevance to a discovery request or legal issue.",
        requires_human_review=False,
    ),
    LegalEffect.DEADLINE_CALCULATE: EffectMeta(
        effect=LegalEffect.DEADLINE_CALCULATE,
        tier=EffectTier.COMPUTATION,
        base_effect=_BaseEffect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Calculate statutory deadlines, limitations periods, or response windows.",
        requires_human_review=False,
    ),
    LegalEffect.ENTITY_EXTRACTION_RUN: EffectMeta(
        effect=LegalEffect.ENTITY_EXTRACTION_RUN,
        tier=EffectTier.COMPUTATION,
        base_effect=_BaseEffect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Extract parties, dates, obligations, and defined terms from documents.",
        requires_human_review=False,
    ),
    LegalEffect.SIMILARITY_SEARCH_RUN: EffectMeta(
        effect=LegalEffect.SIMILARITY_SEARCH_RUN,
        tier=EffectTier.COMPUTATION,
        base_effect=_BaseEffect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Run semantic similarity search across a document corpus.",
        requires_human_review=False,
    ),

    # Tier 3 — Draft / Internal (ALLOW)
    LegalEffect.REDLINE_DRAFT: EffectMeta(
        effect=LegalEffect.REDLINE_DRAFT,
        tier=EffectTier.DRAFT,
        base_effect=_BaseEffect.WRITE,
        default_decision=DefaultDecision.ALLOW,
        description="Draft redlined contract markup. Requires attorney review before sending.",
        requires_human_review=False,
    ),
    LegalEffect.CONTRACT_SUMMARY_DRAFT: EffectMeta(
        effect=LegalEffect.CONTRACT_SUMMARY_DRAFT,
        tier=EffectTier.DRAFT,
        base_effect=_BaseEffect.WRITE,
        default_decision=DefaultDecision.ALLOW,
        description="Draft an abstract or summary of contract key terms. Not yet delivered.",
        requires_human_review=False,
    ),
    LegalEffect.LEGAL_MEMO_DRAFT: EffectMeta(
        effect=LegalEffect.LEGAL_MEMO_DRAFT,
        tier=EffectTier.DRAFT,
        base_effect=_BaseEffect.WRITE,
        default_decision=DefaultDecision.ALLOW,
        description="Draft an internal legal memorandum for attorney review.",
        requires_human_review=False,
    ),
    LegalEffect.COMPLIANCE_REPORT_DRAFT: EffectMeta(
        effect=LegalEffect.COMPLIANCE_REPORT_DRAFT,
        tier=EffectTier.DRAFT,
        base_effect=_BaseEffect.WRITE,
        default_decision=DefaultDecision.ALLOW,
        description="Draft a compliance gap or risk report for internal review.",
        requires_human_review=False,
    ),
    LegalEffect.DISCOVERY_RESPONSE_DRAFT: EffectMeta(
        effect=LegalEffect.DISCOVERY_RESPONSE_DRAFT,
        tier=EffectTier.DRAFT,
        base_effect=_BaseEffect.WRITE,
        default_decision=DefaultDecision.ALLOW,
        description="Draft discovery responses (interrogatories, RFPs). Not yet filed.",
        requires_human_review=False,
    ),
    LegalEffect.PRIVILEGE_LOG_DRAFT: EffectMeta(
        effect=LegalEffect.PRIVILEGE_LOG_DRAFT,
        tier=EffectTier.DRAFT,
        base_effect=_BaseEffect.WRITE,
        default_decision=DefaultDecision.ALLOW,
        description="Draft privilege log entries for attorney review before production.",
        requires_human_review=False,
    ),

    # Tier 4 — Output / External (ASK or ALLOW depending on recipient)
    LegalEffect.REVIEW_SUMMARY_SEND: EffectMeta(
        effect=LegalEffect.REVIEW_SUMMARY_SEND,
        tier=EffectTier.OUTPUT,
        base_effect=_BaseEffect.NOTIFY,
        default_decision=DefaultDecision.ALLOW,
        description="Send a contract review summary to an internal stakeholder.",
        requires_human_review=False,
    ),
    LegalEffect.COMPLIANCE_ALERT_SEND: EffectMeta(
        effect=LegalEffect.COMPLIANCE_ALERT_SEND,
        tier=EffectTier.OUTPUT,
        base_effect=_BaseEffect.NOTIFY,
        default_decision=DefaultDecision.ALLOW,
        description="Send a compliance gap alert to the responsible business owner.",
        requires_human_review=False,
    ),
    LegalEffect.MATTER_UPDATE_NOTIFY: EffectMeta(
        effect=LegalEffect.MATTER_UPDATE_NOTIFY,
        tier=EffectTier.OUTPUT,
        base_effect=_BaseEffect.NOTIFY,
        default_decision=DefaultDecision.ALLOW,
        description="Notify the matter team of a status update or deadline.",
        requires_human_review=False,
    ),
    LegalEffect.EXTERNAL_COUNSEL_NOTIFY: EffectMeta(
        effect=LegalEffect.EXTERNAL_COUNSEL_NOTIFY,
        tier=EffectTier.OUTPUT,
        base_effect=_BaseEffect.NOTIFY,
        default_decision=DefaultDecision.ASK,
        description="Send a notification or document to external counsel. Requires attorney approval.",
        requires_human_review=True,
    ),
    LegalEffect.REGULATORY_FILING_SUBMIT: EffectMeta(
        effect=LegalEffect.REGULATORY_FILING_SUBMIT,
        tier=EffectTier.OUTPUT,
        base_effect=_BaseEffect.NOTIFY,
        default_decision=DefaultDecision.ASK,
        description="Submit a regulatory filing or disclosure. Requires attorney sign-off.",
        requires_human_review=True,
    ),
    LegalEffect.HUMAN_REVIEW_QUEUE_ADD: EffectMeta(
        effect=LegalEffect.HUMAN_REVIEW_QUEUE_ADD,
        tier=EffectTier.OUTPUT,
        base_effect=_BaseEffect.NOTIFY,
        default_decision=DefaultDecision.ALLOW,
        description="Add a document or issue to the attorney review queue.",
        requires_human_review=False,
    ),

    # Tier 5 — Persistence (ALLOW)
    LegalEffect.AUDIT_LOG_WRITE: EffectMeta(
        effect=LegalEffect.AUDIT_LOG_WRITE,
        tier=EffectTier.PERSISTENCE,
        base_effect=_BaseEffect.WRITE,
        default_decision=DefaultDecision.ALLOW,
        description="Write an audit entry. Always permitted, always required.",
        requires_human_review=False,
    ),
    LegalEffect.MATTER_INTERACTION_LOG_WRITE: EffectMeta(
        effect=LegalEffect.MATTER_INTERACTION_LOG_WRITE,
        tier=EffectTier.PERSISTENCE,
        base_effect=_BaseEffect.WRITE,
        default_decision=DefaultDecision.ALLOW,
        description="Log an agent interaction against a matter for billing and audit.",
        requires_human_review=False,
    ),
    LegalEffect.DOCUMENT_CLASSIFICATION_SAVE: EffectMeta(
        effect=LegalEffect.DOCUMENT_CLASSIFICATION_SAVE,
        tier=EffectTier.PERSISTENCE,
        base_effect=_BaseEffect.WRITE,
        default_decision=DefaultDecision.ALLOW,
        description="Persist a document classification decision (privilege, relevance, tag).",
        requires_human_review=False,
    ),
    LegalEffect.DEADLINE_RECORD: EffectMeta(
        effect=LegalEffect.DEADLINE_RECORD,
        tier=EffectTier.PERSISTENCE,
        base_effect=_BaseEffect.WRITE,
        default_decision=DefaultDecision.ALLOW,
        description="Record a computed deadline or docketing entry to the matter calendar.",
        requires_human_review=False,
    ),

    # Tier 6 — System Control
    LegalEffect.AGENT_SUSPEND: EffectMeta(
        effect=LegalEffect.AGENT_SUSPEND,
        tier=EffectTier.SYSTEM_CONTROL,
        base_effect=_BaseEffect.WRITE,
        default_decision=DefaultDecision.ASK,
        description="Suspend an agent via circuit breaker. Requires operator approval.",
        requires_human_review=True,
    ),
    LegalEffect.AGENT_PROMOTE: EffectMeta(
        effect=LegalEffect.AGENT_PROMOTE,
        tier=EffectTier.SYSTEM_CONTROL,
        base_effect=_BaseEffect.WRITE,
        default_decision=DefaultDecision.DENY,
        description="Promote agent from sandbox to production. Lifecycle manager only.",
        requires_human_review=True,
    ),
    LegalEffect.POLICY_RULE_MODIFY: EffectMeta(
        effect=LegalEffect.POLICY_RULE_MODIFY,
        tier=EffectTier.SYSTEM_CONTROL,
        base_effect=_BaseEffect.WRITE,
        default_decision=DefaultDecision.DENY,
        description="Modify a policy rule at runtime. Never permitted from an agent.",
        requires_human_review=True,
    ),

    # Hard Denies — UPL / Privilege / Irreversible acts
    LegalEffect.LEGAL_ADVICE_RENDER: EffectMeta(
        effect=LegalEffect.LEGAL_ADVICE_RENDER,
        tier=EffectTier.SYSTEM_CONTROL,
        base_effect=_BaseEffect.NOTIFY,
        default_decision=DefaultDecision.DENY,
        description=(
            "Render legal advice directly to a client. Hard blocked — constitutes "
            "unauthorised practice of law (UPL) without attorney supervision."
        ),
        requires_human_review=True,
    ),
    LegalEffect.PRIVILEGED_DOCUMENT_DISCLOSE: EffectMeta(
        effect=LegalEffect.PRIVILEGED_DOCUMENT_DISCLOSE,
        tier=EffectTier.SYSTEM_CONTROL,
        base_effect=_BaseEffect.NOTIFY,
        default_decision=DefaultDecision.DENY,
        description=(
            "Disclose a privileged document to an opposing party or third party. "
            "Hard blocked — waives attorney-client privilege."
        ),
        requires_human_review=True,
    ),
    LegalEffect.COURT_FILING_EXECUTE: EffectMeta(
        effect=LegalEffect.COURT_FILING_EXECUTE,
        tier=EffectTier.SYSTEM_CONTROL,
        base_effect=_BaseEffect.WRITE,
        default_decision=DefaultDecision.DENY,
        description=(
            "File a document with a court or tribunal. Hard blocked — must be "
            "signed and submitted by a licensed attorney of record."
        ),
        requires_human_review=True,
    ),
    LegalEffect.SETTLEMENT_EXECUTE: EffectMeta(
        effect=LegalEffect.SETTLEMENT_EXECUTE,
        tier=EffectTier.SYSTEM_CONTROL,
        base_effect=_BaseEffect.WRITE,
        default_decision=DefaultDecision.DENY,
        description=(
            "Execute a settlement agreement or release. Hard blocked — requires "
            "client authorisation and attorney countersignature."
        ),
        requires_human_review=True,
    ),
}
