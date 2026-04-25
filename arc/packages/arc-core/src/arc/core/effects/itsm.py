"""
ITSM Effect Taxonomy.

Covers agents operating in IT service management contexts: email triage,
incident creation, knowledge lookup, ticket routing, and escalation.

Designed to work across any ITSM platform (Pega Case Management,
ServiceNow, Jira Service Management, etc.) via config-driven connectors.

Taxonomy tiers:
  Tier 1 — Data Access      (read-only; broadly safe)
  Tier 2 — Computation      (internal analysis; no external impact)
  Tier 3 — Draft/Internal   (content prepared but not yet sent/submitted)
  Tier 4 — Output/External  (leaves the system — highest scrutiny)
  Tier 5 — Persistence      (log/record writes)
  Tier 6 — System Control   (platform-level; hard denies)

Hard denies cover irreversible or high-blast-radius actions:
  - Bulk email deletion
  - SLA breach suppression
  - Ticket bulk-close without review

Usage:
    from arc.core.effects import ITSMEffect, ITSM_EFFECT_METADATA

    manifest = AgentManifest(
        agent_id="email-triage",
        allowed_effects=[
            ITSMEffect.EMAIL_READ,
            ITSMEffect.EMAIL_CLASSIFY,
            ITSMEffect.TICKET_DRAFT,
            ITSMEffect.TICKET_CREATE,
        ],
        ...
    )
"""

from enum import Enum

from foundry.tollgate.types import Effect as _BaseEffect

from .base import DefaultDecision, EffectMeta, EffectTier


class ITSMEffect(str, Enum):
    """
    Complete effect taxonomy for ITSM / email-triage agents.

    Values are used as ``resource_type`` in Tollgate's ToolRequest,
    enabling fine-grained YAML policy rules per effect.
    Platform-agnostic: works with Pega, ServiceNow, or any ITSM.
    """

    # ─── TIER 1: Data Access ──────────────────────────────────────────────
    EMAIL_READ                  = "email.read"
    EMAIL_THREAD_READ           = "email.thread.read"
    EMAIL_ATTACHMENT_READ       = "email.attachment.read"
    TICKET_READ                 = "ticket.read"
    KNOWLEDGE_ARTICLE_READ      = "knowledge.article.read"
    KNOWLEDGE_BUDDY_QUERY       = "knowledge.buddy.query"
    USER_DIRECTORY_READ         = "user.directory.read"
    TEAM_ROSTER_READ            = "team.roster.read"
    SLA_CONFIG_READ             = "sla.config.read"
    QUEUE_READ                  = "queue.read"

    # ─── TIER 2: Computation ──────────────────────────────────────────────
    EMAIL_CLASSIFY              = "email.classify"
    PRIORITY_INFER              = "priority.infer"
    SENTIMENT_SCORE             = "sentiment.score"
    DUPLICATE_DETECT            = "duplicate.detect"
    SLA_CALCULATE               = "sla.calculate"
    ROUTING_DECIDE              = "routing.decide"
    ENTITY_EXTRACT              = "entity.extract"
    LANGUAGE_DETECT             = "language.detect"

    # ─── TIER 3: Draft / Internal ─────────────────────────────────────────
    TICKET_DRAFT                = "ticket.draft"
    EMAIL_REPLY_DRAFT           = "email.reply.draft"
    TICKET_SUMMARY_DRAFT        = "ticket.summary.draft"
    KNOWLEDGE_MATCH_DRAFT       = "knowledge.match.draft"

    # ─── TIER 4: Output / External ────────────────────────────────────────
    TICKET_CREATE               = "ticket.create"
    TICKET_UPDATE               = "ticket.update"
    TICKET_ASSIGN               = "ticket.assign"
    TICKET_ESCALATE             = "ticket.escalate"
    EMAIL_REPLY_SEND            = "email.reply.send"
    EMAIL_FORWARD               = "email.forward"
    ESCALATION_NOTIFY           = "escalation.notify"
    HUMAN_REVIEW_QUEUE_ADD      = "itsm.human.review.queue.add"

    # ─── TIER 5: Persistence ──────────────────────────────────────────────
    AUDIT_LOG_WRITE             = "itsm.audit.log.write"
    TRIAGE_LOG_WRITE            = "triage.log.write"
    TICKET_INTERACTION_LOG      = "ticket.interaction.log.write"
    CLASSIFICATION_SAVE         = "itsm.classification.save"

    # ─── TIER 6: System Control ───────────────────────────────────────────
    AGENT_SUSPEND               = "itsm.agent.suspend"
    QUEUE_PAUSE                 = "queue.pause"
    ROUTING_RULE_MODIFY         = "routing.rule.modify"

    # ─── Hard Denies ──────────────────────────────────────────────────────
    EMAIL_BULK_DELETE           = "email.bulk.delete"
    SLA_BREACH_SUPPRESS         = "sla.breach.suppress"
    TICKET_BULK_CLOSE           = "ticket.bulk.close"
    TICKET_DELETE               = "ticket.delete"


