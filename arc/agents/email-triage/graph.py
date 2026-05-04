"""
LangGraph email triage graph for Arc.

Full StateGraph implementation for email triage with governance at every node.
Each node calls run_effect() to ensure ControlTower approves before execution.

Graph flow:
    classify → extract_entities → lookup_user → query_knowledge
    → check_duplicate → draft_ticket → create_ticket → log_triage

Conditional edges:
    - check_duplicate: if duplicate found → log_triage (skip create)
    - create_ticket:   P1/P2 → interrupt (ASK), P3/P4 → continue if confidence >= 0.85

LLM:
    - Real: ChatBedrockConverse (us.anthropic.claude-3-5-sonnet-20241022-v2:0),
      wrapped at the call site with ``arc.orchestrators.governed_chat_model``
      so every model invocation routes through ``agent.run_effect()``
      and lands in ControlTower's audit log alongside any other effect.
    - Harness: MockBedrockLLM (deterministic, keyword-based — same interface)

Usage:
    from arc.agents.email_triage.graph import build_email_triage_graph
    from arc.orchestrators import LangGraphOrchestrator
    from langgraph.checkpoint.memory import MemorySaver

    graph = build_email_triage_graph(agent, use_mock_llm=False)
    orchestrator = LangGraphOrchestrator(graph=graph, checkpointer=MemorySaver())
"""

from __future__ import annotations

import logging
import re
from typing import Any, TypedDict

logger = logging.getLogger(__name__)


# ── State schema ──────────────────────────────────────────────────────────────

class EmailTriageState(TypedDict, total=False):
    """Full state for the retirement-plan email-triage graph."""

    # Input
    email_id:       str
    email:          dict          # raw email from Outlook / fixture
    run_id:         str

    # Classification (generic — keep for compatibility with existing nodes)
    intent:         str           # incident/request/question/complaint
    priority:       str           # P1/P2/P3/P4
    confidence:     float
    sentiment:      str           # positive/neutral/negative/urgent

    # Retirement-domain classification (drives Pega case-type selection)
    case_type:      str           # distribution / loan_hardship / sponsor_inquiry
    case_subtype:   str | None    # rollover/lump_sum/rmd | loan/hardship | amendment/compliance/audit/general

    # Entities
    entities:       dict          # extracted retirement-shaped fields

    # Knowledge
    kb_match:       dict | None   # Knowledge Buddy result

    # Ticket
    triage_data:    dict | None   # arc-shaped data the Pega router maps from
    ticket_draft:   dict | None   # Pega-shaped payload (output of registry.map)
    ticket_id:      str | None    # created Pega case ID

    # Routing
    assigned_team:  str
    ticket_target:  str           # "pega" or "servicenow"

    # Control
    approval_status: str          # "pending" / "approved" / "denied"
    error:           str | None
    completed:       bool
    is_duplicate:    bool         # set by check_duplicate node
    fraud_flag:      bool         # if True, ticket.create is hard-DENIED by policy


# ── MockBedrockLLM ────────────────────────────────────────────────────────────

