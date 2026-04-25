"""
Retirement Trajectory Intervention Agent — Example Implementation

Demonstrates two modes:
  - Algorithmic (default): pure Python scoring + template-based message drafting.
  - Bedrock LLM (set USE_BEDROCK=1): Claude writes the intervention message
    via BedrockLLMClient, routed through the policy engine like any other effect.

This is the canonical reference for:
  - Loading a manifest from YAML
  - Wiring ControlTower with financial services policy
  - Using Gateway for data access
  - Running effects through the policy engine
  - Calling Claude via Bedrock inside run_effect() using BedrockLLMClient
  - Logging outcomes for ROI tracking

Run this example:
    python examples/retirement_trajectory/agent.py           # algorithmic drafting
    USE_BEDROCK=1 python examples/retirement_trajectory/agent.py  # Claude drafting
"""

import asyncio
import logging
import os
from pathlib import Path

from arc.core.gateway import MockGatewayConnector
from arc.core.observability import OutcomeTracker
from arc.core.effects import FinancialEffect
from arc.core import BaseAgent, load_manifest
from tollgate import (
    AutoApprover,
    ControlTower,
    JsonlAuditSink,
    YamlPolicyEvaluator,
)

# BedrockLLMClient is optional — only needed when USE_BEDROCK=1
# Install: pip install "agent-foundry[aws]"
USE_BEDROCK = os.environ.get("USE_BEDROCK", "0") == "1"
if USE_BEDROCK:
    try:
        from arc.connectors.bedrock_llm import BedrockLLMClient
    except ImportError:
        raise ImportError("Run: pip install 'agent-foundry[aws]' to use Bedrock LLM")

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

POLICY_PATH = Path(__file__).parent / "policy.yaml"
MANIFEST_PATH = Path(__file__).parent / "manifest.yaml"


# ─── Sample Participant Data (sandbox / synthetic) ────────────────────────────

SAMPLE_PARTICIPANTS = {
    "p-001": {
        "id": "p-001",
        "name": "Marcus",
        "age": 47,
        "balance": 84_200,
        "contrib_rate": 0.03,
        "income": 95_000,
        "login_days_ago": 210,
        "allocation_drift": 0.18,
    },
    "p-002": {
        "id": "p-002",
        "name": "Priya",
        "age": 52,
        "balance": 310_500,
        "contrib_rate": 0.08,
        "income": 140_000,
        "login_days_ago": 12,
        "allocation_drift": 0.04,
    },
}

SAMPLE_COHORT = {
    "age_45_55_median_balance": 210_000,
    "age_45_55_median_contrib_rate": 0.07,
}


# ─── Risk Scoring ─────────────────────────────────────────────────────────────

def compute_trajectory_score(participant: dict, cohort: dict) -> dict:
    """
    Simple retirement trajectory scoring.
    Returns projected income replacement rate and at-risk flag.
    """
    years_to_retire = max(65 - participant["age"], 1)
    projected_balance = (
        participant["balance"]
        * (1 + 0.06) ** years_to_retire
        + participant["income"]
        * participant["contrib_rate"]
        * years_to_retire
        * (1 + 0.04) ** (years_to_retire / 2)
    )
    annual_draw = projected_balance * 0.04
    income_replacement_pct = round((annual_draw / participant["income"]) * 100, 1)
    peer_ratio = participant["balance"] / max(cohort["age_45_55_median_balance"], 1)
    at_risk = income_replacement_pct < 70 or peer_ratio < 0.6

    return {
        "income_replacement_pct": income_replacement_pct,
        "at_risk": at_risk,
        "peer_ratio": round(peer_ratio, 2),
        "years_to_retire": years_to_retire,
    }


def draft_intervention_message(participant: dict, score: dict) -> dict:
    """Draft a personalized plain-language intervention message."""
    name = participant["name"]
    current_pct = score["income_replacement_pct"]
    suggested_rate = round(participant["contrib_rate"] + 0.02, 2)
    projected_pct = round(current_pct + (suggested_rate - participant["contrib_rate"]) * 100, 1)

    if current_pct < 55:
        message_type = "projection"
        body = (
            f"Hi {name} — based on your current account, you're on track to replace "
            f"about {current_pct}% of your income in retirement. That's below the "
            f"70-80% most financial planners recommend. Raising your contribution "
            f"rate to {suggested_rate*100:.0f}% could get you to ~{projected_pct}%."
        )
    else:
        message_type = "informational"
        body = (
            f"Hi {name} — your retirement savings are progressing, but a small "
            f"adjustment now could make a meaningful difference. Consider reviewing "
            f"your contribution rate and allocation to stay on track."
        )

    return {
        "participant_id": participant["id"],
        "message_type": message_type,
        "body": body,
        "channel": "email",
    }


# ─── Agent Implementation ─────────────────────────────────────────────────────