# ── Effect metadata registry ───────────────────────────────────────────────────

ITSM_EFFECT_METADATA: dict[ITSMEffect, EffectMeta] = {

    # ── Tier 1: Data Access (ALLOW) ──────────────────────────────────────

    ITSMEffect.EMAIL_READ: EffectMeta(
        effect=ITSMEffect.EMAIL_READ,
        tier=EffectTier.DATA_ACCESS,
        base_effect=_BaseEffect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Read individual email messages from the inbox.",
        requires_human_review=False,
    ),
    ITSMEffect.EMAIL_THREAD_READ: EffectMeta(
        effect=ITSMEffect.EMAIL_THREAD_READ,
        tier=EffectTier.DATA_ACCESS,
        base_effect=_BaseEffect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Read a full email thread including replies and history.",
        requires_human_review=False,
    ),
    ITSMEffect.EMAIL_ATTACHMENT_READ: EffectMeta(
        effect=ITSMEffect.EMAIL_ATTACHMENT_READ,
        tier=EffectTier.DATA_ACCESS,
        base_effect=_BaseEffect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Read email attachments for classification purposes.",
        requires_human_review=False,
    ),
    ITSMEffect.TICKET_READ: EffectMeta(
        effect=ITSMEffect.TICKET_READ,
        tier=EffectTier.DATA_ACCESS,
        base_effect=_BaseEffect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Read existing ticket details from ITSM platform.",
        requires_human_review=False,
    ),
    ITSMEffect.KNOWLEDGE_ARTICLE_READ: EffectMeta(
        effect=ITSMEffect.KNOWLEDGE_ARTICLE_READ,
        tier=EffectTier.DATA_ACCESS,
        base_effect=_BaseEffect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Read knowledge base articles for context enrichment.",
        requires_human_review=False,
    ),
    ITSMEffect.KNOWLEDGE_BUDDY_QUERY: EffectMeta(
        effect=ITSMEffect.KNOWLEDGE_BUDDY_QUERY,
        tier=EffectTier.DATA_ACCESS,
        base_effect=_BaseEffect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Query Pega Knowledge Buddy (RAG) for a grounded answer.",
        requires_human_review=False,
    ),
    ITSMEffect.USER_DIRECTORY_READ: EffectMeta(
        effect=ITSMEffect.USER_DIRECTORY_READ,
        tier=EffectTier.DATA_ACCESS,
        base_effect=_BaseEffect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Look up user profile, team, and org data for routing.",
        requires_human_review=False,
    ),
    ITSMEffect.TEAM_ROSTER_READ: EffectMeta(
        effect=ITSMEffect.TEAM_ROSTER_READ,
        tier=EffectTier.DATA_ACCESS,
        base_effect=_BaseEffect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Read on-call roster and team assignments for routing.",
        requires_human_review=False,
    ),
    ITSMEffect.SLA_CONFIG_READ: EffectMeta(
        effect=ITSMEffect.SLA_CONFIG_READ,
        tier=EffectTier.DATA_ACCESS,
        base_effect=_BaseEffect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Read SLA configuration and response-time targets.",
        requires_human_review=False,
    ),
    ITSMEffect.QUEUE_READ: EffectMeta(
        effect=ITSMEffect.QUEUE_READ,
        tier=EffectTier.DATA_ACCESS,
        base_effect=_BaseEffect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Read current queue depth and workload for routing decisions.",
        requires_human_review=False,
    ),

    # ── Tier 2: Computation (ALLOW) ──────────────────────────────────────

    ITSMEffect.EMAIL_CLASSIFY: EffectMeta(
        effect=ITSMEffect.EMAIL_CLASSIFY,
        tier=EffectTier.COMPUTATION,
        base_effect=_BaseEffect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Classify email intent (incident/request/question/complaint).",
        requires_human_review=False,
    ),
    ITSMEffect.PRIORITY_INFER: EffectMeta(
        effect=ITSMEffect.PRIORITY_INFER,
        tier=EffectTier.COMPUTATION,
        base_effect=_BaseEffect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Infer ticket priority (P1–P4) from email content and sender.",
        requires_human_review=False,
    ),
    ITSMEffect.SENTIMENT_SCORE: EffectMeta(
        effect=ITSMEffect.SENTIMENT_SCORE,
        tier=EffectTier.COMPUTATION,
        base_effect=_BaseEffect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Score sender sentiment to flag frustrated / urgent senders.",
        requires_human_review=False,
    ),
    ITSMEffect.DUPLICATE_DETECT: EffectMeta(
        effect=ITSMEffect.DUPLICATE_DETECT,
        tier=EffectTier.COMPUTATION,
        base_effect=_BaseEffect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Check if a similar ticket already exists before creating a new one.",
        requires_human_review=False,
    ),
    ITSMEffect.SLA_CALCULATE: EffectMeta(
        effect=ITSMEffect.SLA_CALCULATE,
        tier=EffectTier.COMPUTATION,
        base_effect=_BaseEffect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Calculate response and resolution deadlines from SLA config.",
        requires_human_review=False,
    ),
    ITSMEffect.ROUTING_DECIDE: EffectMeta(
        effect=ITSMEffect.ROUTING_DECIDE,
        tier=EffectTier.COMPUTATION,
        base_effect=_BaseEffect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Decide which team/queue this ticket should be routed to.",
        requires_human_review=False,
    ),
    ITSMEffect.ENTITY_EXTRACT: EffectMeta(
        effect=ITSMEffect.ENTITY_EXTRACT,
        tier=EffectTier.COMPUTATION,
        base_effect=_BaseEffect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Extract product, system, error code, and user from email body.",
        requires_human_review=False,
    ),
    ITSMEffect.LANGUAGE_DETECT: EffectMeta(
        effect=ITSMEffect.LANGUAGE_DETECT,
        tier=EffectTier.COMPUTATION,
        base_effect=_BaseEffect.READ,
        default_decision=DefaultDecision.ALLOW,
        description="Detect email language for routing to the right support team.",
        requires_human_review=False,
    ),

    # ── Tier 3: Draft / Internal (ALLOW) ─────────────────────────────────

    ITSMEffect.TICKET_DRAFT: EffectMeta(
        effect=ITSMEffect.TICKET_DRAFT,
        tier=EffectTier.DRAFT,
        base_effect=_BaseEffect.WRITE,
        default_decision=DefaultDecision.ALLOW,
        description="Draft ticket fields (title, description, category, priority). Not yet submitted.",
        requires_human_review=False,
    ),
    ITSMEffect.EMAIL_REPLY_DRAFT: EffectMeta(
        effect=ITSMEffect.EMAIL_REPLY_DRAFT,
        tier=EffectTier.DRAFT,
        base_effect=_BaseEffect.WRITE,
        default_decision=DefaultDecision.ALLOW,
        description="Draft an acknowledgement or triage reply. Not yet sent.",
        requires_human_review=False,
    ),
    ITSMEffect.TICKET_SUMMARY_DRAFT: EffectMeta(
        effect=ITSMEffect.TICKET_SUMMARY_DRAFT,
        tier=EffectTier.DRAFT,
        base_effect=_BaseEffect.WRITE,
        default_decision=DefaultDecision.ALLOW,
        description="Draft a summary of the email thread for the ticket description.",
        requires_human_review=False,
    ),
    ITSMEffect.KNOWLEDGE_MATCH_DRAFT: EffectMeta(
        effect=ITSMEffect.KNOWLEDGE_MATCH_DRAFT,
        tier=EffectTier.DRAFT,
        base_effect=_BaseEffect.WRITE,
        default_decision=DefaultDecision.ALLOW,
        description="Draft a knowledge match block to attach to the ticket.",
        requires_human_review=False,
    ),

    # ── Tier 4: Output / External ─────────────────────────────────────────
    # Key governance: P1/P2 ticket creation → ASK
    # P3/P4 routine tickets → ALLOW
    # External email send → ASK

    ITSMEffect.TICKET_CREATE: EffectMeta(
        effect=ITSMEffect.TICKET_CREATE,
        tier=EffectTier.OUTPUT,
        base_effect=_BaseEffect.WRITE,
        default_decision=DefaultDecision.ASK,
        description=(
            "Submit a new ticket to the ITSM platform (Pega or ServiceNow). "
            "P1/P2 require human approval; P3/P4 may be pre-authorised via grant."
        ),
        requires_human_review=True,
    ),
    ITSMEffect.TICKET_UPDATE: EffectMeta(
        effect=ITSMEffect.TICKET_UPDATE,
        tier=EffectTier.OUTPUT,
        base_effect=_BaseEffect.WRITE,
        default_decision=DefaultDecision.ALLOW,
        description="Update an existing ticket (add notes, change status, attach KB article).",
        requires_human_review=False,
    ),
    ITSMEffect.TICKET_ASSIGN: EffectMeta(
        effect=ITSMEffect.TICKET_ASSIGN,
        tier=EffectTier.OUTPUT,
        base_effect=_BaseEffect.WRITE,
        default_decision=DefaultDecision.ASK,
        description="Assign a ticket to a specific agent or team. ASK for team lead assignment.",
        requires_human_review=True,
    ),
    ITSMEffect.TICKET_ESCALATE: EffectMeta(
        effect=ITSMEffect.TICKET_ESCALATE,
        tier=EffectTier.OUTPUT,
        base_effect=_BaseEffect.NOTIFY,
        default_decision=DefaultDecision.ASK,
        description="Escalate a ticket to a higher tier or management. Always requires approval.",
        requires_human_review=True,
    ),
    ITSMEffect.EMAIL_REPLY_SEND: EffectMeta(
        effect=ITSMEffect.EMAIL_REPLY_SEND,
        tier=EffectTier.OUTPUT,
        base_effect=_BaseEffect.NOTIFY,
        default_decision=DefaultDecision.ASK,
        description=(
            "Send an email reply to the original sender. "
            "Auto-acknowledgements for P3/P4 may be pre-authorised; "
            "P1/P2 or SLA-commitment replies always require ASK."
        ),
        requires_human_review=True,
    ),
    ITSMEffect.EMAIL_FORWARD: EffectMeta(
        effect=ITSMEffect.EMAIL_FORWARD,
        tier=EffectTier.OUTPUT,
        base_effect=_BaseEffect.NOTIFY,
        default_decision=DefaultDecision.ASK,
        description="Forward an email to another team or escalation contact.",
        requires_human_review=True,
    ),
    ITSMEffect.ESCALATION_NOTIFY: EffectMeta(
        effect=ITSMEffect.ESCALATION_NOTIFY,
        tier=EffectTier.OUTPUT,
        base_effect=_BaseEffect.NOTIFY,
        default_decision=DefaultDecision.ASK,
        description="Send an escalation notification (Slack, Teams, PagerDuty, etc.).",
        requires_human_review=True,
    ),
    ITSMEffect.HUMAN_REVIEW_QUEUE_ADD: EffectMeta(
        effect=ITSMEffect.HUMAN_REVIEW_QUEUE_ADD,
        tier=EffectTier.OUTPUT,
        base_effect=_BaseEffect.NOTIFY,
        default_decision=DefaultDecision.ALLOW,
        description="Add an email or draft ticket to the human review queue.",
        requires_human_review=False,
    ),

    # ── Tier 5: Persistence (ALLOW) ──────────────────────────────────────

    ITSMEffect.AUDIT_LOG_WRITE: EffectMeta(
        effect=ITSMEffect.AUDIT_LOG_WRITE,
        tier=EffectTier.PERSISTENCE,
        base_effect=_BaseEffect.WRITE,
        default_decision=DefaultDecision.ALLOW,
        description="Write an audit entry. Always permitted.",
        requires_human_review=False,
    ),
    ITSMEffect.TRIAGE_LOG_WRITE: EffectMeta(
        effect=ITSMEffect.TRIAGE_LOG_WRITE,
        tier=EffectTier.PERSISTENCE,
        base_effect=_BaseEffect.WRITE,
        default_decision=DefaultDecision.ALLOW,
        description="Log triage decision (classification, priority, routing) for analytics.",
        requires_human_review=False,
    ),
    ITSMEffect.TICKET_INTERACTION_LOG: EffectMeta(
        effect=ITSMEffect.TICKET_INTERACTION_LOG,
        tier=EffectTier.PERSISTENCE,
        base_effect=_BaseEffect.WRITE,
        default_decision=DefaultDecision.ALLOW,
        description="Log agent interaction against a ticket for audit and SLA reporting.",
        requires_human_review=False,
    ),
    ITSMEffect.CLASSIFICATION_SAVE: EffectMeta(
        effect=ITSMEffect.CLASSIFICATION_SAVE,
        tier=EffectTier.PERSISTENCE,
        base_effect=_BaseEffect.WRITE,
        default_decision=DefaultDecision.ALLOW,
        description="Persist email classification result for retraining / feedback loop.",
        requires_human_review=False,
    ),

    # ── Tier 6: System Control ────────────────────────────────────────────

    ITSMEffect.AGENT_SUSPEND: EffectMeta(
        effect=ITSMEffect.AGENT_SUSPEND,
        tier=EffectTier.SYSTEM_CONTROL,
        base_effect=_BaseEffect.WRITE,
        default_decision=DefaultDecision.ASK,
        description="Suspend the triage agent via circuit breaker. Requires operator approval.",
        requires_human_review=True,
    ),
    ITSMEffect.QUEUE_PAUSE: EffectMeta(
        effect=ITSMEffect.QUEUE_PAUSE,
        tier=EffectTier.SYSTEM_CONTROL,
        base_effect=_BaseEffect.WRITE,
        default_decision=DefaultDecision.ASK,
        description="Pause inbound queue processing. Operator approval required.",
        requires_human_review=True,
    ),
    ITSMEffect.ROUTING_RULE_MODIFY: EffectMeta(
        effect=ITSMEffect.ROUTING_RULE_MODIFY,
        tier=EffectTier.SYSTEM_CONTROL,
        base_effect=_BaseEffect.WRITE,
        default_decision=DefaultDecision.DENY,
        description="Modify a routing rule at runtime. Never permitted from an agent.",
        requires_human_review=True,
    ),

    # ── Hard Denies ───────────────────────────────────────────────────────

    ITSMEffect.EMAIL_BULK_DELETE: EffectMeta(
        effect=ITSMEffect.EMAIL_BULK_DELETE,
        tier=EffectTier.SYSTEM_CONTROL,
        base_effect=_BaseEffect.DELETE,
        default_decision=DefaultDecision.DENY,
        description="Bulk-delete emails. Hard blocked — irreversible, high blast-radius.",
        requires_human_review=True,
    ),
    ITSMEffect.SLA_BREACH_SUPPRESS: EffectMeta(
        effect=ITSMEffect.SLA_BREACH_SUPPRESS,
        tier=EffectTier.SYSTEM_CONTROL,
        base_effect=_BaseEffect.WRITE,
        default_decision=DefaultDecision.DENY,
        description="Suppress an SLA breach alert. Hard blocked — violates reporting obligations.",
        requires_human_review=True,
    ),
    ITSMEffect.TICKET_BULK_CLOSE: EffectMeta(
        effect=ITSMEffect.TICKET_BULK_CLOSE,
        tier=EffectTier.SYSTEM_CONTROL,
        base_effect=_BaseEffect.WRITE,
        default_decision=DefaultDecision.DENY,
        description="Bulk-close tickets without review. Hard blocked.",
        requires_human_review=True,
    ),
    ITSMEffect.TICKET_DELETE: EffectMeta(
        effect=ITSMEffect.TICKET_DELETE,
        tier=EffectTier.SYSTEM_CONTROL,
        base_effect=_BaseEffect.DELETE,
        default_decision=DefaultDecision.DENY,
        description="Delete a ticket record. Hard blocked — audit trail must be preserved.",
        requires_human_review=True,
    ),
}