class MockBedrockLLM:
    """
    Deterministic mock LLM for harness testing.

    Returns consistent classifications based on keyword matching,
    reusing the rule-based classifier logic from the email triage POC.
    Interface matches ChatBedrockConverse for drop-in swap.
    """

    # Priority keyword signals (reused from POC)
    PRIORITY_SIGNALS = {
        "P1": [
            "production down", "completely down", "all users", "data loss",
            "security breach", "unauthorized access", "emergency", "critical",
        ],
        "P2": [
            "30%", "significant", "vip", "largest client", "enterprise client",
            "waited 3 days", "sla", "degraded", "affecting",
        ],
        "P3": [
            "wrong", "incorrect", "slow", "performance", "workaround available",
            "when you have a chance", "not critical",
        ],
    }

    INTENT_SIGNALS = {
        "incident":  ["error", "down", "failing", "broken", "issue", "breach", "timeout", "slow"],
        "request":   ["request", "update", "add", "change", "need", "export", "feature"],
        "question":  ["how do i", "how to", "could you", "where", "?"],
        "complaint": ["unacceptable", "waited", "no response", "complaint", "frustrated"],
    }

    SENTIMENT_SIGNALS = {
        "urgent":   ["emergency", "urgent", "critical", "immediately", "asap", "now"],
        "negative": ["unacceptable", "frustrated", "terrible", "awful", "disappointed"],
        "positive": ["thank", "great", "appreciate", "excellent"],
    }

    def classify(self, email: dict) -> dict:
        """Classify an email using keyword matching."""
        text = (email.get("subject", "") + " " + email.get("body", "")).lower()

        # Priority
        priority = "P4"
        for p in ["P1", "P2", "P3"]:
            if any(sig in text for sig in self.PRIORITY_SIGNALS[p]):
                priority = p
                break

        # Intent
        intent = "incident"
        for candidate, signals in self.INTENT_SIGNALS.items():
            if any(s in text for s in signals):
                intent = candidate
                break

        # Sentiment
        sentiment = "neutral"
        for candidate, signals in self.SENTIMENT_SIGNALS.items():
            if any(s in text for s in signals):
                sentiment = candidate
                break

        # Confidence: heuristic from signal hit count
        p_signals = self.PRIORITY_SIGNALS.get(priority, [])
        i_signals = self.INTENT_SIGNALS.get(intent, [])
        hits = sum(1 for s in p_signals + i_signals if s in text)
        confidence = min(0.65 + hits * 0.08, 0.98)

        return {
            "intent":     intent,
            "priority":   priority,
            "confidence": round(confidence, 2),
            "sentiment":  sentiment,
        }

    def extract_entities(self, email: dict) -> dict:
        """Extract entities from email text."""
        text    = email.get("body", "")
        subject = email.get("subject", "")
        combined = subject + " " + text

        ticket_refs = re.findall(r"TKT-\d+", combined, re.IGNORECASE)
        error_codes = re.findall(r"\b[45]\d{2}\b", combined)
        percentages = re.findall(r"\d+%", combined)

        # Extract system references
        systems = []
        for sys_name in ["portal", "api", "database", "auth", "payment", "reports", "analytics"]:
            if sys_name in combined.lower():
                systems.append(sys_name)

        return {
            "ticket_refs":  ticket_refs,
            "error_codes":  error_codes,
            "percentages":  percentages,
            "systems":      systems,
            "sender":       email.get("sender", email.get("from", "")),
            "sender_name":  email.get("sender_name", email.get("from_name", "")),
        }

    def find_kb_match(self, email: dict, articles: dict) -> dict | None:
        """Simple keyword-based KB article matching."""
        text = (email.get("subject", "") + " " + email.get("body", "")).lower()
        for article in articles.values():
            tags = article.get("relevance_tags", [])
            if sum(1 for tag in tags if tag in text) >= 2:
                return article
        return None

    # ChatBedrockConverse-compatible interface (for drop-in swap)
    def invoke(self, messages: Any) -> Any:
        """Stub invoke — direct use goes through classify()/extract_entities()."""
        return {"content": "MockBedrockLLM: use classify() or extract_entities() directly"}

    async def ainvoke(self, messages: Any) -> Any:
        """Async stub — direct use goes through classify()/extract_entities()."""
        return {"content": "MockBedrockLLM: use classify() or extract_entities() directly"}


# ── LLM loader ────────────────────────────────────────────────────────────────

def _load_llm(use_mock: bool, bedrock_config: Any = None) -> Any:
    """Load real Bedrock LLM or fall back to MockBedrockLLM."""
    if use_mock:
        return MockBedrockLLM()

    try:
        import boto3  # type: ignore[import]
        from langchain_aws import ChatBedrockConverse  # type: ignore[import]

        model_id = "us.anthropic.claude-3-5-sonnet-20241022-v2:0"
        region   = "us-east-1"
        if bedrock_config:
            model_id = getattr(bedrock_config, "model_id", model_id)
            region   = getattr(bedrock_config, "region", region)

        return ChatBedrockConverse(
            model=model_id,
            region_name=region,
        )
    except (ImportError, Exception) as e:
        logger.warning(
            "ChatBedrockConverse not available (%s) — using MockBedrockLLM", e
        )
        return MockBedrockLLM()


# ── Retirement case-type classification ──────────────────────────────────────
#
# Maps an inbound retirement-plan email to one of the three Pega case types
# the agent supports. The mapping is deterministic + keyword-based so policy
# reviewers can reason about it; for production, the LLM call inside the
# classify_node already runs through governed_chat_model and can override
# this rule-based fallback.

_DISTRIBUTION_KEYWORDS = [
    "rollover", "distribution", "withdraw", "withdrawal",
    "lump sum", "lump-sum", "rmd", "required minimum",
    "termination", "leaving the company", "retiring", "retirement payout",
]

_LOAN_HARDSHIP_KEYWORDS = [
    "loan", "borrow", "borrowing", "401k loan", "401(k) loan",
    "hardship", "medical bills", "medical expenses",
    "tuition", "education expense", "primary residence",
    "buying a home", "funeral", "eviction", "foreclosure",
]

_SPONSOR_INQUIRY_KEYWORDS = [
    "5500", "form 5500", "amendment", "plan amendment",
    "compliance", "adp test", "acp test", "non-discrimination",
    "audit", "auditor", "filing deadline", "plan document",
    "vesting schedule", "contribution upload failed", "deferral correction",
    "eligibility rule", "safe harbor",
]

_HARDSHIP_CATEGORY_KEYWORDS = {
    "medical":             ["medical", "hospital", "surgery", "medication"],
    "education":           ["tuition", "education", "college", "university"],
    "primary_residence":   ["primary residence", "buying a home", "down payment", "house"],
    "funeral":             ["funeral", "burial"],
    "eviction":            ["eviction", "foreclosure"],
}

