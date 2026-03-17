"""
Example Agent — Template Implementation

Replace this with your actual agent logic.
See examples/ in agent-foundry for full reference implementations.
"""

import asyncio
import logging
from pathlib import Path

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


class ExampleAgent(BaseAgent):
    """
    Example Agent.

    Replace this docstring and implement execute() with your agent logic.
    All tool calls must go through self.run_effect().
    """

    async def execute(self, **kwargs) -> dict:
        results = {"processed": 0, "errors": 0}

        # ── Step 1: Fetch data via Gateway ──────────────────────────────────
        # response = await self.gateway.fetch(DataRequest(
        #     source="participant.data",
        #     params={"participant_id": "p-001"},
        # ))

        # ── Step 2: Run effects through ControlTower ─────────────────────────
        # result = await self.run_effect(
        #     effect=FinancialEffect.PARTICIPANT_DATA_READ,
        #     tool="my_tool",
        #     action="my_action",
        #     params={"key": "value"},
        #     intent_action="describe_what_you_are_doing",
        #     intent_reason="Explain why this action is taken",
        #     exec_fn=lambda: {"data": "result"},
        # )

        # ── Step 3: Log outcomes ──────────────────────────────────────────────
        # await self.log_outcome("event_name", {"key": "value"})

        logger.info("Run complete: %s", results)
        return results


def build_agent() -> ExampleAgent:
    manifest = load_manifest(MANIFEST_PATH)

    policy = YamlPolicyEvaluator(POLICY_PATH)
    approver = AutoApprover(default_outcome="approved")
    audit = JsonlAuditSink("audit.jsonl")
    tower = ControlTower(policy=policy, approver=approver, audit=audit)

    gateway = MockGatewayConnector({
        # Add your mock data sources here:
        # "participant.data": {...},
    })

    tracker = OutcomeTracker(path="outcomes.jsonl")

    return ExampleAgent(
        manifest=manifest,
        tower=tower,
        gateway=gateway,
        tracker=tracker,
    )


async def main():
    agent = build_agent()
    results = await agent.execute()
    print("\nResults:", results)

    if agent.tracker:
        print("Outcomes:", agent.tracker.summary())


if __name__ == "__main__":
    asyncio.run(main())
