# Agent Foundry

**The enterprise agent incubation platform.**

From idea to governed production agent, with a repeatable 6-stage pipeline
and a policy engine built for regulated industries.

Built on [Tollgate](https://github.com/ravi-labs/tollgate) — runtime enforcement
for AI agent tool calls using Identity + Intent + Policy.

---

## What is Agent Foundry?

Agent Foundry is the infrastructure layer that sits between your AI agents
and the real world. It provides:

- **A structured incubation pipeline** — 6 stages from Discover to Scale,
  with defined gate criteria, required artifacts, and sign-off at each step.

- **A financial services effect taxonomy** — 30+ named effects organized into
  6 tiers (Data Access → Computation → Draft → Output → Persistence → System),
  each with a default ALLOW/ASK/DENY decision and compliance metadata.

- **A policy engine powered by Tollgate** — YAML-declarative rules that
  translate regulatory requirements (ERISA, DOL, FINRA, AML/KYC) into
  machine-enforced agent behavior. Every tool call is checked before execution.

- **A shared agent scaffold** — `BaseAgent` wires every agent to the
  ControlTower by default. Manifest-declared effects are enforced at runtime.
  Agents that attempt undeclared effects are blocked, not just warned.

- **A Gateway abstraction layer** — all data access goes through connectors
  that are declared in the manifest and logged centrally.

- **An outcome tracker** — records JSONL events for every agent output,
  enabling the ROI proof points defined in each agent's success metrics.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        Your Agent                           │
│              (subclass of BaseAgent)                        │
└─────────────────────────┬───────────────────────────────────┘
                          │ run_effect()
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                  EffectRequestBuilder                        │
│  FinancialEffect → resource_type + base Effect mapping       │
└─────────────────────────┬───────────────────────────────────┘
                          │ ToolRequest
                          ▼
┌─────────────────────────────────────────────────────────────┐
│              Tollgate ControlTower (vendored)                │
│  Identity → Circuit Breaker → Rate Limit → Policy →         │
│  Network Guard → Schema Validation → ALLOW/ASK/DENY         │
└──────────┬──────────────┬───────────────────────────────────┘
           │ ALLOW        │ ASK
           │              ▼
           │   ┌──────────────────────────┐
           │   │  Human Review Queue      │
           │   │  (Approver / GrantStore) │
           │   └──────────┬───────────────┘
           │              │ APPROVED
           ▼              ▼
┌─────────────────────────────────────────────────────────────┐
│                     Tool Execution                           │
│              + Cryptographic Audit Log                       │
└─────────────────────────────────────────────────────────────┘
```

---

## The Incubation Pipeline

| Stage | From → To | Key Gate |
|-------|-----------|----------|
| **1. Discover** | Idea → Validated opportunity | Go/No-Go scorecard |
| **2. Shape** | Opportunity → Scoped POC | AgentManifest + success metrics |
| **3. Build** | Scoped POC → Working agent | Sandbox tests + edge case log |
| **4. Validate** | Working agent → Proven value | ROI report vs. baseline |
| **5. Govern** | Proven value → Compliance approved | Compliance officer sign-off |
| **6. Scale** | Approved → Live in production | Runbook + monitoring in place |

Agents can only be promoted by the lifecycle manager — never by themselves.
`agent.promote` is a hard DENY in the policy engine.

---

## Financial Services Effect Taxonomy

Effects are the atomic actions an agent can take. Every `run_effect()` call
declares an effect from the taxonomy.

| Tier | Examples | Default |
|------|---------|---------|
| 1 — Data Access | `participant.data.read`, `fund.fees.read` | ALLOW |
| 2 — Computation | `risk.score.compute`, `scenario.model.execute` | ALLOW |
| 3 — Draft | `intervention.draft`, `finding.draft` | ALLOW |
| 4 — Output | `participant.communication.send`, `compliance.finding.emit.high` | ASK |
| 5 — Persistence | `audit.log.write`, `outcome.log.write` | ALLOW |
| 6 — System | `agent.promote`, `account.transaction.execute` | DENY |

---

## Quick Start

```bash
pip install agent-foundry

# Run a reference implementation
python examples/retirement_trajectory/agent.py

# Browse the effect taxonomy
foundry effects list

# Scaffold a new agent
foundry agent new my-first-agent

# Validate a manifest
foundry agent validate my-first-agent/manifest.yaml
```

---

## Multi-Team Platform

Agent Foundry is designed as a platform where multiple teams build agents independently:

```
agent-foundry (this repo)     ← Framework: shared infrastructure
    pip install ↓
team-repo-A/agents/            ← Team A builds their agents
team-repo-B/agents/            ← Team B builds their agents
                ↓ registry PR
agent-registry/registry/       ← Governance catalog (manifests only)
```

See [docs/platform-architecture.md](docs/platform-architecture.md) for the full architecture.
New teams: start with [docs/team-onboarding.md](docs/team-onboarding.md).

---

## CLI Reference

```bash
# Agent commands
foundry agent new <name>          # Scaffold a new agent
foundry agent list                # List all agents in current directory
foundry agent validate [path]     # Validate a manifest.yaml
foundry agent promote [path]      # Promote to next lifecycle stage
foundry agent suspend [path]      # Suspend an agent (kill switch)
foundry agent resume [path]       # Resume a suspended agent

# Registry commands
foundry registry submit [path]    # Prepare manifest for registry PR
foundry registry list             # List all registered agents

# Effect taxonomy
foundry effects list              # Browse all 30 effects
foundry effects list --tier 4     # Filter by tier
foundry effects show <effect>     # Show effect details
```

---

## Project Structure

```
agent-foundry/
├── src/foundry/
│   ├── cli/               # foundry CLI (agent, registry, effects commands)
│   ├── tollgate/          # Vendored Tollgate policy engine
│   ├── policy/            # FinancialEffect taxonomy + ToolRequest builder
│   ├── scaffold/          # BaseAgent + AgentManifest + AgentStatus (kill switch)
│   ├── gateway/           # Data access abstraction
│   ├── lifecycle/         # Incubation pipeline stages + gate criteria
│   ├── observability/     # Outcome tracker for ROI measurement
│   └── registry/          # Registry catalog generation
├── policies/
│   └── financial_services/
│       ├── defaults.yaml  # Default ALLOW/ASK/DENY per financial effect
│       └── erisa.yaml     # ERISA/DOL/FINRA regulatory overlay (immutable)
├── examples/
│   ├── retirement_trajectory/    # Retirement risk + personalized intervention
│   ├── fiduciary_watchdog/       # ERISA §404(a) fund monitoring + compliance findings
│   ├── life_event_anticipation/  # Behavioral signal detection + advisor routing
│   └── plan_design_optimizer/    # Scenario modeling + RM recommendation delivery
├── docs/
│   ├── platform-architecture.md  # Full platform design
│   └── team-onboarding.md        # Getting started guide for teams
└── tests/
    ├── test_effects.py    # Taxonomy completeness and tier tests
    ├── test_manifest.py   # Manifest loading, validation, kill switch
    ├── test_base_agent.py # Permission enforcement, kill switch, effect passthrough
    ├── test_builder.py    # EffectRequestBuilder correctness
    └── test_tracker.py    # OutcomeTracker persistence and querying
```

---

## Relationship to Tollgate

Tollgate provides the policy enforcement engine — the ControlTower,
YAML policy evaluator, audit logging, approvals, and circuit breaker.

Agent Foundry builds the incubation layer on top:
- The **FinancialEffect taxonomy** maps to Tollgate's `resource_type`
- The **AgentManifest** carries the `manifest_version` Tollgate requires for ALLOW
- The **BaseAgent scaffold** wires every agent to the ControlTower by default
- The **lifecycle pipeline** governs which stage an agent is in and what it can do

Both repos live under [ravi-labs](https://github.com/ravi-labs).

---

## License

Apache-2.0. See [LICENSE](LICENSE).
