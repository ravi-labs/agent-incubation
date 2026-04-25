"""
Contract Review Agent — Example Implementation

Demonstrates agent-foundry governance using LegalEffect (UPL taxonomy).

This agent:
  1. Reads contract documents from the repository (Tier 1 — ALLOW)
  2. Reads the playbook and relevant regulations (Tier 1 — ALLOW)
  3. Extracts parties, obligations, and key dates (Tier 2 — ALLOW)
  4. Scores each clause for risk against the playbook (Tier 2 — ALLOW)
  5. Drafts redlines for high-risk clauses (Tier 3 — ALLOW)
  6. Drafts a contract summary memo (Tier 3 — ALLOW)
  7. Routes high-risk contracts to attorney review queue (Tier 4 — ALLOW)
  8. Sends review summary to the requesting business unit (Tier 4 — ASK for high-risk)
  9. Attempts a hard-deny effect to demonstrate unconditional blocking

Key governance properties shown:
  - LegalEffect enum used throughout (UPL-grounded vocabulary)
  - ASK pattern: high-risk contracts suspend until attorney approves summary send
  - Hard-deny: legal.advice.render is blocked unconditionally
  - Manifest declared scope: only listed effects can be invoked
  - Audit JSONL: every decision recorded for legal hold compliance

Run:
    cd agent-foundry
    python examples/contract_review/agent.py
"""

import asyncio
import logging
from pathlib import Path

from arc.core.gateway import MockGatewayConnector
from arc.core.gateway import DataRequest
from arc.core.observability import OutcomeTracker
from arc.core.effects import LegalEffect
from arc.core import BaseAgent, load_manifest
from tollgate import (
    ApprovalOutcome,
    ControlTower,
    JsonlAuditSink,
    YamlPolicyEvaluator,
)


class _SandboxApprover:
    """Always-approve approver for sandbox / demo runs.

    In production, swap this for CliApprover (interactive) or
    AsyncQueueApprover (workflow-integrated) so that ASK decisions
    are routed to a real attorney reviewer.
    """

    async def request_approval_async(self, _agent_ctx, _intent, _tool_request, _hash, _reason):
        return ApprovalOutcome.APPROVED

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

POLICY_PATH   = Path(__file__).parent / "policy.yaml"
MANIFEST_PATH = Path(__file__).parent / "manifest.yaml"


# ─── Synthetic Contract Data (sandbox) ───────────────────────────────────────

SAMPLE_CONTRACTS = {
    "contract-001": {
        "id":          "contract-001",
        "type":        "MSA",
        "counterparty": "Acme Corp",
        "requestor":   "sales-team",
        "clauses": [
            {
                "id":    "cl-001",
                "label": "Limitation of Liability",
                "text":  "Liability of either party shall not exceed $50,000 in aggregate.",
                "type":  "liability_cap",
            },
            {
                "id":    "cl-002",
                "label": "Indemnification",
                "text":  "Customer shall indemnify Company against all third-party claims.",
                "type":  "indemnification",
            },
            {
                "id":    "cl-003",
                "label": "Governing Law",
                "text":  "This agreement shall be governed by the laws of Delaware.",
                "type":  "governing_law",
            },
            {
                "id":    "cl-004",
                "label": "Auto-Renewal",
                "text":  "Agreement auto-renews annually unless 30-day written notice given.",
                "type":  "term",
            },
        ],
        "effective_date": "2026-04-01",
        "term_months":    24,
    },
    "contract-002": {
        "id":          "contract-002",
        "type":        "NDA",
        "counterparty": "Beta Partners LLC",
        "requestor":   "bd-team",
        "clauses": [
            {
                "id":    "cl-005",
                "label": "Confidentiality Obligation",
                "text":  "Recipient shall protect Discloser's confidential information using "
                         "reasonable care for a period of 3 years.",
                "type":  "confidentiality",
            },
            {
                "id":    "cl-006",
                "label": "Permitted Disclosure",
                "text":  "Recipient may disclose to affiliates without restriction.",
                "type":  "disclosure",
            },
        ],
        "effective_date": "2026-04-15",
        "term_months":    12,
    },
    "contract-003": {
        "id":          "contract-003",
        "type":        "SOW",
        "counterparty": "Gamma Consulting Inc.",
        "requestor":   "engineering-team",
        "clauses": [
            {
                "id":    "cl-007",
                "label": "IP Assignment",
                "text":  "All work product shall remain the property of Contractor.",
                "type":  "ip_ownership",
            },
            {
                "id":    "cl-008",
                "label": "Payment Terms",
                "text":  "Net-60 payment terms; late fees of 3% per month.",
                "type":  "payment",
            },
            {
                "id":    "cl-009",
                "label": "Non-Solicitation",
                "text":  "Customer agrees not to hire Contractor's employees for 5 years.",
                "type":  "non_solicitation",
            },
        ],
        "effective_date": "2026-05-01",
        "term_months":    6,
    },
}