class RetirementTrajectoryAgent(BaseAgent):
    """
    Retirement Trajectory Intervention Agent.

    For each participant, this agent:
      1. Reads participant data and cohort benchmarks via Gateway
      2. Computes a retirement trajectory risk score
      3. Drafts a personalized intervention if the participant is at-risk
         — either via algorithmic template OR Claude via Bedrock (USE_BEDROCK=1)
      4. Sends the intervention (subject to policy engine approval)
      5. Logs outcomes for ROI tracking
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Wire up Bedrock LLM client when USE_BEDROCK=1
        # BedrockLLMClient routes every Claude call through run_effect() so it
        # is policy-enforced, audit-logged, and counted against the manifest.
        self.llm = BedrockLLMClient(agent=self) if USE_BEDROCK else None

    async def execute(self, participant_ids: list[str]) -> dict:
        results = {"processed": 0, "at_risk": 0, "interventions_sent": 0, "errors": 0}

        for pid in participant_ids:
            try:
                await self._process_participant(pid, results)
            except Exception as e:
                logger.error("Failed to process participant %s: %s", pid, e)
                results["errors"] += 1

        logger.info("Run complete: %s", results)
        return results

    async def _process_participant(self, pid: str, results: dict) -> None:
        from arc.core.gateway import DataRequest

        results["processed"] += 1

        # Step 1: Fetch participant data via Gateway
        p_resp = await self.gateway.fetch(DataRequest(
            source="participant.data",
            params={"participant_id": pid},
        ))
        participant = p_resp.data.get(pid)
        if not participant:
            logger.warning("Participant %s not found", pid)
            return

        # Step 2: Fetch cohort benchmark
        cohort_resp = await self.gateway.fetch(DataRequest(
            source="participant.cohort",
            params={"age_band": "45_55"},
        ))
        cohort = cohort_resp.data

        # Step 3: Compute risk score (ALLOW — internal computation)
        score = await self.run_effect(
            effect=FinancialEffect.RISK_SCORE_COMPUTE,
            tool="trajectory_scorer",
            action="compute",
            params={"participant_id": pid},
            intent_action="score_retirement_trajectory",
            intent_reason=f"Identify retirement risk for participant {pid}",
            exec_fn=lambda: compute_trajectory_score(participant, cohort),
        )

        if not score["at_risk"]:
            return

        results["at_risk"] += 1
        logger.info("At-risk participant: %s (replacement: %s%%)", pid, score["income_replacement_pct"])

        # Step 4: Draft intervention (ALLOW — internal)
        # Mode A: algorithmic template (default, no LLM dependency)
        # Mode B: Claude via Bedrock (USE_BEDROCK=1) — personalised, empathetic prose
        if self.llm is not None:
            # ── Bedrock / Claude path ─────────────────────────────────────────
            # run_effect() is called INSIDE BedrockLLMClient.generate() —
            # the LLM call is policy-enforced just like any other effect.
            llm_text = await self.llm.generate(
                effect=FinancialEffect.INTERVENTION_DRAFT,
                intent_action="draft_intervention",
                intent_reason=f"Generate personalised retirement intervention for participant {pid}",
                system=(
                    "You are a retirement planning assistant at a regulated financial services firm. "
                    "Write clear, empathetic, jargon-free messages. Never give specific investment advice. "
                    "Always include a disclaimer that projections are estimates, not guarantees."
                ),
                prompt=(
                    f"Write a 2-3 sentence retirement savings nudge for {participant['name']}, "
                    f"age {participant['age']}, currently on track to replace "
                    f"{score['income_replacement_pct']}% of their income (target: 70–80%). "
                    f"Their current contribution rate is {participant['contrib_rate']*100:.0f}%. "
                    f"Suggest increasing it by 2 percentage points. "
                    f"Tone: warm, encouraging, not alarming."
                ),
                max_tokens=256,
                temperature=0.4,
            )
            draft = {
                "participant_id": pid,
                "message_type":   "projection" if score["income_replacement_pct"] < 55 else "informational",
                "body":           llm_text,
                "channel":        "email",
                "generated_by":   "bedrock-claude",
            }
        else:
            # ── Algorithmic path (default) ────────────────────────────────────
            draft = await self.run_effect(
                effect=FinancialEffect.INTERVENTION_DRAFT,
                tool="message_generator",
                action="draft",
                params={"participant_id": pid, "score": score},
                intent_action="draft_intervention",
                intent_reason=f"Generate personalized intervention for at-risk participant {pid}",
                exec_fn=lambda: draft_intervention_message(participant, score),
            )

        # Step 5: Send intervention (ASK by default — policy engine decides)
        await self.run_effect(
            effect=FinancialEffect.PARTICIPANT_COMMUNICATION_SEND,
            tool="email_gateway",
            action="send",
            params=draft,
            intent_action="send_intervention",
            intent_reason=f"Deliver retirement intervention to participant {pid}",
            metadata={"message_type": draft["message_type"]},
        )

        results["interventions_sent"] += 1

        # Step 6: Log that we sent it
        await self.run_effect(
            effect=FinancialEffect.INTERVENTION_LOG_WRITE,
            tool="outcome_store",
            action="write",
            params={"participant_id": pid, "message_type": draft["message_type"]},
            intent_action="log_intervention",
            intent_reason="Record intervention for ROI tracking",
        )

        await self.log_outcome("intervention_sent", {
            "participant_id": pid,
            "message_type": draft["message_type"],
            "income_replacement_pct": score["income_replacement_pct"],
        })


# ─── Wiring ───────────────────────────────────────────────────────────────────

def build_agent() -> RetirementTrajectoryAgent:
    """Wire up the agent with its policy, gateway, and tower."""
    manifest = load_manifest(MANIFEST_PATH)

    policy = YamlPolicyEvaluator(POLICY_PATH)
    approver = AutoApprover(default_outcome="approved")  # Swap for CliApprover in review mode
    audit = JsonlAuditSink("audit.jsonl")
    tower = ControlTower(policy=policy, approver=approver, audit=audit)

    gateway = MockGatewayConnector({
        "participant.data": SAMPLE_PARTICIPANTS,
        "participant.cohort": SAMPLE_COHORT,
    })

    tracker = OutcomeTracker(path="outcomes.jsonl")

    return RetirementTrajectoryAgent(
        manifest=manifest,
        tower=tower,
        gateway=gateway,
        tracker=tracker,
    )


async def main():
    agent = build_agent()
    results = await agent.execute(participant_ids=["p-001", "p-002"])
    print("\nResults:", results)

    if agent.tracker:
        print("Outcomes:", agent.tracker.summary())


if __name__ == "__main__":
    asyncio.run(main())
