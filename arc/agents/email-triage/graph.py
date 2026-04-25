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
    - Real: ChatBedrockConverse (us.anthropic.claude-3-5-sonnet-20241022-v2:0)
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
    """Full state for the email triage graph."""

    # Input
    email_id:       str
    email:          dict          # raw email from Outlook / fixture
    run_id:         str

    # Classification
    intent:         str           # incident/request/question/complaint
    priority:       str           # P1/P2/P3/P4
    confidence:     float
    sentiment:      str           # positive/neutral/negative/urgent

    # Entities
    entities:       dict          # extracted system, error_code, sender info

    # Knowledge
    kb_match:       dict | None   # Knowledge Buddy result

    # Ticket
    ticket_draft:   dict | None
    ticket_id:      str | None    # created ticket ID

    # Routing
    assigned_team:  str
    ticket_target:  str           # "pega" or "servicenow"

    # Control
    approval_status: str          # "pending" / "approved" / "denied"
    error:           str | None
    completed:       bool
    is_duplicate:    bool         # set by check_duplicate node


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


# ── Routing helpers ───────────────────────────────────────────────────────────

def _determine_team(intent: str, priority: str, entities: dict) -> str:
    """Route to the right team based on classification."""
    if priority == "P1":
        return "critical-incidents"
    systems = entities.get("systems", [])
    if "auth" in systems or any("breach" in s for s in entities.get("systems", [])):
        return "security-team"
    if intent == "complaint":
        return "customer-success"
    if intent == "request":
        return "account-management"
    if priority == "P2":
        return "senior-support"
    return "general-support"


# ── Node implementations ──────────────────────────────────────────────────────

def _make_classify_node(agent: Any, llm: Any):
    """classify_node — ITSMEffect.EMAIL_CLASSIFY, PRIORITY_INFER, SENTIMENT_SCORE."""
    from foundry.policy.itsm_effects import ITSMEffect

    async def classify_node(state: EmailTriageState) -> dict:
        email  = state.get("email", {})
        eid    = state.get("email_id", email.get("id", "unknown"))

        if isinstance(llm, MockBedrockLLM):
            result = llm.classify(email)
        else:
            # Real LLM: structured classification via Bedrock
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
            structured_llm = llm.with_structured_output(Classification)
            classification = await structured_llm.ainvoke([HumanMessage(content=prompt)])
            result = {
                "intent":     classification.intent,
                "priority":   classification.priority,
                "confidence": classification.confidence,
                "sentiment":  classification.sentiment,
            }

        # Run through governance for each classification effect
        classification = await agent.run_effect(
            effect=ITSMEffect.EMAIL_CLASSIFY,
            tool="classifier", action="classify",
            params={"email_id": eid, "subject": email.get("subject")},
            intent_action="classify_email",
            intent_reason=f"Classify intent and priority for email {eid}",
            exec_fn=lambda: result,
        )

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
    from foundry.policy.itsm_effects import ITSMEffect

    async def extract_entities_node(state: EmailTriageState) -> dict:
        email = state.get("email", {})
        eid   = state.get("email_id", email.get("id", "unknown"))

        if isinstance(llm, MockBedrockLLM):
            entities = llm.extract_entities(email)
        else:
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
            structured_llm = llm.with_structured_output(Entities)
            extracted = await structured_llm.ainvoke([HumanMessage(content=prompt)])
            entities = extracted.model_dump()
            if not entities.get("sender"):
                entities["sender"] = email.get("sender", email.get("from", ""))
            if not entities.get("sender_name"):
                entities["sender_name"] = email.get("sender_name", "")

        result = await agent.run_effect(
            effect=ITSMEffect.ENTITY_EXTRACT,
            tool="entity-extractor", action="extract",
            params={"email_id": eid},
            intent_action="extract_entities",
            intent_reason=f"Extract structured entities from email {eid}",
            exec_fn=lambda: entities,
        )

        return {"entities": result}

    return extract_entities_node