# Simplified playbook: maps clause types to acceptable terms and risk flags
PLAYBOOK = {
    "liability_cap": {
        "preferred_minimum_usd": 1_000_000,
        "risk_note": "Caps below $1M are high-risk for enterprise MSAs",
    },
    "indemnification": {
        "mutual_required": True,
        "risk_note": "One-sided indemnification is high-risk",
    },
    "governing_law": {
        "preferred_states": ["Delaware", "New York", "California"],
        "risk_note": "Non-preferred jurisdictions need GC review",
    },
    "term": {
        "max_auto_renewal_notice_days": 60,
        "risk_note": "Notice period < 60 days creates operational risk",
    },
    "confidentiality": {
        "min_protection_years": 3,
        "risk_note": "Protection < 3 years may be insufficient",
    },
    "disclosure": {
        "affiliate_restriction_required": True,
        "risk_note": "Unrestricted affiliate disclosure is medium-risk",
    },
    "ip_ownership": {
        "company_must_own": True,
        "risk_note": "Contractor-retained IP is high-risk for SOWs",
    },
    "payment": {
        "max_net_days": 45,
        "max_late_fee_pct": 1.5,
        "risk_note": "Net-60+ or late fees > 1.5% are medium-risk",
    },
    "non_solicitation": {
        "max_years": 2,
        "risk_note": "Non-solicitation > 2 years may be unenforceable",
    },
}


# ─── Clause Risk Logic ────────────────────────────────────────────────────────

def score_clause_risk(clause: dict, playbook: dict) -> dict:
    """Score a contract clause against the playbook for risk."""
    ctype   = clause["type"]
    rules   = playbook.get(ctype, {})
    text    = clause["text"].lower()
    risks   = []
    level   = "low"

    if ctype == "liability_cap":
        # Detect whether cap is below preferred minimum
        import re
        amounts = re.findall(r"\$[\d,]+", text)
        if amounts:
            amount = int(amounts[0].replace("$", "").replace(",", ""))
            if amount < rules.get("preferred_minimum_usd", 1_000_000):
                risks.append(f"Liability cap ${amount:,} is below preferred minimum "
                             f"${rules['preferred_minimum_usd']:,}")
                level = "high"

    elif ctype == "indemnification":
        if "shall indemnify" in text and "mutual" not in text:
            risks.append("One-sided indemnification — mutual language missing")
            level = "high"

    elif ctype == "governing_law":
        preferred = [s.lower() for s in rules.get("preferred_states", [])]
        if not any(s in text for s in preferred):
            risks.append("Governing law is a non-preferred jurisdiction")
            level = "medium"

    elif ctype == "term":
        import re
        days = re.findall(r"(\d+)-day", text)
        if days:
            notice = int(days[0])
            if notice < rules.get("max_auto_renewal_notice_days", 60):
                risks.append(f"Auto-renewal notice period {notice} days is below {rules['max_auto_renewal_notice_days']} days")
                level = "medium"

    elif ctype == "ip_ownership":
        if "contractor" in text and "property of contractor" in text:
            risks.append("IP ownership retained by contractor — company will not own work product")
            level = "high"

    elif ctype == "payment":
        import re
        net_days = re.findall(r"net-(\d+)", text)
        late_fees = re.findall(r"([\d.]+)%", text)
        if net_days and int(net_days[0]) > rules.get("max_net_days", 45):
            risks.append(f"Net-{net_days[0]} exceeds preferred Net-{rules['max_net_days']}")
            level = "medium"
        if late_fees and float(late_fees[0]) > rules.get("max_late_fee_pct", 1.5):
            risks.append(f"Late fee {late_fees[0]}%/month exceeds preferred {rules['max_late_fee_pct']}%")
            level = "medium"

    elif ctype == "non_solicitation":
        import re
        years = re.findall(r"(\d+)\s+year", text)
        if years and int(years[0]) > rules.get("max_years", 2):
            risks.append(f"Non-solicitation period {years[0]} years may be unenforceable")
            level = "medium"

    elif ctype == "disclosure":
        if "without restriction" in text:
            risks.append("Unrestricted affiliate disclosure — scope not bounded")
            level = "medium"

    return {
        "clause_id":  clause["id"],
        "clause_label": clause["label"],
        "clause_type": ctype,
        "risk_level": level,
        "risks":       risks,
        "playbook_note": rules.get("risk_note", ""),
    }


