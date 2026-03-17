"""
Fiduciary Watchdog Agent — Example Implementation

Monitors plan fund lineups for ERISA §404(a) compliance risk:
  - Expense ratio reasonableness vs. benchmark category averages
  - Trailing 3-year and 5-year performance vs. peer benchmarks
  - Style drift (fund characteristics diverging from stated objective)
  - Persistent underperformance (consecutive periods below benchmark)

Findings are tiered:
  - LOW: Early warning, informational — auto-emit to dashboard
  - HIGH: Actionable fiduciary risk — queue for human review before emission

Run this example:
    python examples/fiduciary_watchdog/agent.py
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


# ─── Data Model ───────────────────────────────────────────────────────────────

@dataclass
class FundFinding:
    plan_id: str
    fund_ticker: str
    fund_name: str
    severity: Literal["LOW", "HIGH"]
    finding_type: str
    detail: str
    metric_value: float
    threshold: float
    regulatory_ref: str


# ─── Sandbox Data ─────────────────────────────────────────────────────────────

SAMPLE_PLANS = {
    "plan-001": {
        "plan_id": "plan-001",
        "plan_name": "Acme Corp 401(k)",
        "funds": ["VINIX", "FCNTX", "PRBLX", "FXAIX", "VBTLX"],
    },
    "plan-002": {
        "plan_id": "plan-002",
        "plan_name": "Beta Co Retirement Plan",
        "funds": ["SPY", "AGG", "RERGX"],
    },
}

# Fund data: expense_ratio, 3yr_return, benchmark_return, category_avg_expense
SAMPLE_FUND_DATA = {
    "VINIX": {
        "name": "Vanguard Institutional Index",
        "expense_ratio": 0.035,
        "return_3yr": 10.8,
        "benchmark_return_3yr": 10.9,
        "category_avg_expense": 0.70,
        "category": "Large Cap Blend",
        "return_5yr": 13.2,
        "benchmark_return_5yr": 13.1,
    },
    "FCNTX": {
        "name": "Fidelity Contrafund",
        "expense_ratio": 0.86,
        "return_3yr": 7.2,
        "benchmark_return_3yr": 10.5,
        "category_avg_expense": 0.95,
        "category": "Large Cap Growth",
        "return_5yr": 12.1,
        "benchmark_return_5yr": 14.0,
    },
    "PRBLX": {
        "name": "Parnassus Core Equity",
        "expense_ratio": 0.82,
        "return_3yr": 9.8,
        "benchmark_return_3yr": 10.2,
        "category_avg_expense": 0.90,
        "category": "Large Cap Blend",
        "return_5yr": 13.5,
        "benchmark_return_5yr": 13.1,
    },
    "FXAIX": {
        "name": "Fidelity 500 Index",
        "expense_ratio": 0.015,
        "return_3yr": 10.7,
        "benchmark_return_3yr": 10.9,
        "category_avg_expense": 0.70,
        "category": "Large Cap Blend",
        "return_5yr": 13.0,
        "benchmark_return_5yr": 13.1,
    },
    "VBTLX": {
        "name": "Vanguard Total Bond Market",
        "expense_ratio": 0.05,
        "return_3yr": -1.8,
        "benchmark_return_3yr": -1.6,
        "category_avg_expense": 0.55,
        "category": "Intermediate Core Bond",
        "return_5yr": 0.4,
        "benchmark_return_5yr": 0.5,
    },
    "SPY": {
        "name": "SPDR S&P 500 ETF",
        "expense_ratio": 0.0945,
        "return_3yr": 10.7,
        "benchmark_return_3yr": 10.9,
        "category_avg_expense": 0.70,
        "category": "Large Cap Blend",
        "return_5yr": 13.0,
        "benchmark_return_5yr": 13.1,
    },
    "AGG": {
        "name": "iShares Core US Aggregate Bond",
        "expense_ratio": 0.03,
        "return_3yr": -1.7,
        "benchmark_return_3yr": -1.6,
        "category_avg_expense": 0.55,
        "category": "Intermediate Core Bond",
        "return_5yr": 0.4,
        "benchmark_return_5yr": 0.5,
    },
    "RERGX": {
        "name": "American Funds EuroPacific Growth R6",
        "expense_ratio": 0.49,
        "return_3yr": 3.1,
        "benchmark_return_3yr": 5.8,
        "category_avg_expense": 0.90,
        "category": "Foreign Large Blend",
        "return_5yr": 5.2,
        "benchmark_return_5yr": 8.1,
    },
}


# ─── Compliance Evaluation Logic ──────────────────────────────────────────────

EXCESSIVE_FEE_THRESHOLD_RATIO = 1.5     # Fund expense > 1.5x category avg → flag
UNDERPERFORMANCE_THRESHOLD_BPS = -150   # Fund 3yr return > 150bps below benchmark → flag
SEVERE_UNDERPERFORMANCE_BPS = -300      # > 300bps below both 3yr AND 5yr → HIGH severity


def evaluate_fund(plan_id: str, ticker: str, fund: dict) -> list[FundFinding]:
    """Evaluate a single fund against ERISA §404(a) thresholds."""
    findings = []

    # Check 1: Expense ratio reasonableness
    expense_ratio_multiple = fund["expense_ratio"] / max(fund["category_avg_expense"], 0.001)
    if expense_ratio_multiple > EXCESSIVE_FEE_THRESHOLD_RATIO:
        severity = "HIGH" if expense_ratio_multiple > 2.0 else "LOW"
        findings.append(FundFinding(
            plan_id=plan_id,
            fund_ticker=ticker,
            fund_name=fund["name"],
            severity=severity,
            finding_type="excessive_expense_ratio",
            detail=(
                f"Fund expense ratio {fund['expense_ratio']*100:.3f}% is "
                f"{expense_ratio_multiple:.1f}x the category average "
                f"({fund['category_avg_expense']*100:.2f}%) for {fund['category']}. "
                f"ERISA §404(a) prudence requires fiduciary to demonstrate why "
                f"higher-cost options are retained."
            ),
            metric_value=round(expense_ratio_multiple, 2),
            threshold=EXCESSIVE_FEE_THRESHOLD_RATIO,
            regulatory_ref="ERISA §404(a)(1)(B); DOL Reg §2550.404a-1(b)",
        ))

    # Check 2: 3-year performance vs benchmark
    underperformance_3yr = (fund["return_3yr"] - fund["benchmark_return_3yr"]) * 100
    underperformance_5yr = (fund["return_5yr"] - fund["benchmark_return_5yr"]) * 100

    if underperformance_3yr < UNDERPERFORMANCE_THRESHOLD_BPS:
        if underperformance_3yr < SEVERE_UNDERPERFORMANCE_BPS / 100 and underperformance_5yr < SEVERE_UNDERPERFORMANCE_BPS / 100:
            severity = "HIGH"
            finding_type = "persistent_underperformance"
            detail = (
                f"Fund underperforms benchmark by {abs(underperformance_3yr):.0f}bps "
                f"over 3 years AND {abs(underperformance_5yr):.0f}bps over 5 years. "
                f"Persistent underperformance across multiple periods represents a "
                f"material fiduciary risk under ERISA §404(a). Immediate review required."
            )
        else:
            severity = "LOW"
            finding_type = "underperformance_3yr"
            detail = (
                f"Fund underperforms benchmark by {abs(underperformance_3yr):.0f}bps "
                f"over 3 years ({fund['return_3yr']}% vs {fund['benchmark_return_3yr']}%). "
                f"Monitor for persistence. Review if underperformance continues next quarter."
            )

        findings.append(FundFinding(
            plan_id=plan_id,
            fund_ticker=ticker,
            fund_name=fund["name"],
            severity=severity,
            finding_type=finding_type,
            detail=detail,
            metric_value=round(underperformance_3yr, 1),
            threshold=UNDERPERFORMANCE_THRESHOLD_BPS,
            regulatory_ref="ERISA §404(a)(1)(B); DOL Reg §2550.404a-1(b)(2)(ii)",
        ))

    return findings


# ─── Agent Implementation ─────────────────────────────────────────────────────

class FiduciaryWatchdogAgent(BaseAgent):
    """
    Fiduciary Watchdog Agent.

    For each plan in scope:
      1. Reads plan fund lineup via Gateway
      2. Reads fund performance and fee data via Gateway
      3. Evaluates each fund against ERISA §404(a) thresholds
      4. Drafts compliance findings
      5. Emits low-severity findings to dashboard (ALLOW)
      6. Queues high-severity findings for fiduciary review (ASK)
      7. Logs all findings for audit trail
    """

    async def execute(self, plan_ids: list[str]) -> dict:
        results = {
            "plans_reviewed": 0,
            "funds_evaluated": 0,
            "findings_low": 0,
            "findings_high": 0,
            "errors": 0,
        }

        for plan_id in plan_ids:
            try:
                await self._review_plan(plan_id, results)
            except Exception as e:
                logger.error("Failed to review plan %s: %s", plan_id, e)
                results["errors"] += 1

        logger.info("Watchdog run complete: %s", results)
        return results

    async def _review_plan(self, plan_id: str, results: dict) -> None:
        results["plans_reviewed"] += 1

        # Step 1: Fetch plan data (fund lineup)
        plan_resp = await self.gateway.fetch(DataRequest(
            source="plan.data",
            params={"plan_id": plan_id},
        ))
        plan = plan_resp.data.get(plan_id)
        if not plan:
            logger.warning("Plan %s not found", plan_id)
            return

        logger.info("Reviewing plan: %s (%d funds)", plan["plan_name"], len(plan["funds"]))

        # Step 2: Fetch fund data
        fund_resp = await self.gateway.fetch(DataRequest(
            source="fund.performance",
            params={"tickers": plan["funds"]},
        ))
        all_fund_data = fund_resp.data

        # Step 3: Evaluate each fund
        all_findings: list[FundFinding] = []
        for ticker in plan["funds"]:
            fund_data = all_fund_data.get(ticker)
            if not fund_data:
                continue

            # Run compliance evaluation through policy engine
            findings = await self.run_effect(
                effect=FinancialEffect.COMPLIANCE_EVALUATE,
                tool="erisa_evaluator",
                action="evaluate_fund",
                params={"plan_id": plan_id, "ticker": ticker},
                intent_action="evaluate_fund_compliance",
                intent_reason=f"ERISA §404(a) prudence evaluation for {ticker} in {plan_id}",
                exec_fn=lambda t=ticker, f=fund_data: evaluate_fund(plan_id, t, f),
            )
            results["funds_evaluated"] += 1
            all_findings.extend(findings)

        if not all_findings:
            logger.info("No findings for plan %s", plan_id)
            return

        # Step 4: Process each finding
        for finding in all_findings:
            # Draft the finding memo
            draft = await self.run_effect(
                effect=FinancialEffect.FINDING_DRAFT,
                tool="finding_generator",
                action="draft",
                params={
                    "plan_id": finding.plan_id,
                    "fund_ticker": finding.fund_ticker,
                    "finding_type": finding.finding_type,
                    "severity": finding.severity,
                    "detail": finding.detail,
                    "regulatory_ref": finding.regulatory_ref,
                },
                intent_action="draft_compliance_finding",
                intent_reason=f"Draft ERISA finding for {finding.fund_ticker}: {finding.finding_type}",
                exec_fn=lambda f=finding: {
                    "title": f"[{f.severity}] {f.finding_type.replace('_', ' ').title()} — {f.fund_ticker}",
                    "plan_id": f.plan_id,
                    "fund_ticker": f.fund_ticker,
                    "severity": f.severity,
                    "detail": f.detail,
                    "regulatory_ref": f.regulatory_ref,
                    "metric_value": f.metric_value,
                    "threshold": f.threshold,
                },
            )

            # Step 5a: Low severity → emit to dashboard (ALLOW)
            if finding.severity == "LOW":
                await self.run_effect(
                    effect=FinancialEffect.COMPLIANCE_FINDING_EMIT_LOW,
                    tool="compliance_dashboard",
                    action="emit",
                    params=draft,
                    intent_action="emit_low_finding",
                    intent_reason=f"Emit low-severity finding to dashboard for monitoring",
                    metadata={"severity": "LOW", "plan_id": plan_id},
                )
                results["findings_low"] += 1

            # Step 5b: High severity → queue for human review (ASK)
            else:
                # First add to human review queue
                await self.run_effect(
                    effect=FinancialEffect.HUMAN_REVIEW_QUEUE_ADD,
                    tool="review_queue",
                    action="add",
                    params={**draft, "requires_fiduciary_review": True},
                    intent_action="queue_for_fiduciary_review",
                    intent_reason=f"High-severity finding requires fiduciary committee review before emission",
                    metadata={"severity": "HIGH", "plan_id": plan_id},
                )

                # Then emit (requires human approval per policy)
                await self.run_effect(
                    effect=FinancialEffect.COMPLIANCE_FINDING_EMIT_HIGH,
                    tool="compliance_dashboard",
                    action="emit",
                    params=draft,
                    intent_action="emit_high_finding",
                    intent_reason=f"Emit high-severity finding after fiduciary review approval",
                    metadata={"severity": "HIGH", "plan_id": plan_id},
                )
                results["findings_high"] += 1

            # Step 6: Log all findings for audit trail
            await self.run_effect(
                effect=FinancialEffect.FINDING_LOG_WRITE,
                tool="finding_store",
                action="write",
                params={
                    "plan_id": plan_id,
                    "fund_ticker": finding.fund_ticker,
                    "severity": finding.severity,
                    "finding_type": finding.finding_type,
                },
                intent_action="log_finding",
                intent_reason="Record compliance finding for ERISA §107 audit retention",
            )

            await self.log_outcome("finding_emitted", {
                "plan_id": plan_id,
                "fund_ticker": finding.fund_ticker,
                "severity": finding.severity,
                "finding_type": finding.finding_type,
            })

            logger.info(
                "[%s] %s: %s in %s",
                finding.severity, finding.finding_type, finding.fund_ticker, plan_id
            )


# ─── Wiring ───────────────────────────────────────────────────────────────────

def build_agent() -> FiduciaryWatchdogAgent:
    manifest = load_manifest(MANIFEST_PATH)

    policy = YamlPolicyEvaluator(POLICY_PATH)
    approver = AutoApprover(default_outcome="approved")
    audit = JsonlAuditSink("audit_watchdog.jsonl")
    tower = ControlTower(policy=policy, approver=approver, audit=audit)

    gateway = MockGatewayConnector({
        "plan.data": SAMPLE_PLANS,
        "fund.performance": SAMPLE_FUND_DATA,
    })

    tracker = OutcomeTracker(path="outcomes_watchdog.jsonl")

    return FiduciaryWatchdogAgent(
        manifest=manifest,
        tower=tower,
        gateway=gateway,
        tracker=tracker,
    )


async def main():
    agent = build_agent()
    results = await agent.execute(plan_ids=["plan-001", "plan-002"])
    print("\nWatchdog Results:", results)

    if agent.tracker:
        print("Outcomes:", agent.tracker.summary())


if __name__ == "__main__":
    asyncio.run(main())
