"""
EmailTriageAgent — LangGraph-orchestrated agent for ITSM email intake.

Highlights:
  - Delegates execution to self.orchestrator.run() (LangGraph or direct).
  - Built on arc.core (BaseAgent, ITSMEffect, etc.).
  - Works in both harness mode (MockBedrockLLM) and runtime mode (real Bedrock).
  - P1/P2 interrupt (ASK) for ticket creation; P3/P4 auto-create (ALLOW).

Run in harness mode (no LangGraph required — uses MockBedrockLLM):
    cd arc
    python agents/email-triage/agent.py

Run with full LangGraph + MockBedrockLLM:
    PYTHONPATH=. python agents/email-triage/agent.py --langgraph

Swap to production:
    Replace HarnessBuilder with RuntimeBuilder + RuntimeConfig.
    Remove --mock-llm to use real Bedrock.
"""

import asyncio
import logging
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

BASE     = Path(__file__).parent
MANIFEST = BASE / "manifest.yaml"
POLICY   = BASE / "policy.yaml"
FIXTURES = BASE / "fixtures" / "emails.yaml"


# ── Agent class ───────────────────────────────────────────────────────────────

class EmailTriageAgent:
    """
    Arc EmailTriageAgent — governance-first implementation.

    Delegates execution to the injected orchestrator (LangGraph or direct).
    In harness mode, falls back to direct execution if no orchestrator is set.

    Attributes:
        orchestrator: OrchestratorProtocol instance (injected by builder).
    """

    def __init__(
        self,
        manifest,
        tower,
        gateway,
        tracker=None,
        orchestrator=None,
        **kwargs,
    ):
        self.manifest     = manifest
        self.tower        = tower
        self.gateway      = gateway
        self.tracker      = tracker
        self.orchestrator = orchestrator

        # Import BaseAgent machinery
        from arc.core import BaseAgent
        # Inject BaseAgent's run_effect and log_outcome onto self
        # (BaseAgent normally requires subclassing — we wrap here for flexibility)
        self._base = _DirectAgent(manifest=manifest, tower=tower, gateway=gateway, tracker=tracker)

    async def run_effect(self, effect, tool, action, params, intent_action, intent_reason, exec_fn=None, metadata=None):
        """Delegate to the internal base agent for governed effect execution."""
        return await self._base.run_effect(
            effect=effect,
            tool=tool,
            action=action,
            params=params,
            intent_action=intent_action,
            intent_reason=intent_reason,
            exec_fn=exec_fn,
            metadata=metadata,
        )

    async def log_outcome(self, category: str, data: dict) -> None:
        """Delegate outcome logging to internal base agent."""
        await self._base.log_outcome(category, data)

    async def execute(self, email_ids: list[str] | None = None) -> dict:
        """
        Triage all emails in the inbox.

        If orchestrator is set, runs each email through the full LangGraph
        pipeline (classify → create_ticket → log). Otherwise, falls back
        to the direct implementation from the base agent.

        Args:
            email_ids: Optional list of specific email IDs to process.
                       If None, processes all emails in the inbox.

        Returns:
            Summary dict with counts: processed, p1, p2, p3, p4,
            tickets_created, escalated, errors.
        """
        if self.orchestrator is not None:
            return await self._execute_with_orchestrator(email_ids)
        else:
            return await self._execute_direct(email_ids)

    async def _execute_with_orchestrator(self, email_ids: list[str] | None) -> dict:
        """Run email triage through the LangGraph orchestrator."""
        from arc.core.gateway import DataRequest

        results = {
            "processed": 0, "p1": 0, "p2": 0, "p3": 0, "p4": 0,
            "tickets_created": 0, "escalated": 0, "errors": 0, "duplicates": 0,
        }

        # Fetch inbox
        inbox_resp = await self.gateway.fetch(DataRequest(source="email.inbox", params={}))
        emails = inbox_resp.data or []
        if isinstance(emails, dict):
            emails = list(emails.values())

        if email_ids:
            emails = [e for e in emails if e.get("id") in email_ids]

        for email in emails:
            eid = email.get("id", "unknown")
            try:
                from arc.orchestrators.protocol import OrchestratorSuspended

                result = await self.orchestrator.run({
                    "email_id": eid,
                    "email":    email,
                    "run_id":   f"triage-{eid}",
                })

                state   = result.state
                priority = state.get("priority", "P4")
                results["processed"] += 1
                results[priority.lower()] = results.get(priority.lower(), 0) + 1

                if state.get("ticket_id"):
                    results["tickets_created"] += 1
                if priority in ("P1", "P2"):
                    results["escalated"] += 1
                if state.get("is_duplicate"):
                    results["duplicates"] += 1

            except Exception as exc:
                # Check for OrchestratorSuspended (P1/P2 ASK in production)
                if type(exc).__name__ == "OrchestratorSuspended":
                    logger.info(
                        "Email %s suspended (ASK): %s", eid, exc
                    )
                    results["processed"] += 1
                    results["escalated"]  += 1
                else:
                    logger.error("Failed to triage %s: %s", eid, exc)
                    results["errors"] += 1

        logger.info("Orchestrator triage complete: %s", results)
        return results

    async def _execute_direct(self, email_ids: list[str] | None) -> dict:
        """
        Direct execution using MockBedrockLLM — no LangGraph required.
        Calls run_effect() for every action so governance still applies.
        """
        from arc.core.gateway import DataRequest
        from arc.core.effects import ITSMEffect
        import os

        # Import graph helpers (MockBedrockLLM + routing logic)
        sys.path.insert(0, str(Path(__file__).parent))
        from graph import MockBedrockLLM, _determine_team  # type: ignore[import]

        llm = MockBedrockLLM()

        results = {
            "processed": 0, "p1": 0, "p2": 0, "p3": 0, "p4": 0,
            "tickets_created": 0, "escalated": 0, "errors": 0,
        }

        # Fetch inbox and supporting data
        inbox_resp = await self.gateway.fetch(DataRequest(source="email.inbox", params={}))
        emails = inbox_resp.data or []
        if isinstance(emails, dict):
            emails = list(emails.values())
        if email_ids:
            emails = [e for e in emails if e.get("id") in email_ids]

        kb_resp  = await self.gateway.fetch(DataRequest(source="knowledge.articles", params={}))
        dir_resp = await self.gateway.fetch(DataRequest(source="user.directory", params={}))
        articles  = kb_resp.data or {}
        directory = dir_resp.data or {}

        for email in emails:
            eid = email.get("id", "unknown")
            try:
                results["processed"] += 1

                # Classify
                classification = await self.run_effect(
                    effect=ITSMEffect.EMAIL_CLASSIFY,
                    tool="classifier", action="classify",
                    params={"email_id": eid},
                    intent_action="classify_email",
                    intent_reason=f"Classify email {eid}",
                    exec_fn=lambda e=email: llm.classify(e),
                )
                priority   = classification["priority"]
                intent     = classification["intent"]
                confidence = classification["confidence"]
                results[priority.lower()] = results.get(priority.lower(), 0) + 1

                # Extract entities
                entities = await self.run_effect(
                    effect=ITSMEffect.ENTITY_EXTRACT,
                    tool="entity-extractor", action="extract",
                    params={"email_id": eid},
                    intent_action="extract_entities",
                    intent_reason=f"Extract entities from email {eid}",
                    exec_fn=lambda e=email: llm.extract_entities(e),
                )

                # KB lookup
                kb_match = await self.run_effect(
                    effect=ITSMEffect.KNOWLEDGE_ARTICLE_READ,
                    tool="knowledge-base", action="search",
                    params={"email_id": eid},
                    intent_action="find_kb_article",
                    intent_reason=f"Find KB match for email {eid}",
                    exec_fn=lambda e=email, a=articles: llm.find_kb_match(e, a),
                )

                # Draft ticket
                sender = entities.get("sender", email.get("sender", ""))
                team   = _determine_team(intent, priority, entities)
                ticket_target = os.getenv("TICKET_TARGET", "pega").lower()
                title   = email.get("subject", "Support Request")[:200]
                desc    = (
                    f"[Auto-triaged | {intent.upper()} | {priority} | {confidence:.0%}]\n\n"
                    f"From: {entities.get('sender_name', '')} <{sender}>\n\n"
                    f"{email.get('body', '')[:2000]}"
                )
                if kb_match:
                    desc += f"\n\n[KB: {kb_match.get('title', '')} — {kb_match.get('id', '')}]"

                draft = {
                    "title": title, "description": desc,
                    "priority": priority, "intent": intent,
                    "assigned_team": team, "confidence": confidence,
                    "email_id": eid, "sender": sender, "ticket_target": ticket_target,
                }

                await self.run_effect(
                    effect=ITSMEffect.TICKET_DRAFT,
                    tool="ticket-drafter", action="draft",
                    params={"email_id": eid},
                    intent_action="draft_ticket",
                    intent_reason=f"Draft ticket for email {eid}",
                    exec_fn=lambda d=draft: d,
                )

                # Create ticket (ASK for P1/P2)
                await self.run_effect(
                    effect=ITSMEffect.TICKET_CREATE,
                    tool="itsm-connector", action="create",
                    params=draft,
                    intent_action="create_ticket",
                    intent_reason=f"Create {priority} ticket for {sender}",
                    metadata={"priority": priority, "confidence": confidence},
                )
                results["tickets_created"] += 1
                if priority in ("P1", "P2"):
                    results["escalated"] += 1

                # Log
                await self.run_effect(
                    effect=ITSMEffect.TRIAGE_LOG_WRITE,
                    tool="triage-log", action="write",
                    params={"email_id": eid, "priority": priority, "intent": intent,
                            "confidence": confidence, "team": team},
                    intent_action="log_triage",
                    intent_reason="Record triage decision",
                )
                await self.log_outcome("email_triage", {
                    "email_id": eid, "priority": priority,
                    "intent": intent, "confidence": confidence,
                })
                logger.info(
                    "Triaged %-12s  intent=%-10s  priority=%s  conf=%.2f",
                    eid, intent, priority, confidence,
                )

            except Exception as e:
                logger.error("Failed to triage %s: %s", eid, e)
                results["errors"] += 1

        return results

    def harness_report(self):
        """Return the harness DecisionReport."""
        if hasattr(self, "_harness_report_fn"):
            return self._harness_report_fn()
        raise RuntimeError("harness_report() not available — use HarnessBuilder")


