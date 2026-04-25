"""
Plan Design Optimizer Agent — Example Implementation

Models 5 plan design scenarios for a given plan and delivers ranked
recommendations to the relationship manager.

Scenarios modeled:
  1. Baseline (current design)
  2. Auto-enrollment at 6% (vs. current opt-in)
  3. Auto-enrollment + auto-escalation (1% per year, cap 15%)
  4. Enhanced match formula (50% → 100% match on first 4%)
  5. Roth contribution option added
  6. Full QDIA default: target-date fund suite

For each scenario, the model projects:
  - Estimated participation rate at 12 months
  - Average deferral rate at 12 months
  - Projected median income replacement at age 65
  - Estimated employer cost delta (annualized)

Run this example:
    python examples/plan_design_optimizer/agent.py
"""

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from arc.core.gateway import MockGatewayConnector
from arc.core.gateway import DataRequest
from arc.core.observability import OutcomeTracker
from arc.core.effects import FinancialEffect
from arc.core import BaseAgent, load_manifest
from tollgate import (
    AutoApprover,
    ControlTower,
    JsonlAuditSink,
    YamlPolicyEvaluator,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

POLICY_PATH = Path(__file__).parent / "policy.yaml"
MANIFEST_PATH = Path(__file__).parent / "manifest.yaml"


# ─── Data Model ───────────────────────────────────────────────────────────────

@dataclass
class ScenarioResult:
    scenario_id: str
    scenario_name: str
    description: str
    participation_rate_pct: float
    avg_deferral_rate_pct: float
    median_income_replacement_pct: float
    employer_cost_delta_annual: float
    rank: int = 0
    recommendation_strength: str = ""   # STRONG / MODERATE / INFORMATIONAL


# ─── Sandbox Data ─────────────────────────────────────────────────────────────

SAMPLE_PLANS = {
    "plan-alpha": {
        "plan_id": "plan-alpha",
        "plan_name": "Alpha Manufacturing 401(k)",
        "participant_count": 420,
        "current_participation_rate": 0.62,
        "current_avg_deferral_rate": 0.048,
        "current_match_formula": "50% on first 4%",
        "auto_enrollment": False,
        "auto_escalation": False,
        "roth_option": False,
        "qdia_type": "money_market",
        "avg_salary": 72_000,
        "avg_age": 39,
    },
}

SAMPLE_DEMOGRAPHICS = {
    "plan-alpha": {
        "age_under_30_pct": 0.22,
        "age_30_45_pct": 0.41,
        "age_45_55_pct": 0.24,
        "age_over_55_pct": 0.13,
        "median_tenure_years": 4.2,
        "median_salary": 68_000,
        "high_earner_pct": 0.08,  # Earners above $100k
    },
}

MARKET_ASSUMPTIONS = {
    "equity_return_annual": 0.07,
    "bond_return_annual": 0.035,
    "inflation_annual": 0.025,
    "salary_growth_annual": 0.03,
}


# ─── Scenario Modeling Logic ──────────────────────────────────────────────────

def model_scenarios(plan: dict, demographics: dict, market: dict) -> list[ScenarioResult]:
    """
    Model 5 plan design scenarios and project outcomes.
    Returns scenarios ranked by projected income replacement improvement.
    """
    participant_count = plan["participant_count"]
    avg_salary = plan["avg_salary"]
    avg_age = plan["avg_age"]
    years_to_retire = max(65 - avg_age, 10)

    def project_balance(participation_rate, avg_deferral, match_rate, match_cap):
        """Simple balance projection for median participant."""
        annual_contribution = avg_salary * avg_deferral
        employer_contribution = avg_salary * min(avg_deferral, match_cap) * match_rate
        total_annual = annual_contribution + employer_contribution
        projected_balance = total_annual * ((1 + market["equity_return_annual"]) ** years_to_retire - 1) / market["equity_return_annual"]
        income_replacement = (projected_balance * 0.04) / avg_salary * 100
        return round(participation_rate * 100, 1), round(avg_deferral * 100, 2), round(income_replacement, 1)

    def employer_cost(base_deferral, match_rate, match_cap, participants_affected, salary=avg_salary):
        match_per_participant = salary * min(base_deferral, match_cap) * match_rate
        return round(match_per_participant * participants_affected, 0)

    scenarios = []

    # Scenario 1: Baseline (current design)
    pr, dr, ir = project_balance(plan["current_participation_rate"], plan["current_avg_deferral_rate"], 0.50, 0.04)
    scenarios.append(ScenarioResult(
        scenario_id="baseline",
        scenario_name="Current Design (Baseline)",
        description="No changes to current plan design. Voluntary enrollment, 50% match on first 4%.",
        participation_rate_pct=pr,
        avg_deferral_rate_pct=dr,
        median_income_replacement_pct=ir,
        employer_cost_delta_annual=0,
    ))

    # Scenario 2: Auto-enrollment at 6%
    ae_participation = min(plan["current_participation_rate"] + 0.20, 0.95)  # ~20pp lift from AE
    ae_deferral = 0.060
    pr, dr, ir = project_balance(ae_participation, ae_deferral, 0.50, 0.04)
    new_participants = int((ae_participation - plan["current_participation_rate"]) * participant_count)
    scenarios.append(ScenarioResult(
        scenario_id="auto_enroll_6pct",
        scenario_name="Auto-Enrollment at 6%",
        description=(
            "Automatically enroll all new hires at 6% deferral with opt-out. "
            "Industry research shows 18–22pp participation lift within 12 months. "
            "Effective default is the single highest-impact plan design lever."
        ),
        participation_rate_pct=pr,
        avg_deferral_rate_pct=dr,
        median_income_replacement_pct=ir,
        employer_cost_delta_annual=employer_cost(ae_deferral, 0.50, 0.04, new_participants),
    ))

    # Scenario 3: Auto-enrollment + auto-escalation
    aae_participation = min(ae_participation + 0.03, 0.97)
    aae_deferral = 0.084  # 6% default + ~2.4% average escalation over time
    pr, dr, ir = project_balance(aae_participation, aae_deferral, 0.50, 0.04)
    scenarios.append(ScenarioResult(
        scenario_id="auto_enroll_plus_escalation",
        scenario_name="Auto-Enrollment + Auto-Escalation",
        description=(
            "Auto-enroll at 6% with automatic 1% per year escalation (cap 15%). "
            "Escalation captures the 'set and forget' behavior — participants "
            "rarely opt out of small annual increases. Highest long-term deferral impact."
        ),
        participation_rate_pct=pr,
        avg_deferral_rate_pct=dr,
        median_income_replacement_pct=ir,
        employer_cost_delta_annual=employer_cost(aae_deferral, 0.50, 0.04, participant_count * aae_participation),
    ))

    # Scenario 4: Enhanced match (100% on first 4%)
    em_participation = min(plan["current_participation_rate"] + 0.05, 0.90)  # Small lift from better match
    em_deferral = 0.055  # Participants optimize to capture full match
    pr, dr, ir = project_balance(em_participation, em_deferral, 1.00, 0.04)
    scenarios.append(ScenarioResult(
        scenario_id="enhanced_match",
        scenario_name="Enhanced Match: 100% on First 4%",
        description=(
            "Double the match rate from 50% to 100% on first 4% of compensation. "
            "Strong signal to higher earners; increases perceived plan value. "
            "Most effective when employer budget allows — higher cost per participant."
        ),
        participation_rate_pct=pr,
        avg_deferral_rate_pct=dr,
        median_income_replacement_pct=ir,
        employer_cost_delta_annual=employer_cost(em_deferral, 1.00, 0.04, participant_count * em_participation) - employer_cost(plan["current_avg_deferral_rate"], 0.50, 0.04, participant_count * plan["current_participation_rate"]),
    ))

    # Scenario 5: QDIA upgrade (money market → target-date funds)
    qdia_deferral = plan["current_avg_deferral_rate"] * 1.08  # Small lift from better default investment
    qdia_pr = plan["current_participation_rate"]
    pr, dr, ir = project_balance(qdia_pr, qdia_deferral, 0.50, 0.04)
    # TDF vs money market return premium over 26 years
    tdf_return_premium_pct = (market["equity_return_annual"] * 0.7 + market["bond_return_annual"] * 0.3) - market["bond_return_annual"] * 0.8
    adjusted_ir = round(ir * (1 + tdf_return_premium_pct * years_to_retire * 0.015), 1)
    scenarios.append(ScenarioResult(
        scenario_id="qdia_target_date",
        scenario_name="QDIA Upgrade: Target-Date Fund Suite",
        description=(
            "Replace money market default with age-appropriate target-date funds. "
            "No cost to employer. Significant long-term outcome improvement from "
            "appropriate equity allocation in early career participants. "
            "Addresses the ERISA §404(c) QDIA safe harbor."
        ),
        participation_rate_pct=pr,
        avg_deferral_rate_pct=dr,
        median_income_replacement_pct=adjusted_ir,
        employer_cost_delta_annual=0,
    ))

    # Rank by income replacement improvement vs baseline
    baseline_ir = scenarios[0].median_income_replacement_pct
    ranked = sorted(scenarios[1:], key=lambda s: s.median_income_replacement_pct, reverse=True)
    for i, s in enumerate(ranked):
        s.rank = i + 1
        delta = s.median_income_replacement_pct - baseline_ir
        if delta >= 10:
            s.recommendation_strength = "STRONG"
        elif delta >= 5:
            s.recommendation_strength = "MODERATE"
        else:
            s.recommendation_strength = "INFORMATIONAL"

    return [scenarios[0]] + ranked  # Baseline first, then ranked


def format_recommendation_summary(plan: dict, scenarios: list[ScenarioResult]) -> dict:
    """Format the recommendation output for the relationship manager."""
    baseline = next(s for s in scenarios if s.scenario_id == "baseline")
    ranked = [s for s in scenarios if s.rank > 0]

    return {
        "plan_id": plan["plan_id"],
        "plan_name": plan["plan_name"],
        "analysis_date": "2025-01-01",
        "baseline": {
            "participation_rate_pct": baseline.participation_rate_pct,
            "avg_deferral_rate_pct": baseline.avg_deferral_rate_pct,
            "median_income_replacement_pct": baseline.median_income_replacement_pct,
        },
        "top_recommendation": {
            "scenario": ranked[0].scenario_name,
            "description": ranked[0].description,
            "projected_income_replacement_pct": ranked[0].median_income_replacement_pct,
            "improvement_pp": round(ranked[0].median_income_replacement_pct - baseline.median_income_replacement_pct, 1),
            "employer_cost_delta_annual": ranked[0].employer_cost_delta_annual,
            "recommendation_strength": ranked[0].recommendation_strength,
        },
        "all_scenarios": [
            {
                "rank": s.rank,
                "scenario": s.scenario_name,
                "participation_rate_pct": s.participation_rate_pct,
                "avg_deferral_rate_pct": s.avg_deferral_rate_pct,
                "income_replacement_pct": s.median_income_replacement_pct,
                "improvement_pp": round(s.median_income_replacement_pct - baseline.median_income_replacement_pct, 1),
                "employer_cost_delta": s.employer_cost_delta_annual,
                "strength": s.recommendation_strength,
            }
            for s in ranked
        ],
        "disclosure": (
            "These projections are estimates based on industry research and actuarial "
            "assumptions. Actual outcomes will vary. This analysis is prepared for "
            "internal use by relationship managers and does not constitute investment "
            "advice to plan participants or fiduciary advice to plan sponsors."
        ),
    }


# ─── Agent Implementation ─────────────────────────────────────────────────────

class PlanDesignOptimizerAgent(BaseAgent):
    """
    Plan Design Optimizer Agent.

    For each plan:
      1. Reads plan design parameters and demographics via Gateway
      2. Reads fund performance and fee data for context
      3. Models 5 alternative plan design scenarios
      4. Ranks scenarios by projected outcome improvement
      5. Drafts and delivers ranked recommendation to relationship manager
    """

    async def execute(self, plan_ids: list[str]) -> dict:
        results = {"plans_analyzed": 0, "recommendations_delivered": 0, "errors": 0}

        for plan_id in plan_ids:
            try:
                await self._analyze_plan(plan_id, results)
            except Exception as e:
                logger.error("Failed to analyze plan %s: %s", plan_id, e)
                results["errors"] += 1

        logger.info("Plan design optimization complete: %s", results)
        return results

    async def _analyze_plan(self, plan_id: str, results: dict) -> None:
        results["plans_analyzed"] += 1

        # Step 1: Fetch plan data
        plan_resp = await self.gateway.fetch(DataRequest(
            source="plan.data", params={"plan_id": plan_id},
        ))
        plan = plan_resp.data.get(plan_id)
        if not plan:
            logger.warning("Plan %s not found", plan_id)
            return

        # Step 2: Fetch demographic data
        demo_resp = await self.gateway.fetch(DataRequest(
            source="plan.demographics", params={"plan_id": plan_id},
        ))
        demographics = demo_resp.data.get(plan_id, {})

        # Step 3: Fetch market assumptions
        market_resp = await self.gateway.fetch(DataRequest(
            source="market.benchmarks", params={},
        ))
        market = market_resp.data

        logger.info("Modeling scenarios for plan: %s (%d participants)",
                    plan["plan_name"], plan["participant_count"])

        # Step 4: Run scenario modeling (through policy engine)
        scenarios: list[ScenarioResult] = await self.run_effect(
            effect=FinancialEffect.SCENARIO_MODEL_EXECUTE,
            tool="scenario_modeler",
            action="model_plan_design",
            params={"plan_id": plan_id},
            intent_action="model_plan_design_scenarios",
            intent_reason=f"Generate ranked plan design scenarios for {plan_id} client consultation",
            exec_fn=lambda: model_scenarios(plan, demographics, market),
        )

        # Step 5: Draft recommendation summary
        recommendation = await self.run_effect(
            effect=FinancialEffect.RECOMMENDATION_DRAFT,
            tool="recommendation_generator",
            action="draft",
            params={"plan_id": plan_id},
            intent_action="draft_plan_recommendation",
            intent_reason=f"Compile ranked scenarios into RM-ready recommendation for {plan_id}",
            exec_fn=lambda: format_recommendation_summary(plan, scenarios),
        )

        # Step 6: Deliver to relationship manager (ALLOW per policy)
        await self.run_effect(
            effect=FinancialEffect.RECOMMENDATION_DELIVER,
            tool="rm_delivery",
            action="deliver",
            params=recommendation,
            intent_action="deliver_plan_recommendation",
            intent_reason=f"Deliver plan design analysis to RM for {plan_id} client meeting preparation",
        )

        results["recommendations_delivered"] += 1

        # Log outcome
        top = recommendation["top_recommendation"]
        await self.log_outcome("recommendation_delivered", {
            "plan_id": plan_id,
            "top_scenario": top["scenario"],
            "projected_improvement_pp": top["improvement_pp"],
            "recommendation_strength": top["recommendation_strength"],
        })

        logger.info(
            "Recommendation delivered: %s — top scenario '%s' (+%.1fpp income replacement)",
            plan_id, top["scenario"], top["improvement_pp"]
        )


# ─── Wiring ───────────────────────────────────────────────────────────────────

def build_agent() -> PlanDesignOptimizerAgent:
    manifest = load_manifest(MANIFEST_PATH)

    policy = YamlPolicyEvaluator(POLICY_PATH)
    approver = AutoApprover(default_outcome="approved")
    audit = JsonlAuditSink("audit_plan_design.jsonl")
    tower = ControlTower(policy=policy, approver=approver, audit=audit)

    gateway = MockGatewayConnector({
        "plan.data": SAMPLE_PLANS,
        "plan.demographics": SAMPLE_DEMOGRAPHICS,
        "market.benchmarks": MARKET_ASSUMPTIONS,
    })

    tracker = OutcomeTracker(path="outcomes_plan_design.jsonl")

    return PlanDesignOptimizerAgent(
        manifest=manifest,
        tower=tower,
        gateway=gateway,
        tracker=tracker,
    )


async def main():
    agent = build_agent()
    results = await agent.execute(plan_ids=["plan-alpha"])
    print("\nPlan Design Results:", results)

    if agent.tracker:
        print("Outcomes:", agent.tracker.summary())


if __name__ == "__main__":
    asyncio.run(main())
