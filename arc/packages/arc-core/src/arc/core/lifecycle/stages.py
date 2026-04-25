"""
Lifecycle stages for the agent incubation pipeline.

Every agent moves through six stages, each with defined entry criteria,
exit gates, and artifacts produced. Promotion between stages is recorded
in the manifest and requires sign-off from the appropriate reviewer.

Stage flow:
  DISCOVER → SHAPE → BUILD → VALIDATE → GOVERN → SCALE
"""

from dataclasses import dataclass, field
from enum import Enum


class LifecycleStage(str, Enum):
    """
    The six stages of the agent incubation pipeline.
    """
    DISCOVER  = "DISCOVER"   # Idea → Validated opportunity
    SHAPE     = "SHAPE"      # Opportunity → Scoped POC
    BUILD     = "BUILD"      # Scoped POC → Working agent (sandbox)
    VALIDATE  = "VALIDATE"   # Working agent → Proven value (ROI evidence)
    GOVERN    = "GOVERN"     # Proven value → Compliance approved
    SCALE     = "SCALE"      # Approved → Live in production

    @property
    def is_pre_production(self) -> bool:
        return self in (
            LifecycleStage.DISCOVER,
            LifecycleStage.SHAPE,
            LifecycleStage.BUILD,
            LifecycleStage.VALIDATE,
            LifecycleStage.GOVERN,
        )

    @property
    def requires_sandbox(self) -> bool:
        """Stages that must run in sandbox, never production."""
        return self in (LifecycleStage.BUILD, LifecycleStage.VALIDATE)

    def next_stage(self) -> "LifecycleStage | None":
        """Return the next stage, or None if already at SCALE."""
        stages = list(LifecycleStage)
        idx = stages.index(self)
        return stages[idx + 1] if idx < len(stages) - 1 else None


@dataclass(frozen=True)
class StageGate:
    """
    Gate criteria and artifacts for a lifecycle stage.
    Each stage has required artifacts that must exist before promotion.
    """
    stage: LifecycleStage
    label: str
    description: str
    entry_criteria: list[str]
    exit_artifacts: list[str]
    reviewer: str                      # Who approves the promotion
    environment: str                   # "sandbox" or "production"
    tags: list[str] = field(default_factory=list)


_STAGE_GATES: dict[LifecycleStage, StageGate] = {

    LifecycleStage.DISCOVER: StageGate(
        stage=LifecycleStage.DISCOVER,
        label="Discover",
        description="Identify and qualify high-value automation opportunities.",
        entry_criteria=[
            "Intake form submitted with business problem description",
            "Data sources identified and assessed for availability",
            "Rough ROI hypothesis stated",
        ],
        exit_artifacts=[
            "Go/No-Go scorecard (impact × feasibility × regulatory risk)",
            "Named data owner and business sponsor",
        ],
        reviewer="business_sponsor",
        environment="none",
    ),

    LifecycleStage.SHAPE: StageGate(
        stage=LifecycleStage.SHAPE,
        label="Shape",
        description="Define agent scope, success metrics, and policy boundaries.",
        entry_criteria=[
            "DISCOVER gate artifacts complete",
            "Business sponsor signed off on Go decision",
        ],
        exit_artifacts=[
            "AgentManifest YAML (agent_id, allowed_effects, data_access, policy_path)",
            "Success metrics baseline (current-state measurement)",
            "Draft policy YAML with effect rules",
            "Human-in-loop map: what requires human approval",
        ],
        reviewer="product_owner",
        environment="none",
    ),

    LifecycleStage.BUILD: StageGate(
        stage=LifecycleStage.BUILD,
        label="Build",
        description="Build the working agent in a sandboxed environment.",
        entry_criteria=[
            "SHAPE artifacts complete and signed off",
            "Sandbox environment provisioned",
            "Synthetic or permissioned test data available",
        ],
        exit_artifacts=[
            "Working agent passing end-to-end sandbox tests",
            "Policy YAML finalized and passing policy test suite",
            "Edge case log from testing",
            "Demo-ready build for stakeholder review",
        ],
        reviewer="tech_lead",
        environment="sandbox",
    ),

    LifecycleStage.VALIDATE: StageGate(
        stage=LifecycleStage.VALIDATE,
        label="Validate",
        description="Run agent alongside existing process; prove ROI.",
        entry_criteria=[
            "BUILD gate artifacts complete",
            "Controlled cohort defined for parallel run",
            "Baseline metrics captured",
        ],
        exit_artifacts=[
            "ROI report: time saved, accuracy, cost delta vs baseline",
            "Error rate analysis and failure mode documentation",
            "Go/No-Go recommendation from business owner",
            "Outcome log (did participants/plans act on agent output?)",
        ],
        reviewer="business_owner",
        environment="sandbox",
    ),

    LifecycleStage.GOVERN: StageGate(
        stage=LifecycleStage.GOVERN,
        label="Govern",
        description="Compliance, risk, and regulatory review before production.",
        entry_criteria=[
            "VALIDATE gate artifacts complete with positive recommendation",
            "Compliance officer review scheduled",
        ],
        exit_artifacts=[
            "Governance sign-off document (compliance officer signature)",
            "Regulatory assessment: ERISA, FINRA, AML/KYC coverage",
            "Policy YAML final version with compliance attestation",
            "Data privacy and security review complete",
            "Human override protocols documented",
            "Production monitoring requirements defined",
        ],
        reviewer="compliance_officer",
        environment="sandbox",
        tags=["erisa", "finra", "aml", "fiduciary"],
    ),

    LifecycleStage.SCALE: StageGate(
        stage=LifecycleStage.SCALE,
        label="Scale",
        description="Deploy to production with monitoring and a feedback loop.",
        entry_criteria=[
            "GOVERN gate artifacts complete with compliance sign-off",
            "Production environment provisioned",
            "Runbook authored and reviewed",
            "Alerting thresholds configured",
        ],
        exit_artifacts=[
            "Live agent in production",
            "Operations runbook",
            "Monitoring dashboard with agreed KPIs",
            "Quarterly review cadence scheduled",
        ],
        reviewer="operations_owner",
        environment="production",
    ),
}


def stage_gate(stage: LifecycleStage) -> StageGate:
    """Return the StageGate definition for a given lifecycle stage."""
    return _STAGE_GATES[stage]