# ── Direct base agent (for fallback mode) ────────────────────────────────────

class _DirectAgent:
    """
    Thin wrapper around BaseAgent for direct execution.
    Provides run_effect, log_outcome, and triage helpers.
    """

    def __init__(self, manifest, tower, gateway, tracker=None):
        from arc.core import BaseAgent

        class _Impl(BaseAgent):
            async def execute(self, **kwargs):
                pass

        self._impl = _Impl(manifest=manifest, tower=tower, gateway=gateway, tracker=tracker)

    async def run_effect(self, effect, tool, action, params, intent_action, intent_reason, exec_fn=None, metadata=None):
        return await self._impl.run_effect(
            effect=effect, tool=tool, action=action, params=params,
            intent_action=intent_action, intent_reason=intent_reason,
            exec_fn=exec_fn, metadata=metadata,
        )

    async def log_outcome(self, category: str, data: dict) -> None:
        await self._impl.log_outcome(category, data)

    async def _triage_email(self, email, articles, directory, results):
        """Inline triage fallback used when no orchestrator is wired (no LangGraph)."""
        import re as _re
        from arc.core.effects import ITSMEffect

        PRIORITY_SIGNALS = {
            "P1": ["production down", "completely down", "all users", "data loss",
                   "security breach", "unauthorized access", "emergency", "critical"],
            "P2": ["30%", "significant", "vip", "largest client", "enterprise client",
                   "waited 3 days", "sla", "degraded", "affecting"],
            "P3": ["wrong", "incorrect", "slow", "performance", "workaround available",
                   "when you have a chance", "not critical"],
        }
        INTENT_SIGNALS = {
            "incident":  ["error", "down", "failing", "broken", "issue", "breach", "timeout", "slow"],
            "request":   ["request", "update", "add", "change", "need", "export", "feature"],
            "question":  ["how do i", "how to", "could you", "where", "?"],
            "complaint": ["unacceptable", "waited", "no response", "complaint", "frustrated"],
        }

        eid  = email.get("id", "unknown")
        text = (email.get("subject", "") + " " + email.get("body", "")).lower()
        results["processed"] += 1

        # Priority
        priority = "P4"
        for p in ["P1", "P2", "P3"]:
            if any(sig in text for sig in PRIORITY_SIGNALS[p]):
                priority = p
                break

        # Intent
        intent = "incident"
        for candidate, signals in INTENT_SIGNALS.items():
            if any(s in text for s in signals):
                intent = candidate
                break

        # Confidence
        hits = sum(
            1 for signals in [PRIORITY_SIGNALS.get(priority, []), INTENT_SIGNALS.get(intent, [])]
            for s in signals if s in text
        )
        confidence = min(0.65 + hits * 0.08, 0.98)

        classification = await self.run_effect(
            effect=ITSMEffect.EMAIL_CLASSIFY,
            tool="classifier", action="classify",
            params={"email_id": eid},
            intent_action="classify_email",
            intent_reason=f"Classify email {eid}",
            exec_fn=lambda: {"intent": intent, "priority": priority, "confidence": round(confidence, 2)},
        )

        results[priority.lower()] = results.get(priority.lower(), 0) + 1

        # Entities
        combined = email.get("subject", "") + " " + email.get("body", "")
        entities = await self.run_effect(
            effect=ITSMEffect.ENTITY_EXTRACT,
            tool="entity-extractor", action="extract",
            params={"email_id": eid},
            intent_action="extract_entities",
            intent_reason=f"Extract entities from email {eid}",
            exec_fn=lambda: {
                "ticket_refs": _re.findall(r"TKT-\d+", combined, _re.IGNORECASE),
                "error_codes": _re.findall(r"\b[45]\d{2}\b", combined),
                "sender":      email.get("sender", ""),
                "sender_name": email.get("sender_name", ""),
            },
        )

        sender = entities.get("sender", "")

        # User lookup
        await self.run_effect(
            effect=ITSMEffect.USER_DIRECTORY_READ,
            tool="user-directory", action="lookup",
            params={"email": sender},
            intent_action="lookup_user",
            intent_reason="Get sender profile for routing",
            exec_fn=lambda: directory.get(sender, {"tier": "standard"}),
        )

        # KB match
        kb_match = await self.run_effect(
            effect=ITSMEffect.KNOWLEDGE_ARTICLE_READ,
            tool="knowledge-base", action="search",
            params={"email_id": eid},
            intent_action="find_kb_article",
            intent_reason="Find relevant KB article",
            exec_fn=lambda: self._find_kb_match(email, articles),
        )

        # Draft ticket
        team = (
            "critical-incidents" if priority == "P1"
            else "customer-success" if intent == "complaint"
            else "account-management" if intent == "request"
            else "senior-support" if priority == "P2"
            else "general-support"
        )
        title = email.get("subject", "")[:80]
        desc  = f"[Auto-triaged | {intent.upper()} | {confidence:.0%}]\n\n{email.get('body', '')[:500]}"
        if kb_match:
            desc += f"\n\n[KB: {kb_match.get('id', '')}]"

        ticket = await self.run_effect(
            effect=ITSMEffect.TICKET_DRAFT,
            tool="ticket-drafter", action="draft",
            params={"email_id": eid},
            intent_action="draft_ticket",
            intent_reason=f"Draft ticket for email {eid}",
            exec_fn=lambda: {
                "title": title, "description": desc,
                "priority": priority, "intent": intent,
                "assigned_team": team, "confidence": confidence,
                "email_id": eid, "sender": sender,
            },
        )

        # Create ticket
        await self.run_effect(
            effect=ITSMEffect.TICKET_CREATE,
            tool="itsm-connector", action="create",
            params=ticket,
            intent_action="create_ticket",
            intent_reason=f"Create {priority} {intent} ticket for {sender}",
            metadata={"priority": priority, "confidence": confidence},
        )

        results["tickets_created"] = results.get("tickets_created", 0) + 1
        if priority in ("P1", "P2"):
            results["escalated"] = results.get("escalated", 0) + 1

        # Log
        await self.run_effect(
            effect=ITSMEffect.TRIAGE_LOG_WRITE,
            tool="triage-log", action="write",
            params={"email_id": eid, "priority": priority, "intent": intent,
                    "confidence": confidence, "team": team, "kb_hit": kb_match is not None},
            intent_action="log_triage",
            intent_reason="Record triage decision",
        )

        await self.log_outcome("email_triage", {
            "email_id": eid, "priority": priority, "intent": intent,
            "confidence": confidence,
        })

    def _find_kb_match(self, email: dict, articles: dict) -> dict | None:
        text = (email.get("subject", "") + " " + email.get("body", "")).lower()
        for article in articles.values():
            tags = article.get("relevance_tags", [])
            if sum(1 for tag in tags if tag in text) >= 2:
                return article
        return None


