"""
foundry.harness — sandbox layer for agent development and testing.

Provides everything needed to run any agent locally against synthetic
fixture data, without touching real external systems.

Quick start:
    from foundry.harness import HarnessBuilder

    agent = (
        HarnessBuilder(manifest="examples/email_triage/manifest.yaml",
                       policy="examples/email_triage/policy.yaml")
        .with_fixtures("examples/email_triage/fixtures/emails.yaml")
        .build(EmailTriageAgent)
    )

    report = await agent.run_harness()
    report.print()

The harness:
  - Routes all gateway fetches to in-memory fixture data
  - Captures every ALLOW/ASK/DENY decision in a ShadowAuditSink
  - Runs in shadow mode by default (Tier 4+ effects logged, not executed)
  - Produces a DecisionReport showing exactly what the agent would have done
"""

from .approver import SandboxApprover
from .builder import HarnessBuilder
from .report import DecisionReport
from .shadow import ShadowAuditSink
from .fixtures import FixtureLoader

__all__ = [
    "HarnessBuilder",
    "SandboxApprover",
    "ShadowAuditSink",
    "DecisionReport",
    "FixtureLoader",
]