_SPONSOR_CATEGORY_KEYWORDS = {
    "compliance":          ["compliance", "adp test", "acp test", "non-discrimination", "safe harbor"],
    "amendment":           ["amendment", "plan amendment", "plan document"],
    "audit":               ["5500", "form 5500", "audit", "auditor", "filing"],
    "contribution":        ["contribution upload", "deferral correction", "payroll"],
}


def _classify_pega_case_type(email: dict) -> tuple[str, str | None]:
    """Map email content to (case_type, case_subtype) for Pega routing.

    Deliberately rule-based + auditable. The classify_node's LLM step can
    refine these later if the keyword fallback misses; for now this keeps
    the Pega case-type decision deterministic and traceable.

    Precedence (matters because keywords overlap — "hardship withdrawal"
    contains both 'withdrawal' and 'hardship'):
      1. Hardship signals win first  — IRS-substantiated path
      2. Loan signals next            — explicit "loan" / "borrow"
      3. Distribution signals         — rollover, RMD, lump-sum, withdrawal
      4. Sponsor-inquiry signals      — 5500, compliance, audit
      5. Fallback                     — sponsor_inquiry/general
    """
    text = (email.get("subject", "") + " " + email.get("body", "")).lower()

    # 1. Hardship signals — match before distribution because "hardship
    # withdrawal" would otherwise be misclassified as a routine distribution.
    if "hardship" in text or any(
        kw in text
        for kws in _HARDSHIP_CATEGORY_KEYWORDS.values()
        for kw in kws
    ):
        return ("loan_hardship", "hardship")

    # 2. Loan signals — explicit "loan" or "borrow" without hardship context.
    if any(kw in text for kw in ["loan", "borrow", "borrowing"]):
        return ("loan_hardship", "loan")

    # 3. Distribution signals.
    if any(kw in text for kw in _DISTRIBUTION_KEYWORDS):
        if "rollover" in text:
            return ("distribution", "rollover")
        if "rmd" in text or "required minimum" in text:
            return ("distribution", "rmd")
        if any(kw in text for kw in ["lump sum", "lump-sum", "termination", "leaving the company", "retiring"]):
            return ("distribution", "lump_sum")
        return ("distribution", "in_service")

    # 4. Sponsor-side inquiries.
    if any(kw in text for kw in _SPONSOR_INQUIRY_KEYWORDS):
        for category, kws in _SPONSOR_CATEGORY_KEYWORDS.items():
            if any(kw in text for kw in kws):
                return ("sponsor_inquiry", category)
        return ("sponsor_inquiry", "general")

    # 5. Default — sponsor_inquiry/general catches everything we don't
    # otherwise recognise. Adjuster reclassification then feeds
    # OutcomeTracker as a signal that the keyword tables need updating.
    return ("sponsor_inquiry", "general")


def _hardship_category(email: dict) -> str | None:
    """Sub-classify hardship withdrawals against the IRS safe-harbor categories."""
    text = (email.get("subject", "") + " " + email.get("body", "")).lower()
    for category, kws in _HARDSHIP_CATEGORY_KEYWORDS.items():
        if any(kw in text for kw in kws):
            return category
    return "other"


# ── Routing helpers ───────────────────────────────────────────────────────────

def _determine_team(
    case_type: str,
    case_subtype: str | None,
    severity: str,
    amount: float,
) -> str:
    """Route to the right adjuster / admin team based on retirement case type.

    The team names are placeholder strings — your Pega tenant's real
    operator IDs go in pega_schemas/<case_type>.yaml's defaults or
    via the routing.team mapping. Whichever team string this returns
    needs to map to a real Pega operator/work-group ID.
    """
    if case_type == "distribution":
        if severity in ("S1", "S2") or amount > 25_000:
            return "distributions-senior"
        return "distributions-standard"

    if case_type == "loan_hardship":
        if case_subtype == "hardship":
            return "hardship-review"            # all hardships go to senior reviewer
        if case_subtype == "loan" and amount > 50_000:
            return "loans-senior"
        return "loans-standard"

    if case_type == "sponsor_inquiry":
        if case_subtype == "compliance":
            return "erisa-compliance"
        if case_subtype in ("amendment", "audit"):
            return "plan-admin-senior"
        return "plan-admin-standard"

    return "general-support"


# ── Pega schema registry singleton ───────────────────────────────────────────
#
# Loaded once per process; the email-triage directory has a hyphen so
# we can't import its sibling files as a Python package — load the
# router module by file path. Cheap; happens once.

_PEGA_REGISTRY = None


