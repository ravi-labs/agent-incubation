"""
Life Event Anticipation Agent — Example Implementation

Detects life events from behavioral signals and routes participants to
the right response path:

  HIGH_CONFIDENCE + COMPLEX → Advisor escalation (human review required)
  HIGH_CONFIDENCE + SIMPLE  → Informational outreach (batch approval)
  LOW_CONFIDENCE            → Watch queue + follow-up in 30 days

Life events detected:
  - JOB_CHANGE:        Employer HR feed + login surge
  - NEW_BABY:          Beneficiary update + allocation shift to conservative
  - APPROACHING_RMD:   Age-based + account activity patterns
  - FINANCIAL_STRESS:  Loan inquiry + hardship signals
  - NEARING_RETIREMENT: Age + balance trajectory + engagement surge

Run this example:
    python examples/life_event_anticipation/agent.py
"""

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from foundry.gateway import MockGatewayConnector
from foundry.gateway.base import DataRequest
from foundry.observability import OutcomeTracker
from foundry.policy.effects import FinancialEffect
from foundry.scaffold import BaseAgent, load_manifest
from foundry.tollgate import (
    AutoApprover,
    ControlTower,
    JsonlAuditSink,
    YamlPolicyEvaluator,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

POLICY_PATH = Path(__file__).parent / "policy.yaml"
MANIFEST_PATH = Path(__file__).parent / "manifest.yaml"

# Score thresholds
HIGH_CONFIDENCE_THRESHOLD = 0.70
ESCALATE_THRESHOLD = 0.85      # Very high confidence → advisor escalation
FOLLOWUP_THRESHOLD = 0.35      # Low confidence → watch queue, follow up in 30 days


# ─── Data Model ───────────────────────────────────────────────────────────────

@dataclass
class LifeEventScore:
    participant_id: str
    event_type: str
    confidence: float
    signals: list[str]
    recommended_action: Literal["OUTREACH", "ESCALATE", "WATCH", "NO_ACTION"]
    message_type: Literal["informational", "advice", "guidance"]


# ─── Sandbox Data ─────────────────────────────────────────────────────────────

SAMPLE_PARTICIPANTS = {
    "p-101": {
        "id": "p-101",
        "name": "Angela",
        "age": 62,
        "balance": 485_000,
        "contrib_rate": 0.10,
        "income": 110_000,
        "login_days_ago": 3,
        "beneficiary_update_days_ago": None,
        "allocation_equity_pct": 0.80,
    },
    "p-102": {
        "id": "p-102",
        "name": "David",
        "age": 34,
        "balance": 42_000,
        "contrib_rate": 0.04,
        "income": 78_000,
        "login_days_ago": 5,
        "beneficiary_update_days_ago": 14,  # Recent beneficiary update
        "allocation_equity_pct": 0.40,  # Shifted conservative
    },
    "p-103": {
        "id": "p-103",
        "name": "Keisha",
        "age": 44,
        "balance": 95_000,
        "contrib_rate": 0.06,
        "income": 92_000,
        "login_days_ago": 2,
        "beneficiary_update_days_ago": None,
        "allocation_equity_pct": 0.72,
    },
}

SAMPLE_EMPLOYER_FEED = {
    "p-101": {"status": "active", "hire_date_days_ago": 5840, "term_pending": False},
    "p-102": {"status": "active", "hire_date_days_ago": 210, "term_pending": False},
    "p-103": {"status": "active", "hire_date_days_ago": 730, "term_pending": True},  # Term pending
}


# ─── Life Event Scoring Logic ─────────────────────────────────────────────────

def score_life_events(participant: dict, employer_data: dict) -> list[LifeEventScore]:
    """Evaluate participant signals for life event probabilities."""
    scores = []
    pid = participant["id"]

    # Signal 1: Approaching retirement (age 60–65 + high engagement)
    if 59 <= participant["age"] <= 66:
        engagement_bonus = 0.15 if participant["login_days_ago"] <= 7 else 0
        confidence = 0.55 + engagement_bonus + max(0, (participant["age"] - 59) * 0.04)
        signals = [f"Age {participant['age']} in retirement window"]
        if participant["login_days_ago"] <= 7:
            signals.append("High recent engagement (login within 7 days)")
        scores.append(LifeEventScore(
            participant_id=pid,
            event_type="NEARING_RETIREMENT",
            confidence=min(confidence, 0.95),
            signals=signals,
            recommended_action="ESCALATE" if confidence >= ESCALATE_THRESHOLD else "OUTREACH",
            message_type="guidance",
        ))

    # Signal 2: New baby (recent beneficiary update + allocation shift to conservative)
    if (
        participant.get("beneficiary_update_days_ago") is not None
        and participant["beneficiary_update_days_ago"] <= 30
        and participant["allocation_equity_pct"] <= 0.50
    ):
        confidence = 0.78
        signals = [
            f"Beneficiary update {participant['beneficiary_update_days_ago']} days ago",
            f"Allocation shifted to {participant['allocation_equity_pct']*100:.0f}% equity (conservative)",
        ]
        scores.append(LifeEventScore(
            participant_id=pid,
            event_type="NEW_BABY",
            confidence=confidence,
            signals=signals,
            recommended_action="OUTREACH",
            message_type="informational",
        ))

    # Signal 3: Job change (employer termination pending or recent hire)
    if employer_data.get("term_pending") or employer_data.get("hire_date_days_ago", 999) <= 90:
        confidence = 0.92 if employer_data.get("term_pending") else 0.72
        signals = []
        if employer_data.get("term_pending"):
            signals.append("Employer HR feed: termination pending")
        if employer_data.get("hire_date_days_ago", 999) <= 90:
            signals.append(f"Recent hire: {employer_data['hire_date_days_ago']} days ago")
        scores.append(LifeEventScore(
            participant_id=pid,
            event_type="JOB_CHANGE",
            confidence=confidence,
            signals=signals,
            recommended_action="ESCALATE" if confidence >= ESCALATE_THRESHOLD else "OUTREACH",
            message_type="guidance",
        ))

    return scores


# ─── Message Drafting ─────────────────────────────────────────────────────────

def draft_outreach(participant: dict, event_score: LifeEventScore) -> dict:
    """Draft a personalized outreach message for a detected life event."""
    name = participant["name"]

    templates = {
        "NEARING_RETIREMENT": {
            "subject": f"Your retirement is getting closer, {name} — let's review your readiness",
            "body": (
                f"Hi {name}, you're entering the home stretch before retirement. "
                f"This is a great time to review your projected income, Social Security "
                f"timing, and withdrawal strategy. "
                f"A quick call with your advisor could make a real difference at this stage."
            ),
        },
        "NEW_BABY": {
            "subject": f"Congratulations on your growing family, {name}",
            "body": (
                f"Hi {name}, we noticed you recently updated your beneficiary designation — "
                f"congratulations on what may be a new addition to your family! "
                f"This is a great time to review your life insurance, emergency fund, "
                f"and contribution rate to stay on track for your goals."
            ),
        },
        "JOB_CHANGE": {
            "subject": f"Important: Your retirement account options during this transition",
            "body": (
                f"Hi {name}, it looks like you may be going through a job change. "
                f"You have important decisions to make about your retirement account: "
                f"you can keep it with your current plan, roll it over to an IRA, "
                f"or move it to a new employer's plan. Each option has different tax "
                f"implications. We'd love to walk you through your options."
            ),
        },
    }

    template = templates.get(event_score.event_type, {
        "subject": f"A quick check-in, {name}",
        "body": f"Hi {name}, we wanted to reach out about your retirement plan.",
    })

    return {
        "participant_id": participant["id"],
        "event_type": event_score.event_type,
        "subject": template["subject"],
        "body": template["body"],
        "message_type": event_score.message_type,
        "channel": "email",
        "confidence": event_score.confidence,
    }


# ─── Agent Implementation ─────────────────────────────────────────────────────

class LifeEventAnticipationAgent(BaseAgent):
    """
    Life Event Anticipation Agent.

    For each participant:
      1. Reads participant data + employer HR feed via Gateway
      2. Scores life event probabilities
      3. Routes: ESCALATE → advisor, OUTREACH → personalized email, WATCH → follow-up queue
    """

    async def execute(self, participant_ids: list[str]) -> dict:
        results = {
            "participants_scanned": 0,
            "events_detected": 0,
            "outreach_sent": 0,
            "advisor_escalations": 0,
            "watch_queue": 0,
            "errors": 0,
        }

        for pid in participant_ids:
            try:
                await self._process_participant(pid, results)
            except Exception as e:
                logger.error("Failed to process participant %s: %s", pid, e)
                results["errors"] += 1

        logger.info("Life event scan complete: %s", results)
        return results

    async def _process_participant(self, pid: str, results: dict) -> None:
        results["participants_scanned"] += 1

        # Step 1: Fetch participant data
        p_resp = await self.gateway.fetch(DataRequest(
            source="participant.data",
            params={"participant_id": pid},
        ))
        participant = p_resp.data.get(pid)
        if not participant:
            return

        # Step 2: Fetch employer feed
        emp_resp = await self.gateway.fetch(DataRequest(
            source="employer.feed",
            params={"participant_id": pid},
        ))
        employer_data = emp_resp.data.get(pid, {})

        # Step 3: Score life events (through policy engine)
        event_scores: list[LifeEventScore] = await self.run_effect(
            effect=FinancialEffect.LIFE_EVENT_SCORE,
            tool="life_event_model",
            action="score",
            params={"participant_id": pid},
            intent_action="score_life_events",
            intent_reason=f"Detect life event signals for participant {pid}",
            exec_fn=lambda: score_life_events(participant, employer_data),
        )

        if not event_scores:
            return

        for event_score in event_scores:
            results["events_detected"] += 1
            logger.info(
                "Event detected: %s for %s (confidence: %.0f%%)",
                event_score.event_type, pid, event_score.confidence * 100
            )

            if event_score.recommended_action == "WATCH" or event_score.confidence < FOLLOWUP_THRESHOLD:
                # Low confidence → schedule follow-up
                await self.run_effect(
                    effect=FinancialEffect.FOLLOWUP_SCHEDULE,
                    tool="followup_scheduler",
                    action="schedule",
                    params={"participant_id": pid, "event_type": event_score.event_type, "days": 30},
                    intent_action="schedule_followup",
                    intent_reason=f"Low-confidence event {event_score.event_type} — re-evaluate in 30 days",
                )
                results["watch_queue"] += 1

            elif event_score.recommended_action == "ESCALATE":
                # High confidence + complex → advisor escalation (requires human approval)
                await self.run_effect(
                    effect=FinancialEffect.ADVISOR_ESCALATION_TRIGGER,
                    tool="advisor_routing",
                    action="escalate",
                    params={
                        "participant_id": pid,
                        "event_type": event_score.event_type,
                        "confidence": event_score.confidence,
                        "signals": event_score.signals,
                        "priority": "high",
                    },
                    intent_action="escalate_to_advisor",
                    intent_reason=f"High-confidence {event_score.event_type} event warrants advisor consultation",
                    metadata={"event_type": event_score.event_type},
                )
                results["advisor_escalations"] += 1

            else:
                # Detected event → draft + send informational outreach
                draft = await self.run_effect(
                    effect=FinancialEffect.OUTREACH_DRAFT,
                    tool="message_generator",
                    action="draft",
                    params={"participant_id": pid, "event_type": event_score.event_type},
                    intent_action="draft_life_event_outreach",
                    intent_reason=f"Draft personalized outreach for {event_score.event_type} event",
                    exec_fn=lambda e=event_score: draft_outreach(participant, e),
                )

                await self.run_effect(
                    effect=FinancialEffect.PARTICIPANT_COMMUNICATION_SEND,
                    tool="email_gateway",
                    action="send",
                    params=draft,
                    intent_action="send_life_event_outreach",
                    intent_reason=f"Deliver timely {event_score.event_type} outreach to {pid}",
                    metadata={"message_type": draft["message_type"], "event_type": event_score.event_type},
                )
                results["outreach_sent"] += 1

            # Log outcome for ROI tracking
            await self.log_outcome("life_event_processed", {
                "participant_id": pid,
                "event_type": event_score.event_type,
                "confidence": event_score.confidence,
                "action": event_score.recommended_action,
            })


# ─── Wiring ───────────────────────────────────────────────────────────────────

def build_agent() -> LifeEventAnticipationAgent:
    manifest = load_manifest(MANIFEST_PATH)

    policy = YamlPolicyEvaluator(POLICY_PATH)
    approver = AutoApprover(default_outcome="approved")
    audit = JsonlAuditSink("audit_life_event.jsonl")
    tower = ControlTower(policy=policy, approver=approver, audit=audit)

    gateway = MockGatewayConnector({
        "participant.data": SAMPLE_PARTICIPANTS,
        "employer.feed": SAMPLE_EMPLOYER_FEED,
    })

    tracker = OutcomeTracker(path="outcomes_life_event.jsonl")

    return LifeEventAnticipationAgent(
        manifest=manifest,
        tower=tower,
        gateway=gateway,
        tracker=tracker,
    )


async def main():
    agent = build_agent()
    results = await agent.execute(participant_ids=["p-101", "p-102", "p-103"])
    print("\nLife Event Results:", results)

    if agent.tracker:
        print("Outcomes:", agent.tracker.summary())


if __name__ == "__main__":
    asyncio.run(main())