def aggregate_contract_risk(clause_scores: list[dict]) -> dict:
    """Aggregate clause-level scores into a contract-level risk assessment."""
    high_count   = sum(1 for s in clause_scores if s["risk_level"] == "high")
    medium_count = sum(1 for s in clause_scores if s["risk_level"] == "medium")
    score = high_count * 3 + medium_count * 1

    flagged_clauses = [s for s in clause_scores if s["risk_level"] in ("high", "medium")]

    return {
        "score":           score,
        "risk_level":      "high" if high_count > 0 else ("medium" if medium_count > 0 else "low"),
        "high_risk_count": high_count,
        "medium_risk_count": medium_count,
        "flagged_clauses": flagged_clauses,
    }


def extract_entities(contract: dict) -> dict:
    """Extract key metadata from a contract record."""
    return {
        "contract_id":   contract["id"],
        "contract_type": contract["type"],
        "counterparty":  contract["counterparty"],
        "requestor":     contract["requestor"],
        "effective_date": contract["effective_date"],
        "term_months":   contract["term_months"],
        "clause_count":  len(contract["clauses"]),
    }


def draft_redlines(contract: dict, clause_scores: list[dict]) -> list[dict]:
    """Draft suggested redlines for high-risk clauses."""
    redlines = []
    for score in clause_scores:
        if score["risk_level"] != "high":
            continue
        ctype = score["clause_type"]
        suggestion = {
            "clause_id":    score["clause_id"],
            "clause_label": score["clause_label"],
            "risk_level":   "high",
            "risks":        score["risks"],
        }
        # Add playbook-driven suggested language
        if ctype == "liability_cap":
            suggestion["suggested_language"] = (
                "Liability of either party shall not exceed the greater of "
                "$1,000,000 or the total fees paid in the 12 months preceding "
                "the event giving rise to the claim."
            )
        elif ctype == "indemnification":
            suggestion["suggested_language"] = (
                "Each party shall indemnify, defend, and hold harmless the other "
                "party from and against any third-party claims arising from such "
                "party's own negligence or wilful misconduct."
            )
        elif ctype == "ip_ownership":
            suggestion["suggested_language"] = (
                "All work product, deliverables, and intellectual property created "
                "by Contractor in connection with this SOW shall be deemed works "
                "made for hire and shall be the sole property of Customer."
            )
        else:
            suggestion["suggested_language"] = (
                "[Attorney to supply revised language consistent with playbook]"
            )
        redlines.append(suggestion)
    return redlines


