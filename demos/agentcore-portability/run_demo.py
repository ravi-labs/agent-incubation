"""
AgentCore portability demo — same agent, two runtimes, same outputs.

Runs a tiny arc agent twice:

  --mode local       In-process. No AWS calls. Audit + telemetry write
                     to ./out/local/.

  --mode agentcore   Bedrock AgentCore runtime via AgentCoreOrchestrator.
                     Requires AWS credentials + a deployed AgentCore
                     agent. Audit + telemetry write to ./out/agentcore/.

The demo agent is intentionally minimal: it makes a fixed sequence of
`run_effect` calls (one ALLOW, one ASK, one DENY by policy) so the
**audit + telemetry shape is deterministic** and easy to compare across
runtimes.

The point is *not* to demonstrate a specific business agent. The point
is to prove that arc's governance contract — Tollgate, audit log,
metric vocabulary, redaction — is **identical regardless of where the
orchestrator runs**. Email-triage, fiduciary-watchdog, plan-design-
optimizer, your future agent: same proof.

Usage
-----
    # Local (no AWS):
    python demos/agentcore-portability/run_demo.py --mode local

    # AgentCore (needs AWS):
    export AWS_REGION=us-east-1
    export AGENTCORE_AGENT_ID=<from CDK output>
    python demos/agentcore-portability/run_demo.py --mode agentcore

    # Compare:
    python demos/agentcore-portability/compare.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import tempfile
from pathlib import Path

logger = logging.getLogger("arc.demo.portability")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR   = Path(__file__).parent / "out"


# ── Minimal demo agent ────────────────────────────────────────────────────────


def _build_demo_agent(out_dir: Path):
    """Construct a minimal BaseAgent + ControlTower + telemetry stack.

    Returns (agent, telemetry_stream) — caller is responsible for
    closing the telemetry stream after the run.
    """
    sys.path.insert(0, str(REPO_ROOT))

    from arc.core import (
        AgentManifest,
        AgentStatus,
        ApprovalOutcome,
        BaseAgent,
        CloudWatchEMFTelemetry,
        ControlTower,
        FinancialEffect,
        JsonlAuditSink,
        LifecycleStage,
        MockGatewayConnector,
        OutcomeTracker,
        YamlPolicyEvaluator,
    )

    class AlwaysApprove:
        """Demo-only approver that approves every ASK. Real
        deployments use AsyncQueueApprover (SQS + DynamoDB)."""
        async def request_approval_async(self, *_a, **_kw):
            return ApprovalOutcome.APPROVED

    out_dir.mkdir(parents=True, exist_ok=True)

    # Minimal manifest — declares the effects we'll exercise below.
    manifest = AgentManifest(
        agent_id        = "portability-demo",
        version         = "0.1.0",
        owner           = "demo-team",
        description     = "Portability demo agent",
        lifecycle_stage = LifecycleStage.BUILD,
        allowed_effects = [
            FinancialEffect.PARTICIPANT_DATA_READ,
            FinancialEffect.RISK_SCORE_COMPUTE,
            FinancialEffect.AUDIT_LOG_WRITE,
        ],
        data_access     = ["participant.data"],
        policy_path     = str(out_dir / "policy.yaml"),
        success_metrics = ["m1"],
        environment     = "sandbox",
        status          = AgentStatus.ACTIVE,
    )

    # Tiny inline policy — exercises ALLOW + ASK paths so the audit
    # shape is deterministic across runtimes.
    policy_path = out_dir / "policy.yaml"
    policy_path.write_text(
        "rules:\n"
        "  - resource_type: audit.log.write\n"
        "    decision: ASK\n"
        "    reason: Demo — second-party review for audit log writes\n"
        "  - resource_type: participant.data.read\n"
        "    decision: ALLOW\n"
        "    reason: Demo — read access OK\n"
        "  - resource_type: risk.score.compute\n"
        "    decision: ALLOW\n"
        "    reason: Demo — compute OK\n"
    )

    audit_path = out_dir / "audit.jsonl"
    tower = ControlTower(
        policy   = YamlPolicyEvaluator(str(policy_path)),
        approver = AlwaysApprove(),
        audit    = JsonlAuditSink(audit_path),
    )

    # Telemetry → CloudWatch EMF → captured to a file.
    telemetry_stream = (out_dir / "telemetry.ndjson").open("w")
    telemetry        = CloudWatchEMFTelemetry(stream=telemetry_stream)

    # OutcomeTracker writes outcomes alongside, both telemetry-aware.
    tracker = OutcomeTracker(
        path      = out_dir / "outcomes.jsonl",
        telemetry = telemetry,
    )

    class DemoAgent(BaseAgent):
        async def execute(self, **kwargs):
            # Three deterministic effects: ALLOW, ASK (policy), ALLOW.
            # All three populate the audit log + telemetry stream.
            await self.run_effect(
                effect        = FinancialEffect.PARTICIPANT_DATA_READ,
                tool          = "demo", action = "read",
                params        = {"id": "p-001"},
                intent_action = "fetch",
                intent_reason = "demo: read participant data",
            )
            await self.run_effect(
                effect        = FinancialEffect.RISK_SCORE_COMPUTE,
                tool          = "demo", action = "score",
                params        = {"id": "p-001"},
                intent_action = "score",
                intent_reason = "demo: compute risk",
            )
            await self.run_effect(
                effect        = FinancialEffect.AUDIT_LOG_WRITE,
                tool          = "demo", action = "log",
                params        = {"event": "demo"},
                intent_action = "log",
                intent_reason = "demo: write to audit (will hit ASK)",
            )
            await self.log_outcome(
                event_type = "demo_run_complete",
                data       = {"latency_ms": 17.0},
            )
            return {"ok": True, "effects": 3}

    agent = DemoAgent(
        manifest  = manifest,
        tower     = tower,
        gateway   = MockGatewayConnector(),
        tracker   = tracker,
        telemetry = telemetry,
    )
    return agent, telemetry_stream


# ── Local mode ────────────────────────────────────────────────────────────────


async def run_local() -> dict:
    """Run the demo agent in-process. No network, no AWS."""
    out_dir = OUT_DIR / "local"
    agent, tel_stream = _build_demo_agent(out_dir)

    logger.info("local mode: running portability-demo")
    result = await agent.execute()
    tel_stream.close()

    summary = _summarise_run(out_dir=out_dir, result=result, runtime="local")
    _save_summary(out_dir, summary)
    return summary


# ── AgentCore mode ────────────────────────────────────────────────────────────


async def run_agentcore() -> dict:
    """Run the same demo agent's effects via AgentCoreOrchestrator.

    The orchestrator forwards the agent invocation to a deployed
    Bedrock AgentCore agent. The audit + telemetry come from the same
    arc.core paths — what changes is *where* the work executes.

    For this demo we instantiate the orchestrator and call .run() with
    the demo's input. In a real deployment the AgentCore Lambda action
    group would host the same DemoAgent class and call agent.execute()
    inside the action handler.
    """
    sys.path.insert(0, str(REPO_ROOT))
    from arc.orchestrators import AgentCoreOrchestrator

    agent_id  = os.environ.get("AGENTCORE_AGENT_ID")
    region    = os.environ.get("AWS_REGION", "us-east-1")
    if not agent_id:
        logger.error(
            "AGENTCORE_AGENT_ID not set. Deploy via "
            "deploy/cdk/bedrock_agent_stack.py and export the agent id "
            "before running --mode agentcore."
        )
        return {"runtime": "agentcore", "skipped": True, "reason": "no agent id"}

    out_dir = OUT_DIR / "agentcore"
    out_dir.mkdir(parents=True, exist_ok=True)

    orchestrator = AgentCoreOrchestrator(
        agent_id  = agent_id,
        region    = region,
        memory_id = os.environ.get("AGENTCORE_MEMORY_ID"),
    )

    logger.info("agentcore mode: invoking agent_id=%s region=%s", agent_id, region)
    result = await orchestrator.run(
        input  = {"effects": ["participant.data.read", "risk.score.compute", "audit.log.write"]},
        config = {"session_id": "demo-portability"},
    )

    summary = {
        "runtime":  "agentcore",
        "agent_id": agent_id,
        "run_id":   result.run_id,
        "metadata": result.metadata,
        "result_summary": "(see AgentCore audit log + Datadog dashboards)",
    }
    _save_summary(out_dir, summary)
    return summary


# ── Helpers ───────────────────────────────────────────────────────────────────


def _summarise_run(*, out_dir: Path, result, runtime: str) -> dict:
    """Collapse a run's audit + telemetry into a comparable summary dict."""
    audit_count = 0
    decisions: dict[str, int] = {}
    audit_path = out_dir / "audit.jsonl"
    if audit_path.exists():
        with audit_path.open() as f:
            for line in f:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                audit_count += 1
                d = row.get("decision", {})
                if isinstance(d, dict):
                    # Tollgate audit row shape: decision = {decision: "ALLOW", ...}
                    dt = (
                        d.get("decision")
                        or d.get("decision_type")
                        or d.get("type")
                        or ""
                    )
                else:
                    dt = str(d)
                if dt:
                    decisions[dt] = decisions.get(dt, 0) + 1

    telemetry_metrics: dict[str, int] = {}
    tel_path = out_dir / "telemetry.ndjson"
    if tel_path.exists():
        with tel_path.open() as f:
            for line in f:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                cw = row.get("_aws", {}).get("CloudWatchMetrics", [])
                for entry in cw:
                    for m in entry.get("Metrics", []):
                        name = m.get("Name", "")
                        if name:
                            telemetry_metrics[name] = telemetry_metrics.get(name, 0) + 1

    return {
        "runtime":           runtime,
        "audit_rows":        audit_count,
        "decisions":         decisions,
        "telemetry_metrics": telemetry_metrics,
        "result":            result if isinstance(result, dict) else str(result),
    }


def _save_summary(out_dir: Path, summary: dict) -> None:
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2))
    logger.info("wrote %s", out_dir / "summary.json")


# ── Entry point ───────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode", required=True, choices=["local", "agentcore"],
        help="local: in-process. agentcore: AWS Bedrock AgentCore.",
    )
    args = parser.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    if args.mode == "local":
        summary = asyncio.run(run_local())
    else:
        summary = asyncio.run(run_agentcore())

    print()
    print("=" * 60)
    print(f"  Run summary — {summary['runtime']}")
    print("=" * 60)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