def _make_lookup_user_node(agent: Any):
    """lookup_user_node — ITSMEffect.USER_DIRECTORY_READ."""
    from foundry.policy.itsm_effects import ITSMEffect
    from foundry.gateway.base import DataRequest

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
    from foundry.policy.itsm_effects import ITSMEffect
    from foundry.gateway.base import DataRequest

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
    from foundry.policy.itsm_effects import ITSMEffect
    from foundry.gateway.base import DataRequest

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
    """draft_ticket_node — ITSMEffect.TICKET_DRAFT + TICKET_SUMMARY_DRAFT."""
    from foundry.policy.itsm_effects import ITSMEffect
    import os

    async def draft_ticket_node(state: EmailTriageState) -> dict:
        email      = state.get("email", {})
        eid        = state.get("email_id", email.get("id", "unknown"))
        intent     = state.get("intent", "incident")
        priority   = state.get("priority", "P4")
        confidence = state.get("confidence", 0.7)
        entities   = state.get("entities", {})
        kb_match   = state.get("kb_match")

        sender      = entities.get("sender", "")
        sender_name = entities.get("sender_name", "")
        team        = _determine_team(intent, priority, entities)

        ticket_target = os.getenv("TICKET_TARGET", "pega").lower()

        title = email.get("subject", "Support Request")
        if len(title) > 200:
            title = title[:197] + "..."

        description = (
            f"[Auto-triaged | {intent.upper()} | Priority: {priority} | "
            f"Confidence: {confidence:.0%}]\n\n"
            f"From: {sender_name} <{sender}>\n\n"
            f"{email.get('body', '')[:2000]}"
        )

        if kb_match:
            kb_title = kb_match.get("title", kb_match.get("pyArticleTitle", ""))
            kb_id    = kb_match.get("id", kb_match.get("article_id", "KB-000"))
            description += f"\n\n[KB Match: {kb_title} — {kb_id}]"

        draft = {
            "title":         title,
            "description":   description,
            "priority":      priority,
            "intent":        intent,
            "assigned_team": team,
            "confidence":    confidence,
            "email_id":      eid,
            "sender":        sender,
            "ticket_target": ticket_target,
        }

        result = await agent.run_effect(
            effect=ITSMEffect.TICKET_DRAFT,
            tool="ticket-drafter", action="draft",
            params={"email_id": eid},
            intent_action="draft_ticket",
            intent_reason=f"Draft ticket fields from email {eid}",
            exec_fn=lambda: draft,
        )

        await agent.run_effect(
            effect=ITSMEffect.TICKET_SUMMARY_DRAFT,
            tool="ticket-drafter", action="draft_summary",
            params={"email_id": eid},
            intent_action="draft_ticket_summary",
            intent_reason=f"Draft ticket summary for email {eid}",
        )

        return {
            "ticket_draft":  result,
            "assigned_team": team,
            "ticket_target": ticket_target,
        }

    return draft_ticket_node


def _make_create_ticket_node(agent: Any):
    """
    create_ticket_node — ITSMEffect.TICKET_CREATE.

    P1/P2 → ASK (interrupt in production, auto-approved in harness).
    P3/P4 → ALLOW if confidence >= 0.85, else ASK.
    """
    from foundry.policy.itsm_effects import ITSMEffect

    async def create_ticket_node(state: EmailTriageState) -> dict:
        ticket     = state.get("ticket_draft", {})
        priority   = state.get("priority", "P4")
        confidence = state.get("confidence", 0.7)
        eid        = state.get("email_id", "unknown")

        if not ticket:
            return {"error": "No ticket draft available", "ticket_id": None}

        # Build metadata for policy evaluation
        metadata: dict = {
            "priority":   priority,
            "confidence": confidence,
            "email_id":   eid,
        }

        ticket_id = await agent.run_effect(
            effect=ITSMEffect.TICKET_CREATE,
            tool="itsm-connector", action="create",
            params=ticket,
            intent_action="create_ticket",
            intent_reason=(
                f"Create {priority} {ticket.get('intent', 'incident')} ticket "
                f"from {ticket.get('sender', 'unknown')}"
            ),
            metadata=metadata,
        )

        # Generate a mock ticket ID if connector doesn't return one
        if not ticket_id:
            import uuid
            ticket_id = f"MOCK-{uuid.uuid4().hex[:8].upper()}"

        logger.info(
            "create_ticket_node: %s → ticket_id=%s (priority=%s)",
            eid, ticket_id, priority,
        )

        return {
            "ticket_id":      str(ticket_id) if ticket_id else None,
            "approval_status": "approved",
        }

    return create_ticket_node


def _make_log_triage_node(agent: Any):
    """log_triage_node — ITSMEffect.TRIAGE_LOG_WRITE."""
    from foundry.policy.itsm_effects import ITSMEffect

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