def draft_review_summary(contract: dict, entities: dict, risk: dict, redlines: list[dict]) -> dict:
    """Draft a contract review summary for the requesting business unit."""
    flagged_lines = "\n".join(
        f"  • [{s['risk_level'].upper()}] {s['clause_label']}: {'; '.join(s['risks'])}"
        for s in risk["flagged_clauses"]
    )
    redline_note = (
        f"\n{len(redlines)} high-risk clause(s) have been redlined for attorney review."
        if redlines else ""
    )

    body = (
        f"CONTRACT REVIEW SUMMARY\n"
        f"{'─' * 40}\n"
        f"Contract: {entities['contract_id']} ({entities['contract_type']})\n"
        f"Counterparty: {entities['counterparty']}\n"
        f"Effective Date: {entities['effective_date']}\n"
        f"Term: {entities['term_months']} months\n\n"
        f"Overall Risk: {risk['risk_level'].upper()} "
        f"(score: {risk['score']}, {risk['high_risk_count']} high, "
        f"{risk['medium_risk_count']} medium)\n\n"
        f"Flagged Clauses:\n{flagged_lines if flagged_lines else '  None'}"
        f"{redline_note}\n\n"
        f"This review was prepared by the Contract Review Agent and is subject to "
        f"attorney review before acting on its contents. This is not legal advice."
    )

    return {
        "contract_id":  contract["id"],
        "requestor":    entities["requestor"],
        "channel":      "internal-portal",
        "body":         body,
        "risk_level":   risk["risk_level"],
        "redline_count": len(redlines),
    }


# ─── Agent Implementation ──────────────────────────────────────────────────────

