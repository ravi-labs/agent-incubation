"""
arc.harness — sandbox testing layer.

Provides everything needed to run any arc agent locally against synthetic
fixture data, without touching real external systems.

Quick start:
    from arc.harness import HarnessBuilder
    from arc.core import ITSMEffect

    report = await (
        HarnessBuilder(manifest="manifest.yaml", policy="policy.yaml")
        .with_fixtures("fixtures/emails.yaml")
        .run(EmailTriageAgent, email_ids=["e-001"])
    )
    report.print()

Native after migration module 5 — replaces the prior re-export stub.

The harness:
  - Routes all gateway fetches to in-memory fixture data
  - Captures every ALLOW/ASK/DENY decision in a ShadowAuditSink
  - Auto-approves every ASK via SandboxApprover
  - Produces a DecisionReport showing exactly what the agent would have done

Swapping to production:
    Replace HarnessBuilder with RuntimeBuilder from arc.runtime. The agent
    class, manifest, and policy stay identical.
"""

from .approver import SandboxApprover
from .builder import HarnessBuilder
from .fixtures import FixtureLoader
from .report import DecisionReport
from .shadow import ShadowAuditSink

__all__ = [
    "HarnessBuilder",
    "SandboxApprover",
    "ShadowAuditSink",
    "DecisionReport",
    "FixtureLoader",
]