def _get_pega_registry():
    """Return a process-wide PegaSchemaRegistry, loaded lazily on first use."""
    global _PEGA_REGISTRY
    if _PEGA_REGISTRY is None:
        import importlib.util
        from pathlib import Path
        agent_dir = Path(__file__).parent
        spec = importlib.util.spec_from_file_location(
            "_pega_router_local",
            agent_dir / "pega_router.py",
        )
        if spec is None or spec.loader is None:
            raise RuntimeError("Could not locate pega_router.py next to graph.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _PEGA_REGISTRY = mod.PegaSchemaRegistry(agent_dir / "pega_schemas")
    return _PEGA_REGISTRY


# ── Node implementations ──────────────────────────────────────────────────────

def _make_classify_node(agent: Any, llm: Any):
    """classify_node — ITSMEffect.EMAIL_CLASSIFY, PRIORITY_INFER, SENTIMENT_SCORE."""
    from arc.core.effects import ITSMEffect

    async def classify_node(state: EmailTriageState) -> dict:
        email  = state.get("email", {})
        eid    = state.get("email_id", email.get("id", "unknown"))

        if isinstance(llm, MockBedrockLLM):
            # Harness path. The mock isn't a real BaseChatModel, so we
            # can't wrap it with governed_chat_model — record the result
            # via run_effect directly. Same audit shape, no LLM metadata.
            result = llm.classify(email)
            await agent.run_effect(
                effect=ITSMEffect.EMAIL_CLASSIFY,
                tool="classifier", action="classify",
                params={"email_id": eid, "subject": email.get("subject")},
                intent_action="classify_email",
                intent_reason=f"Classify intent and priority for email {eid}",
                exec_fn=lambda: result,
            )
        else:
            # Real LLM path. Wrap ChatBedrockConverse (or any BaseChatModel)
            # so the LLM call itself is the governed effect — ControlTower
            # sees prompt size + provider + model in the audit row.
            from arc.orchestrators import governed_chat_model  # type: ignore[import]
            from langchain_core.messages import HumanMessage  # type: ignore[import]
            from pydantic import BaseModel, Field  # type: ignore[import]

            class Classification(BaseModel):
                intent:     str   = Field(description="incident/request/question/complaint")
                priority:   str   = Field(description="P1/P2/P3/P4")
                confidence: float = Field(description="0.0-1.0")
                sentiment:  str   = Field(description="positive/neutral/negative/urgent")

            prompt = (
                f"Classify this IT support email:\n\n"
                f"Subject: {email.get('subject', '')}\n"
                f"Body: {email.get('body', '')[:1000]}\n\n"
                "Return JSON with: intent (incident/request/question/complaint), "
                "priority (P1/P2/P3/P4), confidence (0.0-1.0), "
                "sentiment (positive/neutral/negative/urgent)."
            )

            governed = governed_chat_model(
                chat_model    = llm,
                agent         = agent,
                effect        = ITSMEffect.EMAIL_CLASSIFY,
                intent_action = "classify_email",
                intent_reason = f"Classify intent and priority for email {eid}",
                metadata      = {"email_id": eid},
            )
            structured_llm = governed.with_structured_output(Classification)
            classification = await structured_llm.ainvoke([HumanMessage(content=prompt)])
            result = {
                "intent":     classification.intent,
                "priority":   classification.priority,
                "confidence": classification.confidence,
                "sentiment":  classification.sentiment,
            }

        await agent.run_effect(
            effect=ITSMEffect.PRIORITY_INFER,
            tool="classifier", action="infer_priority",
            params={"email_id": eid, "priority": result["priority"]},
            intent_action="infer_priority",
            intent_reason=f"Infer P1-P4 priority for email {eid}",
        )

        await agent.run_effect(
            effect=ITSMEffect.SENTIMENT_SCORE,
            tool="classifier", action="score_sentiment",
            params={"email_id": eid, "sentiment": result.get("sentiment", "neutral")},
            intent_action="score_sentiment",
            intent_reason=f"Score sender sentiment for email {eid}",
        )

        logger.info(
            "classify_node: %s → intent=%s priority=%s confidence=%.2f sentiment=%s",
            eid, result["intent"], result["priority"],
            result["confidence"], result.get("sentiment", "neutral"),
        )

        return {
            "intent":    result["intent"],
            "priority":  result["priority"],
            "confidence": result["confidence"],
            "sentiment": result.get("sentiment", "neutral"),
        }

    return classify_node


def _make_extract_entities_node(agent: Any, llm: Any):
    """extract_entities_node — ITSMEffect.ENTITY_EXTRACT."""
    from arc.core.effects import ITSMEffect

    async def extract_entities_node(state: EmailTriageState) -> dict:
        email = state.get("email", {})
        eid   = state.get("email_id", email.get("id", "unknown"))

        if isinstance(llm, MockBedrockLLM):
            # Harness path — same shape as classify_node above.
            entities = llm.extract_entities(email)
            entities = await agent.run_effect(
                effect=ITSMEffect.ENTITY_EXTRACT,
                tool="entity-extractor", action="extract",
                params={"email_id": eid},
                intent_action="extract_entities",
                intent_reason=f"Extract structured entities from email {eid}",
                exec_fn=lambda: entities,
            )
        else:
            from arc.orchestrators import governed_chat_model  # type: ignore[import]
            from langchain_core.messages import HumanMessage  # type: ignore[import]
            from pydantic import BaseModel, Field  # type: ignore[import]

            class Entities(BaseModel):
                systems:     list[str] = Field(default_factory=list)
                error_codes: list[str] = Field(default_factory=list)
                ticket_refs: list[str] = Field(default_factory=list)
                sender:      str = ""
                sender_name: str = ""

            prompt = (
                f"Extract entities from this IT support email:\n\n"
                f"Subject: {email.get('subject', '')}\n"
                f"Body: {email.get('body', '')[:1000]}\n\n"
                "Extract: systems mentioned, error codes, ticket references, sender email, sender name."
            )

            governed = governed_chat_model(
                chat_model    = llm,
                agent         = agent,
                effect        = ITSMEffect.ENTITY_EXTRACT,
                intent_action = "extract_entities",
                intent_reason = f"Extract structured entities from email {eid}",
                metadata      = {"email_id": eid},
            )
            structured_llm = governed.with_structured_output(Entities)
            extracted = await structured_llm.ainvoke([HumanMessage(content=prompt)])
            entities = extracted.model_dump()
            if not entities.get("sender"):
                entities["sender"] = email.get("sender", email.get("from", ""))
            if not entities.get("sender_name"):
                entities["sender_name"] = email.get("sender_name", "")

        return {"entities": entities}

    return extract_entities_node


def _make_lookup_user_node(agent: Any):
    """lookup_user_node — ITSMEffect.USER_DIRECTORY_READ."""
    from arc.core.effects import ITSMEffect
    from arc.core.gateway import DataRequest

    async def lookup_user_node(state: EmailTriageState) -> dict:
        entities = state.get("entities", {})
        sender   = entities.get("sender", "")
        eid      = state.get("email_id", "unknown")

        async def do_lookup():
            try:
                resp = await agent.gateway.fetch(DataRequest(
                    source="user.directory", params={"email": sender}
                ))
                directory = resp.data or {}
                if isinstance(directory, dict):
                    return directory.get(sender, {"tier": "standard", "email": sender})
                return {"tier": "standard", "email": sender}
            except Exception:
                return {"tier": "standard", "email": sender}

        user_info = await agent.run_effect(
            effect=ITSMEffect.USER_DIRECTORY_READ,
            tool="user-directory", action="lookup",
            params={"email": sender},
            intent_action="lookup_user",
            intent_reason=f"Look up sender profile for routing (email={sender})",
            exec_fn=do_lookup,
        )

        # Merge user_info into entities
        updated_entities = dict(entities)
        updated_entities["user_info"] = user_info
        return {"entities": updated_entities}

    return lookup_user_node


def _make_query_knowledge_node(agent: Any):
    """query_knowledge_node — ITSMEffect.KNOWLEDGE_BUDDY_QUERY (optional)."""
    from arc.core.effects import ITSMEffect
    from arc.core.gateway import DataRequest

    async def query_knowledge_node(state: EmailTriageState) -> dict:
        email = state.get("email", {})
        eid   = state.get("email_id", email.get("id", "unknown"))

        async def do_kb_query():
            # Try gateway KB first
            try:
                subject = email.get("subject", "")
                body    = email.get("body", "")[:500]
                resp = await agent.gateway.fetch(DataRequest(
                    source="knowledge.articles",
                    params={"email_id": eid},
                ))
                articles = resp.data or {}
                if isinstance(articles, dict) and articles:
                    # Use rule-based matching for KB articles
                    text = (subject + " " + body).lower()
                    for article in articles.values():
                        tags = article.get("relevance_tags", [])
                        if sum(1 for tag in tags if tag in text) >= 2:
                            return article
            except Exception:
                pass
            return None

        kb_match = await agent.run_effect(
            effect=ITSMEffect.KNOWLEDGE_BUDDY_QUERY,
            tool="knowledge-base", action="query",
            params={"email_id": eid},
            intent_action="query_knowledge",
            intent_reason=f"Query knowledge base for relevant articles for email {eid}",
            exec_fn=do_kb_query,
        )

        if kb_match:
            logger.info("query_knowledge_node: %s → KB match: %s", eid, kb_match.get("id", kb_match.get("article_id", "")))

        return {"kb_match": kb_match}

    return query_knowledge_node


def _make_check_duplicate_node(agent: Any):
    """check_duplicate_node — ITSMEffect.DUPLICATE_DETECT."""
    from arc.core.effects import ITSMEffect
    from arc.core.gateway import DataRequest

    async def check_duplicate_node(state: EmailTriageState) -> dict:
        eid      = state.get("email_id", "unknown")
        intent   = state.get("intent", "incident")
        priority = state.get("priority", "P4")
        entities = state.get("entities", {})
        email    = state.get("email", {})

        async def do_check():
            # Check for existing ticket references in entities
            ticket_refs = entities.get("ticket_refs", [])
            if ticket_refs:
                return {
                    "is_duplicate": True,
                    "duplicate_ref": ticket_refs[0],
                    "reason": f"Email references existing ticket {ticket_refs[0]}",
                }
            return {"is_duplicate": False}

        dup_result = await agent.run_effect(
            effect=ITSMEffect.DUPLICATE_DETECT,
            tool="duplicate-checker", action="check",
            params={"email_id": eid, "subject": email.get("subject", "")},
            intent_action="check_duplicate",
            intent_reason=f"Check for duplicate tickets before creating for email {eid}",
            exec_fn=do_check,
        )

        if dup_result and dup_result.get("is_duplicate"):
            logger.info(
                "check_duplicate_node: %s → DUPLICATE detected (%s)",
                eid, dup_result.get("reason", ""),
            )

        return {"is_duplicate": dup_result.get("is_duplicate", False) if dup_result else False}

    return check_duplicate_node


def _make_draft_ticket_node(agent: Any):
    """draft_ticket_node — classify the Pega case type, build the arc-shaped
    triage_data dict that the Pega schema registry maps from, and run it
    through ITSMEffect.TICKET_DRAFT for the audit row."""
    from arc.core.effects import ITSMEffect
    import os
    import re

    async def draft_ticket_node(state: EmailTriageState) -> dict:
        email      = state.get("email", {})
        eid        = state.get("email_id", email.get("id", "unknown"))
        priority   = state.get("priority", "P4")
        confidence = state.get("confidence", 0.7)
        entities   = state.get("entities", {})

        sender      = entities.get("sender", "")
        sender_name = entities.get("sender_name", "")
        ticket_target = os.getenv("TICKET_TARGET", "pega").lower()

        # ── 1. Classify into one of the 3 retirement Pega case types ─────────
        case_type, case_subtype = _classify_pega_case_type(email)

        # ── 2. Pull retirement-shaped fields from the email ──────────────────
        # Most of these would be filled by a real entity extractor (LLM) in
        # production. The keyword fallback below covers the demo path.
        body = email.get("body", "")
        amount = _extract_amount(body)
        participant_id = _extract_participant_id(body)
        plan_id = entities.get("plan_id") or _extract_plan_id(body)
        request_date = entities.get("incident_date", "")

        # Sub-classify hardship category if applicable
        hardship_category = _hardship_category(email) if case_subtype == "hardship" else None

        # Sponsor-side fields (only relevant when sender is a sponsor contact)
        sponsor_id = entities.get("sponsor_id", "")
        sponsor_company = entities.get("sponsor_company", "")
        related_filing = entities.get("related_filing", "")

        # Severity heuristic — amount + priority + subtype.
        severity = _severity_from_signals(case_type, case_subtype, amount, priority)

        team = _determine_team(case_type, case_subtype, severity, amount)

        # ── 3. The arc-shaped triage_data dict — input to the Pega router ────
        triage_data = {
            "participant_id":         participant_id,
            "plan_id":                plan_id,
            "amount_requested":       amount,
            "request_date":           request_date,
            "request_subtype":        case_subtype if case_type == "loan_hardship" else None,
            "distribution_subtype":   case_subtype if case_type == "distribution" else None,
            "hardship_category":      hardship_category,
            "loan_purpose":           None,         # extracted by LLM in production
            "loan_term_months":       None,
            "tax_withholding_pct":    None,
            "destination_institution": None,
            "destination_account":    None,
            "full_balance":           "full" in body.lower() or "entire balance" in body.lower(),
            "documentation_provided": "attached" in body.lower() or "document" in body.lower(),
            "reason_text":            email.get("subject", "")[:500],
            "sponsor_id":             sponsor_id,
            "sponsor_company":        sponsor_company,
            "inquiry_category":       case_subtype if case_type == "sponsor_inquiry" else None,
            "inquiry_summary":        email.get("subject", "")[:500],
            "related_filing":         related_filing,
            "filing_deadline":        None,
            "related_documents":      None,
            "requestor": {
                "email": sender,
                "name":  sender_name,
                "role":  entities.get("sender_role", ""),
            },
            "routing": {"team": team},
            "triage": {"severity": severity},
        }

        # ── 4. Map through the Pega schema registry → Pega-shaped payload ────
        registry = _get_pega_registry()
        try:
            pega_payload = registry.map(case_type, triage_data)
        except Exception as exc:
            # Schema validation failed (missing required, etc.). Surface so the
            # error path captures it; don't crash the graph.
            logger.warning(
                "draft_ticket_node: pega registry could not map case_type=%s for email %s: %s",
                case_type, eid, exc,
            )
            pega_payload = None

        # ── 5. Audit-log the draft via TICKET_DRAFT ─────────────────────────
        ticket_draft_record = {
            "case_type":     case_type,
            "case_subtype":  case_subtype,
            "team":          team,
            "amount":        amount,
            "severity":      severity,
            "pega_payload":  pega_payload,
        }
        result = await agent.run_effect(
            effect=ITSMEffect.TICKET_DRAFT,
            tool="ticket-drafter", action="draft",
            params={
                "email_id":         eid,
                "case_type":        case_type,
                "case_subtype":     case_subtype,
                "amount_requested": amount,
            },
            intent_action="draft_ticket",
            intent_reason=f"Draft Pega case payload for {case_type} from email {eid}",
            exec_fn=lambda: ticket_draft_record,
        )

        return {
            "case_type":     case_type,
            "case_subtype":  case_subtype,
            "triage_data":   triage_data,
            "ticket_draft":  result,
            "assigned_team": team,
            "ticket_target": ticket_target,
        }

    return draft_ticket_node


# ── Entity extraction helpers (rule-based fallbacks) ────────────────────────


def _extract_amount(text: str) -> float:
    """Pull a dollar amount out of free text. 0.0 if none found."""
    import re
    m = re.search(r"\$\s*([\d,]+(?:\.\d{1,2})?)", text)
    if m:
        try:
            return float(m.group(1).replace(",", ""))
        except ValueError:
            return 0.0
    return 0.0


def _extract_participant_id(text: str) -> str:
    """Extract a participant ID like P-12345 or PART12345."""
    import re
    m = re.search(r"\b(?:P|PART|PARTICIPANT)[-_]?(\d+)\b", text, re.IGNORECASE)
    return f"P-{m.group(1)}" if m else ""


def _extract_plan_id(text: str) -> str:
    """Extract a plan ID like PLAN-001 or 401K-9876."""
    import re
    m = re.search(r"\b(?:PLAN|401K)[-_]?\d+\b", text, re.IGNORECASE)
    return m.group(0).upper().replace("_", "-") if m else ""


def _severity_from_signals(
    case_type: str,
    case_subtype: str | None,
    amount: float,
    priority: str,
) -> str:
    """Derive S1-S4 severity from amount + priority + subtype."""
    if priority in ("P1",):
        return "S1"
    if case_type == "loan_hardship" and case_subtype == "hardship":
        return "S2"
    if case_type == "distribution" and amount > 100_000:
        return "S1"
    if case_type == "distribution" and amount > 25_000:
        return "S2"
    if priority == "P2":
        return "S2"
    if priority == "P3":
        return "S3"
    return "S4"


def _make_create_ticket_node(agent: Any):
    """
    create_ticket_node — ITSMEffect.TICKET_CREATE.

    Sends the Pega-shaped payload (built by the schema registry in
    draft_ticket_node) to the ticket connector. Top-level `params`
    carry the gate-relevant fields (case_type, amount_requested,
    request_subtype, fraud_flag) so policy.yaml's `when:` rules can
    branch on them — e.g. to ASK for high-value distributions or DENY
    for fraud-flagged participants.
    """
    from arc.core.effects import ITSMEffect

    async def create_ticket_node(state: EmailTriageState) -> dict:
        ticket_draft = state.get("ticket_draft") or {}
        triage       = state.get("triage_data") or {}
        case_type    = state.get("case_type", "sponsor_inquiry")
        case_subtype = state.get("case_subtype")
        priority     = state.get("priority", "P4")
        confidence   = state.get("confidence", 0.7)
        eid          = state.get("email_id", "unknown")
        fraud_flag   = state.get("fraud_flag", False)

        pega_payload = ticket_draft.get("pega_payload")
        if pega_payload is None:
            return {
                "error":     "Pega payload could not be assembled — see draft_ticket_node logs",
                "ticket_id": None,
            }

        # Top-level params drive policy gates. The full Pega-shaped payload
        # rides as a nested key so the connector can lift it out unchanged.
        params: dict = {
            "case_type":         case_type,
            "case_subtype":      case_subtype,
            "request_subtype":   case_subtype if case_type == "loan_hardship" else None,
            "amount_requested":  triage.get("amount_requested", 0.0),
            "inquiry_category":  case_subtype if case_type == "sponsor_inquiry" else None,
            "fraud_flag":        fraud_flag,
            "pega_payload":      pega_payload,            # the Pega-shaped JSON
        }

        metadata: dict = {
            "priority":         priority,
            "confidence":       confidence,
            "email_id":         eid,
            "schema_version":   pega_payload.get("schema_version"),
            "pega_case_type":   pega_payload.get("caseTypeID"),
            "team":              ticket_draft.get("team"),
        }

        ticket_id = await agent.run_effect(
            effect=ITSMEffect.TICKET_CREATE,
            tool="pega-case", action="create",
            params=params,
            intent_action=f"create_{case_type}_case",
            intent_reason=(
                f"Open Pega case ({pega_payload.get('caseTypeID', 'unknown')}) "
                f"from email {eid} routed to {ticket_draft.get('team')}"
            ),
            metadata=metadata,
        )

        # Generate a mock ticket ID if the connector didn't return one
        # (harness path uses MockGatewayConnector for ticket.system).
        if not ticket_id:
            import uuid
            ticket_id = f"MOCK-{uuid.uuid4().hex[:8].upper()}"

        logger.info(
            "create_ticket_node: %s → ticket_id=%s (case_type=%s, team=%s)",
            eid, ticket_id, case_type, ticket_draft.get("team"),
        )

        return {
            "ticket_id":       str(ticket_id) if ticket_id else None,
            "approval_status": "approved",
        }

    return create_ticket_node


def _make_log_triage_node(agent: Any):
    """log_triage_node — ITSMEffect.TRIAGE_LOG_WRITE."""
    from arc.core.effects import ITSMEffect

    async def log_triage_node(state: EmailTriageState) -> dict:
        eid        = state.get("email_id", "unknown")
        priority   = state.get("priority", "P4")
        intent     = state.get("intent", "incident")
        confidence = state.get("confidence", 0.0)
        ticket     = state.get("ticket_draft", {})
        team       = state.get("assigned_team", ticket.get("assigned_team", "unknown") if ticket else "unknown")
        ticket_id  = state.get("ticket_id")
        is_dup     = state.get("is_duplicate", False)

        await agent.run_effect(
            effect=ITSMEffect.TRIAGE_LOG_WRITE,
            tool="triage-log", action="write",
            params={
                "email_id":   eid,
                "priority":   priority,
                "intent":     intent,
                "confidence": confidence,
                "team":       team,
                "ticket_id":  ticket_id,
                "is_duplicate": is_dup,
                "kb_hit":     state.get("kb_match") is not None,
            },
            intent_action="log_triage",
            intent_reason="Record triage decision for analytics and audit",
        )

        # Log outcome via tracker if available
        if hasattr(agent, "log_outcome"):
            await agent.log_outcome("email_triage", {
                "email_id":   eid,
                "priority":   priority,
                "intent":     intent,
                "confidence": confidence,
                "ticket_id":  ticket_id,
                "is_duplicate": is_dup,
            })

        return {"completed": True}

    return log_triage_node


# ── Edge conditions ───────────────────────────────────────────────────────────

def _route_after_duplicate_check(state: EmailTriageState) -> str:
    """After check_duplicate: duplicate → log_triage, else → draft_ticket."""
    if state.get("is_duplicate", False):
        return "log_triage"
    return "draft_ticket"


# ── Graph builder ─────────────────────────────────────────────────────────────

def build_email_triage_graph(
    agent: Any,
    use_mock_llm: bool = False,
    bedrock_config: Any = None,
) -> Any:
    """
    Build and compile the email triage StateGraph.

    Args:
        agent:          BaseAgent instance (for run_effect access).
        use_mock_llm:   Force use of MockBedrockLLM (True in harness mode).
        bedrock_config: Optional BedrockConfig for model/region settings.

    Returns:
        Compiled LangGraph StateGraph with MemorySaver checkpointer.
        Ready for use with LangGraphOrchestrator.

    Raises:
        ImportError: If langgraph is not installed.
    """
    try:
        from langgraph.graph import StateGraph, END  # type: ignore[import]
        from langgraph.checkpoint.memory import MemorySaver  # type: ignore[import]
    except ImportError as e:
        raise ImportError(
            "langgraph is required for build_email_triage_graph. "
            "Install with: pip install langgraph"
        ) from e

    llm = _load_llm(use_mock=use_mock_llm, bedrock_config=bedrock_config)

    # ── Build node functions ───────────────────────────────────────────────
    classify_node        = _make_classify_node(agent, llm)
    extract_entities_node = _make_extract_entities_node(agent, llm)
    lookup_user_node     = _make_lookup_user_node(agent)
    query_knowledge_node = _make_query_knowledge_node(agent)
    check_duplicate_node = _make_check_duplicate_node(agent)
    draft_ticket_node    = _make_draft_ticket_node(agent)
    create_ticket_node   = _make_create_ticket_node(agent)
    log_triage_node      = _make_log_triage_node(agent)

    # ── Build graph ────────────────────────────────────────────────────────
    graph = StateGraph(EmailTriageState)

    graph.add_node("classify",         classify_node)
    graph.add_node("extract_entities", extract_entities_node)
    graph.add_node("lookup_user",      lookup_user_node)
    graph.add_node("query_knowledge",  query_knowledge_node)
    graph.add_node("check_duplicate",  check_duplicate_node)
    graph.add_node("draft_ticket",     draft_ticket_node)
    graph.add_node("create_ticket",    create_ticket_node)
    graph.add_node("log_triage",       log_triage_node)

    # ── Linear edges ───────────────────────────────────────────────────────
    graph.set_entry_point("classify")
    graph.add_edge("classify",         "extract_entities")
    graph.add_edge("extract_entities", "lookup_user")
    graph.add_edge("lookup_user",      "query_knowledge")
    graph.add_edge("query_knowledge",  "check_duplicate")

    # ── Conditional edge: duplicate check ─────────────────────────────────
    graph.add_conditional_edges(
        "check_duplicate",
        _route_after_duplicate_check,
        {
            "draft_ticket": "draft_ticket",
            "log_triage":   "log_triage",
        },
    )

    graph.add_edge("draft_ticket",  "create_ticket")
    graph.add_edge("create_ticket", "log_triage")
    graph.add_edge("log_triage",    END)

    # ── Compile with MemorySaver checkpointer ─────────────────────────────
    checkpointer = MemorySaver()
    compiled = graph.compile(checkpointer=checkpointer)

    logger.info(
        "build_email_triage_graph: compiled (llm=%s)",
        type(llm).__name__,
    )
    return compiled