class ContractReviewAgent(BaseAgent):
    """
    Contract Review Agent.

    For each contract, this agent:
      1. Reads the contract document (LegalEffect — Tier 1)
      2. Reads the playbook and applicable regulations (Tier 1 — ALLOW)
      3. Extracts entities and key metadata (Tier 2 — ALLOW)
      4. Scores each clause for risk (Tier 2 — ALLOW)
      5. Drafts redlines for high-risk clauses (Tier 3 — ALLOW)
      6. Drafts a review summary memo (Tier 3 — ALLOW)
      7. Routes high-risk contracts to attorney review queue (Tier 4 — ALLOW)
      8. Sends review summary to business unit (Tier 4 — ASK for high-risk)
      9. Demonstrates hard-deny blocking of legal.advice.render
    """

    async def execute(self, contract_ids: list[str], demo_hard_deny: bool = False) -> dict:
        results = {
            "processed":           0,
            "high_risk":           0,
            "medium_risk":         0,
            "low_risk":            0,
            "total_flags":         0,
            "total_redlines":      0,
            "escalated_to_review": 0,
            "summaries_sent":      0,
            "errors":              0,
        }

        for cid in contract_ids:
            try:
                await self._process_contract(cid, results)
            except Exception as e:
                logger.error("Failed to process contract %s: %s", cid, e)
                results["errors"] += 1

        # ── Hard-deny demonstration ──────────────────────────────────────────
        # Attempt to render legal advice — unconditionally blocked regardless
        # of the manifest or policy, demonstrating the hard-deny layer.
        if demo_hard_deny:
            await self._demo_hard_deny()

        logger.info("Run complete: %s", results)
        return results

    async def _process_contract(self, cid: str, results: dict) -> None:
        results["processed"] += 1

        # Step 1 — Read contract document (Tier 1: ALLOW)
        c_resp = await self.gateway.fetch(DataRequest(
            source="contract.repository",
            params={"contract_id": cid},
        ))
        contract = c_resp.data.get(cid)
        if not contract:
            logger.warning("Contract %s not found", cid)
            return

        # Step 2 — Read playbook (Tier 1: ALLOW)
        pb_resp = await self.gateway.fetch(DataRequest(
            source="playbook.library",
            params={},
        ))
        playbook = pb_resp.data

        # Step 3 — Extract entities (Tier 2: ALLOW)
        entities = await self.run_effect(
            effect=LegalEffect.ENTITY_EXTRACTION_RUN,
            tool="entity_extractor",
            action="extract",
            params={"contract_id": cid},
            intent_action="extract_entities",
            intent_reason=f"Extract parties, dates, and obligations from contract {cid}",
            exec_fn=lambda: extract_entities(contract),
        )

        logger.info(
            "Contract %s — type: %s, counterparty: %s, clauses: %d",
            cid, entities["contract_type"], entities["counterparty"], entities["clause_count"],
        )

        # Step 4 — Score clause risk (Tier 2: ALLOW)
        clause_scores = await self.run_effect(
            effect=LegalEffect.CLAUSE_RISK_SCORE,
            tool="risk_scorer",
            action="score",
            params={"contract_id": cid, "clauses": contract["clauses"]},
            intent_action="score_clause_risk",
            intent_reason=f"Score all clauses in contract {cid} against playbook",
            exec_fn=lambda: [score_clause_risk(cl, playbook) for cl in contract["clauses"]],
        )

        # Step 5 — Aggregate to contract-level risk (Tier 2: ALLOW)
        risk = await self.run_effect(
            effect=LegalEffect.COMPLIANCE_GAP_IDENTIFY,
            tool="risk_aggregator",
            action="aggregate",
            params={"contract_id": cid, "clause_scores": clause_scores},
            intent_action="aggregate_contract_risk",
            intent_reason=f"Compute overall risk level for contract {cid}",
            exec_fn=lambda: aggregate_contract_risk(clause_scores),
        )

        logger.info(
            "Contract %s — risk: %s (score: %d, high: %d, medium: %d)",
            cid, risk["risk_level"], risk["score"],
            risk["high_risk_count"], risk["medium_risk_count"],
        )

        results[f"{risk['risk_level']}_risk"] += 1
        results["total_flags"] += len(risk["flagged_clauses"])

        # Step 6 — Draft redlines for high-risk clauses (Tier 3: ALLOW)
        redlines = await self.run_effect(
            effect=LegalEffect.REDLINE_DRAFT,
            tool="redline_drafter",
            action="draft",
            params={"contract_id": cid, "clause_scores": clause_scores},
            intent_action="draft_redlines",
            intent_reason=f"Draft redlines for high-risk clauses in contract {cid}",
            exec_fn=lambda: draft_redlines(contract, clause_scores),
        )

        results["total_redlines"] += len(redlines)

        if redlines:
            logger.info(
                "Contract %s — %d clause(s) redlined", cid, len(redlines),
            )

        # Step 7 — Draft review summary (Tier 3: ALLOW)
        summary = await self.run_effect(
            effect=LegalEffect.CONTRACT_SUMMARY_DRAFT,
            tool="summary_drafter",
            action="draft",
            params={"contract_id": cid},
            intent_action="draft_review_summary",
            intent_reason=f"Draft review summary memo for contract {cid}",
            exec_fn=lambda: draft_review_summary(contract, entities, risk, redlines),
        )

        if risk["risk_level"] == "high":
            # Step 8a — Route to attorney review queue (Tier 4: ALLOW)
            # High-risk contracts must pass through human review before summary
            # is sent. Adding to queue is always permitted; the ASK gate fires
            # on review.summary.send when risk_level == "high".
            await self.run_effect(
                effect=LegalEffect.HUMAN_REVIEW_QUEUE_ADD,
                tool="review_queue",
                action="add",
                params={
                    "contract_id": cid,
                    "reason":      "High-risk contract — attorney review required before send",
                    "summary":     summary,
                    "risk":        risk,
                    "redlines":    redlines,
                },
                intent_action="escalate_to_review",
                intent_reason=f"Escalate high-risk contract {cid} for attorney review",
            )
            results["escalated_to_review"] += 1
            logger.info("Contract %s escalated to attorney review queue", cid)

        # Step 8b — Send review summary (Tier 4: ALLOW for low/medium, ASK for high)
        # AutoApprover auto-approves ASK decisions in sandbox — in production
        # this would suspend the agent until the reviewing attorney approves.
        await self.run_effect(
            effect=LegalEffect.REVIEW_SUMMARY_SEND,
            tool="internal_portal",
            action="send",
            params=summary,
            intent_action="send_review_summary",
            intent_reason=f"Send contract review summary to {entities['requestor']}",
            metadata={"risk_level": risk["risk_level"]},
        )

        results["summaries_sent"] += 1

        # Step 9 — Log the interaction (Tier 5: ALLOW)
        await self.run_effect(
            effect=LegalEffect.MATTER_INTERACTION_LOG_WRITE,
            tool="interaction_log",
            action="write",
            params={
                "contract_id":  cid,
                "contract_type": entities["contract_type"],
                "risk_level":   risk["risk_level"],
                "flag_count":   len(risk["flagged_clauses"]),
                "redline_count": len(redlines),
                "action":       "review_complete",
            },
            intent_action="log_interaction",
            intent_reason="Record contract review interaction for billing and audit",
        )

        await self.log_outcome("contract_review", {
            "contract_id":  cid,
            "risk_level":   risk["risk_level"],
            "flag_count":   len(risk["flagged_clauses"]),
            "redline_count": len(redlines),
        })

    async def _demo_hard_deny(self) -> None:
        """
        Demonstrate the hard-deny layer.

        LegalEffect.LEGAL_ADVICE_RENDER is on the hard-deny list —
        blocked unconditionally regardless of manifest or policy.
        This shows that even if a developer accidentally adds it to a manifest,
        the framework will raise PermissionError before execution.
        """
        logger.info("\n── Hard-Deny Demonstration ─────────────────────────────")
        logger.info(
            "Attempting LegalEffect.LEGAL_ADVICE_RENDER "
            "(hard-deny — should be blocked unconditionally)..."
        )
        try:
            # Temporarily add to allowed_effects to show the gate still fires
            # (hard-deny runs before manifest check)
            self.manifest.allowed_effects.append(
                LegalEffect.LEGAL_ADVICE_RENDER
            )
            await self.run_effect(
                effect=LegalEffect.LEGAL_ADVICE_RENDER,
                tool="advice_engine",
                action="render",
                params={"contract_id": "contract-001", "question": "Should I sign?"},
                intent_action="render_legal_advice",
                intent_reason="Attempt blocked by hard-deny layer",
            )
        except (PermissionError, Exception) as e:
            logger.info("✓ Hard-deny blocked: %s", e)
        finally:
            if LegalEffect.LEGAL_ADVICE_RENDER in self.manifest.allowed_effects:
                self.manifest.allowed_effects.remove(
                    LegalEffect.LEGAL_ADVICE_RENDER
                )