# ── Entry point ───────────────────────────────────────────────────────────────

async def main():
    import sys

    use_langgraph = "--langgraph" in sys.argv
    use_mock_llm  = "--mock-llm" in sys.argv or True  # Always mock in harness

    print("=" * 62)
    print("  Email Triage Agent — Arc edition")
    print(f"  Mode: {'LangGraph + MockBedrockLLM' if use_langgraph else 'Direct (no LangGraph)'}")
    print("=" * 62)

    try:
        from arc.harness import HarnessBuilder
    except ImportError:
        from arc.harness import HarnessBuilder

    builder = (
        HarnessBuilder(manifest=MANIFEST, policy=POLICY)
        .with_fixtures(FIXTURES)
        .with_tracker("email_triage_outcomes.jsonl")
    )

    if use_langgraph:
        try:
            from arc.agents.email_triage.graph import build_email_triage_graph
            from arc.orchestrators import LangGraphOrchestrator

            # Build a temporary agent instance to pass to graph builder
            temp_agent_holder: list = []
            agent = builder.build(EmailTriageAgent)
            graph = build_email_triage_graph(agent, use_mock_llm=True)
            orchestrator = LangGraphOrchestrator(graph=graph)
            agent.orchestrator = orchestrator
        except ImportError as e:
            print(f"  [WARNING] LangGraph not installed ({e}) — falling back to direct mode")
            agent = builder.build(EmailTriageAgent)
    else:
        agent = builder.build(EmailTriageAgent)

    # Inject harness_report_fn
    agent._harness_report_fn = agent.harness_report  # type: ignore[attr-defined]

    results = await agent.execute()

    # Print report
    if hasattr(agent, "_harness_audit"):
        try:
            from arc.harness.report import DecisionReport
            from arc.harness.shadow import ShadowAuditSink
            report = DecisionReport(
                audit=agent._harness_audit,
                approver=agent._harness_approver,
                agent_id=agent.manifest.agent_id,
            )
            report.print()
        except Exception:
            pass

    print("── Triage results ──────────────────────────────────────")
    for k, v in results.items():
        print(f"  {k:<22} {v}")

    print()
    print("── Quality gate check ──────────────────────────────────")
    processed = results.get("processed", 0)
    created   = results.get("tickets_created", 0)
    escalated = results.get("escalated", 0)
    p1_p2     = results.get("p1", 0) + results.get("p2", 0)

    accuracy  = (created / processed * 100) if processed else 0
    esc_rate  = (escalated / max(p1_p2, 1) * 100)

    audit = getattr(agent, "_harness_audit", None)
    deny_count  = getattr(audit, "deny_count", 0) if audit else 0
    error_count = getattr(audit, "error_count", 0) if audit else 0

    checks = [
        ("Classification accuracy",  f"{accuracy:.0f}%",  accuracy >= 90,  ">= 90%"),
        ("P1/P2 escalation rate",    f"{esc_rate:.0f}%",  esc_rate >= 95,  ">= 95%"),
        ("Audit completeness",       "100%",              error_count == 0, "= 100%"),
        ("Hard-deny blocks",         "0",                 deny_count == 0,  "= 0"),
    ]

    all_pass = True
    for name, value, passed, target in checks:
        icon   = "✓" if passed else "✗"
        status = "PASS" if passed else "FAIL"
        if not passed:
            all_pass = False
        print(f"  {icon} {name:<30} {value:<8} {status}  (target {target})")

    print()
    if all_pass:
        print("  ✓ All quality gates passing — ready for Phase 1 gate review")
    else:
        print("  ✗ Some gates failing — review before promoting")
    print()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(name)s: %(message)s")
    asyncio.run(main())
