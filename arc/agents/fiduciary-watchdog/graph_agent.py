"""
fiduciary_watchdog.graph_agent
───────────────────────────────
LangGraph-powered Fiduciary Watchdog Agent.

This is a complete, runnable example of GraphAgent — showing how to use
LangGraph's state machine to structure a multi-step compliance workflow
while keeping all effects policy-enforced by Tollgate ControlTower.

Graph topology (for a single fund):

    START
      │
      ▼
    [fetch_fund_data]        ← Tier 1 reads (ALLOW by default)
      │
      ▼
    [evaluate_fees]          ← Tier 2 computation (ALLOW)
      │
      ▼
    [evaluate_performance]   ← Tier 2 computation (ALLOW)
      │
      ▼
    [check_style_drift]      ← Tier 2 computation (ALLOW)
      │
      ▼
    [score_overall_risk]     ← Tier 2 computation (ALLOW)
      │
      ▼
    [draft_finding]          ← Tier 3 draft (ALLOW — internal only)
      │
      ▼
    [route_finding]  ──────────────── conditional router ──────────────────────
      │                                                                         │
      ▼ (severity = "none")    ▼ (severity = "low")      ▼ (severity = "high") │
      END              [emit_low_finding]         [queue_for_review]            │
                             │                          │                       │
                             ▼                          ▼                       │
                            END                 [emit_high_finding] ◄───────────┘
                                                        │              (after review)
                                                        ▼
                                                       END

Install:
    pip install "arc-orchestrators[langgraph]"

Run locally (sandbox):
    python graph_agent.py

Or wire through arc.runtime.RuntimeBuilder for production deployment.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from arc.orchestrators.langgraph_agent import END, START, FoundryState, GraphAgent
from arc.core.effects import FinancialEffect
from arc.core.manifest import AgentManifest
from arc.core.gateway import GatewayConnector
from arc.core.observability import OutcomeTracker
from tollgate.tower import ControlTower

logger = logging.getLogger(__name__)


# ── State schema ───────────────────────────────────────────────────────────────


class WatchdogState(FoundryState):
    """
    State that flows through all nodes in the Fiduciary Watchdog graph.

    Fields are populated progressively as the graph executes.
    All fields are Optional so nodes can declare partial updates.
    """

    # ── Inputs (provided to agent.execute()) ──────────────────────────────
    fund_id:   str
    plan_id:   str

    # ── Data fetched in fetch_fund_data node ──────────────────────────────
    fund_name:        Optional[str]
    fund_category:    Optional[str]
    benchmark_id:     Optional[str]
    expense_ratio:    Optional[float]    # Annual fund expense ratio (%)
    category_avg_er:  Optional[float]    # Category average expense ratio (%)

    # ── Fee analysis (evaluate_fees node) ─────────────────────────────────
    fee_analysis:         Optional[dict]
    fee_flag:             Optional[bool]  # True = fee concern
    fee_excess_bps:       Optional[int]   # Excess fee in basis points vs category avg

    # ── Performance analysis (evaluate_performance node) ──────────────────
    performance_analysis: Optional[dict]
    performance_flag:     Optional[bool]  # True = performance concern
    trailing_returns:     Optional[dict]  # {"1y": float, "3y": float, "5y": float}

    # ── Style drift check (check_style_drift node) ─────────────────────────
    style_analysis:       Optional[dict]
    style_drift_flag:     Optional[bool]  # True = style drift detected

    # ── Composite risk score (score_overall_risk node) ─────────────────────
    risk_score:           Optional[float]   # 0.0 – 1.0
    risk_factors:         Optional[list]    # List of contributing factors

    # ── Finding (draft_finding node) ───────────────────────────────────────
    finding_draft:        Optional[dict]
    finding_severity:     Optional[str]     # "none" | "low" | "high"
    finding_id:           Optional[str]

    # ── Final result ───────────────────────────────────────────────────────
    emitted:              Optional[bool]
    review_queued:        Optional[bool]


# ── Graph Agent ────────────────────────────────────────────────────────────────


class FiduciaryWatchdogGraphAgent(GraphAgent[WatchdogState]):
    """
    Fiduciary Watchdog — LangGraph-powered ERISA compliance monitor.

    Monitors a single fund-in-plan for ERISA §404(a) compliance risks:
      - Excessive fees vs. category average
      - Trailing performance vs. benchmark
      - Investment style drift (category vs. actual holdings)

    All nodes call self.run_effect() — every data read and output is
    policy-enforced, rate-limited, and audit-logged by Tollgate.

    High-severity findings (risk_score >= 0.70) route to the human review
    queue before emission (ASK policy in policy.yaml).
    Low-severity findings auto-emit to the compliance dashboard (ALLOW).
    """

    def __init__(
        self,
        manifest: AgentManifest,
        tower: ControlTower,
        gateway: GatewayConnector,
        tracker: OutcomeTracker | None = None,
    ):
        super().__init__(manifest, tower, gateway, tracker)

    def build_graph(self):
        from langgraph.graph import StateGraph

        g = StateGraph(WatchdogState)

        # ── Nodes ──────────────────────────────────────────────────────────
        g.add_node("fetch_fund_data",        self.fetch_fund_data)
        g.add_node("evaluate_fees",          self.evaluate_fees)
        g.add_node("evaluate_performance",   self.evaluate_performance)
        g.add_node("check_style_drift",      self.check_style_drift)
        g.add_node("score_overall_risk",     self.score_overall_risk)
        g.add_node("draft_finding",          self.draft_finding)
        g.add_node("emit_low_finding",       self.emit_low_finding)
        g.add_node("queue_for_review",       self.queue_for_review)
        g.add_node("emit_high_finding",      self.emit_high_finding)

        # ── Linear edges ───────────────────────────────────────────────────
        g.add_edge(START,                   "fetch_fund_data")
        g.add_edge("fetch_fund_data",       "evaluate_fees")
        g.add_edge("evaluate_fees",         "evaluate_performance")
        g.add_edge("evaluate_performance",  "check_style_drift")
        g.add_edge("check_style_drift",     "score_overall_risk")
        g.add_edge("score_overall_risk",    "draft_finding")

        # ── Conditional routing after draft ────────────────────────────────
        g.add_conditional_edges(
            "draft_finding",
            self.route_by_severity,
            {
                "none": END,
                "low":  "emit_low_finding",
                "high": "queue_for_review",
            },
        )
        g.add_edge("emit_low_finding",  END)
        g.add_edge("queue_for_review",  "emit_high_finding")
        g.add_edge("emit_high_finding", END)

        return g.compile()

    # ── Data fetch ─────────────────────────────────────────────────────────

    async def fetch_fund_data(self, state: WatchdogState) -> dict:
        """Fetch fund metadata, fees, and benchmark from the data gateway."""
        fund_id = state["fund_id"]
        plan_id = state["plan_id"]

        plan_data = await self.run_effect(
            effect=FinancialEffect.PLAN_DATA_READ,
            tool="plan_data_gateway",
            action="get_fund_in_plan",
            params={"fund_id": fund_id, "plan_id": plan_id},
            intent_action="fetch_fund_data",
            intent_reason=(
                f"Retrieve fund metadata and plan context for ERISA §404(a) "
                f"evaluation of fund {fund_id} in plan {plan_id}"
            ),
        )

        fund_data = await self.run_effect(
            effect=FinancialEffect.FUND_FEES_READ,
            tool="fund_data_gateway",
            action="get_fund_details",
            params={"fund_id": fund_id},
            intent_action="fetch_fund_fees",
            intent_reason=f"Retrieve expense ratio for fee analysis of fund {fund_id}",
        )

        logger.info(
            "fetch_fund_data agent=%s fund=%s plan=%s",
            self.manifest.agent_id, fund_id, plan_id,
        )

        return {
            "fund_name":       fund_data.get("name",          f"Fund {fund_id}"),
            "fund_category":   fund_data.get("category",      "unknown"),
            "benchmark_id":    fund_data.get("benchmark_id",  "SPY"),
            "expense_ratio":   fund_data.get("expense_ratio", 0.0),
            "category_avg_er": fund_data.get("category_avg_expense_ratio", 0.0),
        }

    # ── Analysis nodes ─────────────────────────────────────────────────────

    async def evaluate_fees(self, state: WatchdogState) -> dict:
        """
        Compare fund expense ratio to category average.

        ERISA §404(a) standard: fees must be reasonable for the services
        provided. A fund charging >25 bps above the category average is
        considered a "fee concern" for monitoring purposes.
        """
        er           = state.get("expense_ratio", 0.0) or 0.0
        category_avg = state.get("category_avg_er", 0.0) or 0.0

        analysis = await self.run_effect(
            effect=FinancialEffect.COMPLIANCE_EVALUATE,
            tool="fee_evaluator",
            action="evaluate_expense_ratio",
            params={
                "fund_id":      state["fund_id"],
                "expense_ratio": er,
                "category_avg": category_avg,
            },
            intent_action="evaluate_fees",
            intent_reason=(
                "Assess whether fund expense ratio is reasonable under "
                "ERISA §404(a) prudence standard"
            ),
        )

        excess_bps = round((er - category_avg) * 100)  # 1% = 100 bps
        fee_flag   = excess_bps >= 25  # 25+ bps excess = concern

        logger.info(
            "evaluate_fees fund=%s er=%.2f%% cat_avg=%.2f%% excess=%d bps flag=%s",
            state["fund_id"], er, category_avg, excess_bps, fee_flag,
        )

        return {
            "fee_analysis":   analysis,
            "fee_flag":       fee_flag,
            "fee_excess_bps": excess_bps,
        }

    async def evaluate_performance(self, state: WatchdogState) -> dict:
        """
        Evaluate trailing 1y / 3y / 5y performance vs. benchmark.

        A fund underperforming its benchmark by >100 bps/year over 3+ years
        is a "performance concern" under ERISA prudence standards.
        """
        fund_id      = state["fund_id"]
        benchmark_id = state.get("benchmark_id", "SPY") or "SPY"

        perf_data = await self.run_effect(
            effect=FinancialEffect.FUND_PERFORMANCE_READ,
            tool="fund_data_gateway",
            action="get_trailing_returns",
            params={
                "fund_id":      fund_id,
                "benchmark_id": benchmark_id,
                "periods":      ["1y", "3y", "5y"],
            },
            intent_action="evaluate_performance",
            intent_reason=(
                f"Compare trailing returns vs benchmark {benchmark_id} "
                f"to assess performance under ERISA §404(a)"
            ),
        )

        analysis = await self.run_effect(
            effect=FinancialEffect.COMPLIANCE_EVALUATE,
            tool="performance_evaluator",
            action="compare_to_benchmark",
            params={
                "fund_id":        fund_id,
                "trailing_data":  perf_data,
                "benchmark_id":   benchmark_id,
            },
            intent_action="evaluate_performance_vs_benchmark",
            intent_reason="Determine if persistent underperformance constitutes ERISA concern",
        )

        # Performance flag: underperforming 3y avg by > 100 bps
        perf_flag = bool(analysis.get("underperformance_flag", False))

        logger.info(
            "evaluate_performance fund=%s vs=%s flag=%s",
            fund_id, benchmark_id, perf_flag,
        )

        return {
            "performance_analysis": analysis,
            "performance_flag":     perf_flag,
            "trailing_returns":     perf_data.get("trailing_returns", {}),
        }

    async def check_style_drift(self, state: WatchdogState) -> dict:
        """
        Detect investment style drift vs. declared fund category.

        A fund that consistently holds securities outside its declared
        category (e.g., a "Large Cap Value" fund with significant small-cap
        or growth holdings) creates fiduciary risk from benchmark misalignment.
        """
        analysis = await self.run_effect(
            effect=FinancialEffect.COMPLIANCE_EVALUATE,
            tool="style_analyzer",
            action="check_style_drift",
            params={
                "fund_id":      state["fund_id"],
                "category":     state.get("fund_category", "unknown"),
            },
            intent_action="check_style_drift",
            intent_reason=(
                "Detect style drift between fund holdings and declared "
                "category to identify benchmark misalignment risk"
            ),
        )

        style_drift_flag = bool(analysis.get("drift_detected", False))

        logger.info(
            "check_style_drift fund=%s flag=%s",
            state["fund_id"], style_drift_flag,
        )

        return {
            "style_analysis":  analysis,
            "style_drift_flag": style_drift_flag,
        }

    async def score_overall_risk(self, state: WatchdogState) -> dict:
        """
        Compute a composite ERISA risk score [0.0, 1.0] from all flags.

        Risk score weights:
          - Fee concern:         0.35 (high weight — fees are primary ERISA concern)
          - Performance concern: 0.40 (highest weight — outcomes matter most)
          - Style drift:         0.25 (moderate — benchmark misalignment)

        Thresholds:
          - score < 0.30: no finding
          - 0.30 ≤ score < 0.70: low severity
          - score ≥ 0.70: high severity
        """
        score_result = await self.run_effect(
            effect=FinancialEffect.RISK_SCORE_COMPUTE,
            tool="risk_scorer",
            action="composite_score",
            params={
                "fund_id":        state["fund_id"],
                "fee_flag":       state.get("fee_flag", False),
                "fee_excess_bps": state.get("fee_excess_bps", 0),
                "perf_flag":      state.get("performance_flag", False),
                "style_flag":     state.get("style_drift_flag", False),
            },
            intent_action="score_overall_risk",
            intent_reason=(
                "Aggregate individual compliance signals into a composite "
                "ERISA §404(a) risk score for severity classification"
            ),
        )

        risk_score   = float(score_result.get("score", 0.0))
        risk_factors = score_result.get("factors", [])

        logger.info(
            "score_overall_risk fund=%s score=%.3f factors=%s",
            state["fund_id"], risk_score, risk_factors,
        )

        return {
            "risk_score":   risk_score,
            "risk_factors": risk_factors,
        }

    async def draft_finding(self, state: WatchdogState) -> dict:
        """
        Draft an ERISA compliance finding based on the risk score.

        The draft is internal only (Tier 3 — ALLOW by default).
        It is not delivered to any external party until emit_*_finding runs.
        """
        risk_score = state.get("risk_score", 0.0) or 0.0
        severity   = (
            "high" if risk_score >= 0.70
            else "low" if risk_score >= 0.30
            else "none"
        )

        if severity == "none":
            logger.info(
                "draft_finding fund=%s severity=none (score=%.3f) — no finding",
                state["fund_id"], risk_score,
            )
            return {"finding_severity": "none", "finding_draft": None}

        draft = await self.run_effect(
            effect=FinancialEffect.FINDING_DRAFT,
            tool="finding_generator",
            action="draft_erisa_finding",
            params={
                "fund_id":      state["fund_id"],
                "plan_id":      state["plan_id"],
                "fund_name":    state.get("fund_name"),
                "severity":     severity,
                "risk_score":   risk_score,
                "risk_factors": state.get("risk_factors", []),
                "fee_excess_bps": state.get("fee_excess_bps", 0),
                "trailing_returns": state.get("trailing_returns", {}),
            },
            intent_action="draft_finding",
            intent_reason=(
                f"Draft {severity}-severity ERISA §404(a) finding for "
                f"fund {state['fund_id']} (score={risk_score:.3f}) — "
                f"internal only, not yet delivered"
            ),
        )

        import uuid
        finding_id = f"fw-{state['fund_id']}-{uuid.uuid4().hex[:8]}"

        logger.info(
            "draft_finding fund=%s severity=%s finding_id=%s",
            state["fund_id"], severity, finding_id,
        )

        return {
            "finding_draft":    draft,
            "finding_severity": severity,
            "finding_id":       finding_id,
        }

    # ── Routing ────────────────────────────────────────────────────────────

    def route_by_severity(self, state: WatchdogState) -> str:
        """
        Route to the appropriate emission path based on finding severity.

        Called after draft_finding. Returns a routing key consumed by
        the conditional edges in build_graph().
        """
        severity = state.get("finding_severity", "none") or "none"
        logger.debug(
            "route_by_severity fund=%s → %s",
            state.get("fund_id"), severity,
        )
        return severity

    # ── Output nodes ───────────────────────────────────────────────────────

    async def emit_low_finding(self, state: WatchdogState) -> dict:
        """
        Auto-emit a low-severity finding to the compliance dashboard.

        ALLOW by policy (policy.yaml) — no human approval needed.
        Low findings are informational and do not trigger regulatory reporting.
        """
        await self.run_effect(
            effect=FinancialEffect.COMPLIANCE_FINDING_EMIT_LOW,
            tool="compliance_dashboard",
            action="emit_finding",
            params={
                "finding_id":   state.get("finding_id"),
                "fund_id":      state["fund_id"],
                "plan_id":      state["plan_id"],
                "severity":     "low",
                "risk_score":   state.get("risk_score"),
                "finding":      state.get("finding_draft"),
            },
            intent_action="emit_low_finding",
            intent_reason=(
                "Emit low-severity ERISA monitoring finding to compliance "
                "dashboard — informational, no regulatory reporting obligation"
            ),
        )

        await self.run_effect(
            effect=FinancialEffect.FINDING_LOG_WRITE,
            tool="finding_store",
            action="log_finding",
            params={
                "finding_id":  state.get("finding_id"),
                "severity":    "low",
                "fund_id":     state["fund_id"],
                "plan_id":     state["plan_id"],
            },
            intent_action="log_finding",
            intent_reason="Persist finding record to the compliance audit trail",
        )

        logger.info(
            "emit_low_finding fund=%s finding_id=%s emitted",
            state["fund_id"], state.get("finding_id"),
        )
        return self.append_output(state, {
            "finding_id":   state.get("finding_id"),
            "severity":     "low",
            "fund_id":      state["fund_id"],
            "action_taken": "emitted_to_dashboard",
        })

    async def queue_for_review(self, state: WatchdogState) -> dict:
        """
        Add a high-severity finding to the human review queue.

        ALLOW by policy (policy.yaml) — queuing itself is permitted.
        The actual emission (emit_high_finding) is ASK — requires approval.
        """
        await self.run_effect(
            effect=FinancialEffect.HUMAN_REVIEW_QUEUE_ADD,
            tool="review_queue",
            action="add_to_queue",
            params={
                "finding_id":  state.get("finding_id"),
                "fund_id":     state["fund_id"],
                "plan_id":     state["plan_id"],
                "severity":    "high",
                "risk_score":  state.get("risk_score"),
                "risk_factors": state.get("risk_factors", []),
                "finding":     state.get("finding_draft"),
                "priority":    "urgent" if (state.get("risk_score") or 0) >= 0.90 else "high",
            },
            intent_action="queue_for_review",
            intent_reason=(
                "Queue high-severity ERISA finding for fiduciary committee review "
                "before formal emission — required process documentation for §404(a)"
            ),
        )

        logger.info(
            "queue_for_review fund=%s finding_id=%s queued for human review",
            state["fund_id"], state.get("finding_id"),
        )
        return {"review_queued": True}

    async def emit_high_finding(self, state: WatchdogState) -> dict:
        """
        Formally emit a high-severity finding after human review.

        ASK by policy (policy.yaml) — requires human approval before executing.
        When Tollgate raises TollgateDeferred, the Lambda handler returns a
        REPROMPT response to Bedrock, asking the compliance officer to confirm.

        In sandbox mode with AutoApprover, this runs unblocked.
        In production with SQSApprover, it pauses until a reviewer approves.
        """
        await self.run_effect(
            effect=FinancialEffect.COMPLIANCE_FINDING_EMIT_HIGH,
            tool="compliance_dashboard",
            action="emit_finding",
            params={
                "finding_id":   state.get("finding_id"),
                "fund_id":      state["fund_id"],
                "plan_id":      state["plan_id"],
                "severity":     "high",
                "risk_score":   state.get("risk_score"),
                "finding":      state.get("finding_draft"),
            },
            intent_action="emit_high_finding",
            intent_reason=(
                "Formally emit high-severity ERISA §404(a) finding after "
                "human review — triggers plan sponsor notification and "
                "initiates the fund remediation workflow"
            ),
        )

        await self.run_effect(
            effect=FinancialEffect.FINDING_LOG_WRITE,
            tool="finding_store",
            action="log_finding",
            params={
                "finding_id": state.get("finding_id"),
                "severity":   "high",
                "fund_id":    state["fund_id"],
                "plan_id":    state["plan_id"],
                "reviewed":   True,
            },
            intent_action="log_high_finding",
            intent_reason="Persist high-severity finding to audit trail with review timestamp",
        )

        logger.info(
            "emit_high_finding fund=%s finding_id=%s EMITTED after review",
            state["fund_id"], state.get("finding_id"),
        )
        return self.append_output(state, {
            "finding_id":   state.get("finding_id"),
            "severity":     "high",
            "fund_id":      state["fund_id"],
            "action_taken": "emitted_after_review",
        })


# ── Local sandbox runner ───────────────────────────────────────────────────────


async def _run_sandbox(fund_id: str = "FUND001", plan_id: str = "PLAN001") -> None:
    """
    Run the Fiduciary Watchdog Graph Agent in a local sandbox.

    Uses AutoApprover (auto-approves all ASK decisions) and a mock gateway.
    Suitable for local development and unit testing.

    Usage:
        python graph_agent.py
        python graph_agent.py FUND999 PLAN042
    """
    import json
    from pathlib import Path

    from arc.core.manifest import AgentManifest
    from tollgate.tower import ControlTower
    from tollgate import AutoApprover, JsonlAuditSink, YamlPolicyEvaluator
    from arc.core.gateway import GatewayConnector
    from arc.core.observability import OutcomeTracker

    manifest_path = Path(__file__).parent / "manifest.yaml"
    policy_path   = Path(__file__).parent / "policy.yaml"
    audit_path    = Path("/tmp") / f"audit-{fund_id}.jsonl"

    manifest = AgentManifest.from_yaml(str(manifest_path))
    policy   = YamlPolicyEvaluator(str(policy_path))
    approver = AutoApprover()
    audit    = JsonlAuditSink(str(audit_path))
    tower    = ControlTower(policy=policy, approver=approver, audit=audit)
    gateway  = GatewayConnector()
    tracker  = OutcomeTracker(path=f"/tmp/outcomes-{fund_id}.jsonl")

    agent = FiduciaryWatchdogGraphAgent(
        manifest=manifest,
        tower=tower,
        gateway=gateway,
        tracker=tracker,
    )

    print(f"\n{'='*60}")
    print(f"  Fiduciary Watchdog Graph Agent — sandbox run")
    print(f"  fund_id={fund_id}  plan_id={plan_id}")
    print(f"{'='*60}\n")

    final_state = await agent.execute(fund_id=fund_id, plan_id=plan_id)

    print("\n── Final State ────────────────────────────────────────────")
    print(json.dumps(
        {k: v for k, v in final_state.items() if not k.startswith("_")},
        indent=2, default=str,
    ))
    print(f"\n── Outputs: {len(final_state.get('outputs', []))} finding(s) emitted")
    for out in final_state.get("outputs", []):
        print(f"   {out.get('severity', '?')} → {out.get('action_taken', '?')}: "
              f"finding_id={out.get('finding_id', '?')}")

    if final_state.get("errors"):
        print(f"\n── Errors: {final_state['errors']}")

    print(f"\n── Audit log: {audit_path}")
    print("="*60)


if __name__ == "__main__":
    import sys
    fund_id = sys.argv[1] if len(sys.argv) > 1 else "FUND001"
    plan_id = sys.argv[2] if len(sys.argv) > 2 else "PLAN001"
    asyncio.run(_run_sandbox(fund_id=fund_id, plan_id=plan_id))