# ─── Wiring ────────────────────────────────────────────────────────────────────

def build_agent() -> ContractReviewAgent:
    """Wire up the Contract Review Agent with its policy, gateway, and tower."""
    manifest = load_manifest(MANIFEST_PATH)

    policy   = YamlPolicyEvaluator(POLICY_PATH)
    approver = _SandboxApprover()   # swap CliApprover or AsyncQueueApprover for production
    audit    = JsonlAuditSink("contract_review_audit.jsonl")
    tower    = ControlTower(policy=policy, approver=approver, audit=audit)

    gateway = MockGatewayConnector({
        "contract.repository": SAMPLE_CONTRACTS,
        "playbook.library":    PLAYBOOK,
    })

    tracker = OutcomeTracker(path="contract_review_outcomes.jsonl")

    return ContractReviewAgent(
        manifest=manifest,
        tower=tower,
        gateway=gateway,
        tracker=tracker,
    )


async def main():
    print("=" * 60)
    print("Contract Review Agent — agent-foundry demo")
    print("Taxonomy: LegalEffect (UPL / Privilege)")
    print("=" * 60)

    agent = build_agent()

    results = await agent.execute(
        contract_ids=["contract-001", "contract-002", "contract-003"],
        demo_hard_deny=True,
    )

    print("\n── Results ─────────────────────────────────────────────")
    for k, v in results.items():
        print(f"  {k}: {v}")

    if agent.tracker:
        print("\n── Outcome Summary ─────────────────────────────────────")
        print(agent.tracker.summary())

    print("\nAudit log written to: contract_review_audit.jsonl")
    print("Generate HTML report: foundry-audit contract_review_audit.jsonl")


if __name__ == "__main__":
    asyncio.run(main())
