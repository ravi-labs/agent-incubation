# Arc

**Every agent follows an arc — from shadow to autonomous.**

Arc is a governed agent incubation platform. Developers build agents with typed governance built in. Business users pilot agents safely before they act autonomously. Every action is declared, policy-evaluated, and audited before execution.

---

## Packages

| Package | Purpose | Audience |
|---|---|---|
| `arc-core` | Governance engine — typed effects, ControlTower, policy, audit | Agent developers |
| `arc-harness` | Local testing — fixtures, shadow mode, decision reports | Agent developers |
| `arc-orchestrators` | Framework adapters — LangGraph, AgentCore, Strands | Agent developers |
| `arc-connectors` | Real system integrations — Outlook, Pega, ServiceNow | Platform / DevOps |
| `arc-runtime` | Production wiring — RuntimeConfig, env-var driven | Platform / DevOps |
| `arc-platform` | FastAPI backend + two React dashboards (`frontend/ops` for business users, `frontend/dev` for engineers) | Platform team |

---

## Quick start

```python
# Build and test an agent in harness mode
from arc.core import BaseAgent, ITSMEffect
from arc.harness import HarnessBuilder

class EmailTriageAgent(BaseAgent):
    async def execute(self, **kwargs):
        # Every action goes through governance
        result = await self.run_effect(
            effect=ITSMEffect.EMAIL_CLASSIFY,
            tool="classifier", action="classify",
            params={"email_id": "e-001"},
            intent_action="classify_email",
            intent_reason="Determine intent and priority",
        )
        return result

# Run against synthetic fixtures
report = await (
    HarnessBuilder(manifest="manifest.yaml", policy="policy.yaml")
    .with_fixtures("fixtures/emails.yaml")
    .run(EmailTriageAgent)
)
report.print()
```

```python
# Swap to production — one line change
from arc.runtime import RuntimeBuilder, RuntimeConfig

config = RuntimeConfig.from_env()   # reads OUTLOOK_*, PEGA_*, SNOW_* vars
config.validate_for_agent(["outlook", "pega_case"])

agent = RuntimeBuilder(config, manifest="manifest.yaml", policy="policy.yaml") \
    .build(EmailTriageAgent)
```

---

## Autonomy progression

Every agent earns its autonomy level through proven performance:

```
Shadow   →  Human in Loop  →  Confidence-based  →  Human on Loop
  L1            L2                  L3                   L4
no exec    every action ASK    auto above 0.85       fully autonomous
                                                    kill switch active
```

Promotion is triggered by the agent builder when quality gates pass.
Auto-demotion fires when the anomaly detector triggers.

---

## Domain taxonomies (domains/)

| Domain | Taxonomy | Hard denies |
|---|---|---|
| Financial / Retirement | `FinancialEffect` | account.transaction.execute |
| ITSM / Support | `ITSMEffect` | email.bulk.delete, sla.breach.suppress |
| Compliance / ERISA | `ComplianceEffect` | regulatory.filing.submit |
| Healthcare (generic) | `HealthcareEffect` | clinical.order.execute |

---

## Swap between orchestrators

```python
from arc.orchestrators import LangGraphOrchestrator, AgentCoreOrchestrator

# Development — LangGraph with in-memory checkpointer
orchestrator = LangGraphOrchestrator(graph=my_graph)

# Production — LangGraph on AgentCore (memory + sessions managed)
orchestrator = AgentCoreOrchestrator(
    agent_id  = "email-triage-v1",
    memory_id = "mem-abc123",
    graph     = my_graph,
)

# Future — Strands (stub ready)
# orchestrator = StrandsOrchestrator(model_id="...")
```

Agent code never changes. Only the orchestrator line in the builder.

---

## Project structure

```
arc/
  packages/
    arc-core/           governance engine
    arc-harness/        dev/test layer
    arc-orchestrators/  framework adapters
    arc-connectors/     real system integrations
    arc-runtime/        production wiring
    arc-platform/       FastAPI backend + ops/dev React dashboards
  domains/
    financial/          FinancialEffect
    itsm/               ITSMEffect
    compliance/         ComplianceEffect
    healthcare/         HealthcareEffect
  agents/
    email-triage/       first agent (POC → Phase 1)
    care-coordinator/   example
    contract-review/    example
```
