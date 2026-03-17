# Agent Incubation Platform

**Enterprise AI agent incubation — from idea to governed production agent.**

Built for financial services: retirement plans, wealth services, and private markets.
Powered by [Tollgate](https://github.com/ravi-labs/tollgate) — runtime enforcement
using Identity + Intent + Policy.

---

## What's in this repo

```
agent-incubation/
├── agent-foundry/        ← The framework: pip-installable, policy engine, CLI, scaffold
├── agent-registry/       ← Central governance catalog: manifests only, no code
├── agent-team-template/  ← Starter template: copy this to bootstrap a new team repo
└── deck/                 ← Interactive HTML deck: the incubation pipeline story
```

### `agent-foundry/`
The core platform — a pip-installable Python framework that every agent team depends on.
Includes:
- **FinancialEffect taxonomy** — 30 named effects across 6 tiers
- **BaseAgent scaffold** — all tool calls enforced through Tollgate's ControlTower
- **AgentManifest + kill switch** — YAML artifact, lifecycle stages, ACTIVE/SUSPENDED/DEPRECATED
- **ERISA/DOL/FINRA policies** — regulatory overlay baked in as a policy layer
- **`foundry` CLI** — scaffold, validate, promote, suspend, registry commands
- **4 reference agents** — fiduciary watchdog, retirement trajectory, life event, plan optimizer

### `agent-registry/`
The governance catalog. Teams submit a PR here when an agent is ready for compliance review.
Contains manifests only — no business logic. Each PR requires compliance-team sign-off.

### `agent-team-template/`
Boilerplate for a new team's agent repo. Copy it, `pip install agent-foundry`, and build.
Contains a working scaffold with manifest, policy, agent stub, and tests.

### `deck/`
An interactive HTML presentation covering the full incubation pipeline, agent concepts,
and platform architecture — for stakeholder walkthroughs.

---

## Quick start

```bash
# Install the framework
pip install -e agent-foundry/

# Browse the effect taxonomy
foundry effects list

# Scaffold a new agent
foundry agent new my-agent

# Run a reference implementation
python agent-foundry/examples/retirement_trajectory/agent.py

# Validate a manifest
foundry agent validate my-agent/manifest.yaml --strict
```

---

## The Incubation Pipeline

| Stage | Description | Gate |
|-------|-------------|------|
| **1. Discover** | Validate the opportunity | Go/No-Go scorecard |
| **2. Shape** | Define scope + manifest | AgentManifest + success metrics |
| **3. Build** | Implement + sandbox test | Tests pass, edge cases logged |
| **4. Validate** | Prove value vs. baseline | ROI report |
| **5. Govern** | Compliance sign-off | Officer approval |
| **6. Scale** | Live in production | Runbook + monitoring |

---

## Architecture

See [agent-foundry/docs/platform-architecture.md](agent-foundry/docs/platform-architecture.md)

New teams: [agent-foundry/docs/team-onboarding.md](agent-foundry/docs/team-onboarding.md)

---

Apache-2.0 · [ravi-labs](https://github.com/ravi-labs)
