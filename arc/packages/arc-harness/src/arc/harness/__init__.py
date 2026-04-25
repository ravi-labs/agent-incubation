"""
arc.harness — sandbox testing layer.

Run any Arc agent locally against synthetic fixture data without
touching real external systems.

Quick start:
    from arc.harness import HarnessBuilder
    from arc.core import ITSMEffect

    report = await (
        HarnessBuilder(manifest="manifest.yaml", policy="policy.yaml")
        .with_fixtures("fixtures/emails.yaml")
        .run(EmailTriageAgent, email_ids=["e-001"])
    )
    report.print()

Swapping to production:
    Replace HarnessBuilder with RuntimeBuilder from arc.runtime.
    The agent class, manifest, and policy stay identical.
"""

from foundry.harness import (
    HarnessBuilder,
    SandboxApprover,
    ShadowAuditSink,
    DecisionReport,
    FixtureLoader,
)

__all__ = [
    "HarnessBuilder",
    "SandboxApprover",
    "ShadowAuditSink",
    "DecisionReport",
    "FixtureLoader",
]
