# Agent Incubation Platform

**Enterprise AI agent incubation — from idea to governed production agent.**

A platform for building agents that are safe to ship in regulated domains
(financial services, healthcare, legal, ITSM, compliance). Every agent action
is **declared in a manifest, evaluated by policy, and audited** before it runs.

Powered by [Tollgate](tollgate/) — runtime enforcement using
Identity + Intent + Policy.

---

## What's in this repo

```
agent-incubation/
├── agent-foundry/        ← Active implementation: framework, CLI, scaffold, examples, docs
├── arc/                  ← Future packaging surface: multi-package monorepo (in migration)
├── agent-registry/       ← Central governance catalog: manifests only, no code
├── agent-team-template/  ← Starter template: copy this to bootstrap a new team repo
├── tollgate/             ← Vendored policy engine (ControlTower, evaluator, circuit breaker)
└── docs/                 ← Vision, research, and marketing artifacts
    ├── vision/           ← platform vision, project plan, engineering overview
    ├── research/         ← typed-effect-scopes paper
    └── marketing/        ← competitive analysis, quickstart, interactive deck
```

### `agent-foundry/` — the active implementation
The mature, pip-installable framework. Today this is what every agent runs on.
Includes:
- **Effect taxonomies** — Financial (30+ effects across 6 tiers), plus Healthcare,
  Legal, ITSM, and Compliance taxonomies for domain-specific governance
- **BaseAgent scaffold** — every tool call routed through Tollgate's ControlTower,
  manifest-declared effects only, kill switch enforced at the class level
- **AgentManifest** — YAML artifact declaring scope, effects, lifecycle stage,
  success metrics
- **Harness layer** — sandbox testing with fixture data, shadow audit sink,
  decision reports, automatic ASK-approval in sandbox mode
- **`foundry` CLI** — scaffold, validate, promote, suspend, registry commands
- **Audit dashboard** — self-contained HTML report generated from JSONL audit logs
- **Reference agents** — retirement trajectory, fiduciary watchdog, life event
  anticipation, plan design optimizer (financial); email triage (ITSM);
  care coordinator (healthcare); contract review (legal)
- **Full documentation** under [agent-foundry/docs/](agent-foundry/docs/)

### `arc/` — the future packaging surface
Multi-package monorepo (`arc-core`, `arc-harness`, `arc-orchestrators`,
`arc-connectors`, `arc-runtime`, `arc-platform`). Today most arc packages
**re-export from agent-foundry**; the migration is happening module by module.

Net-new in arc, not in foundry:
- **arc-orchestrators** — common protocol with adapters for LangGraph, AgentCore,
  and Strands so agent code is orchestrator-agnostic
- **arc-connectors** — Outlook, Pega (case + knowledge), ServiceNow, plus a mock
  for tests, all behind a common base interface
- **arc-runtime** — `RuntimeConfig.from_env()` + `RuntimeBuilder` for production
  wiring (mirror of `HarnessBuilder` in foundry)

See [arc/README.md](arc/README.md) for the package layout and target API.

### `agent-registry/`
The governance catalog. Teams submit a PR here when an agent is ready for
compliance review. Manifests only — no business logic. Each PR requires
compliance-team sign-off.

### `agent-team-template/`
Boilerplate for a new team's agent repo. Copy it, install foundry, and build.
Contains a working scaffold with manifest, policy, agent stub, and tests.

### `tollgate/`
Vendored policy engine: ControlTower, YAML policy evaluator, circuit breaker,
approval primitives. Imported by foundry; will be imported by arc once the
core migration completes.

### `docs/`
Planning and stakeholder artifacts — platform vision deck, project plan,
engineering overview, the typed-effect-scopes research paper, competitive
analysis, and the interactive HTML pipeline deck.

---

## Quick start

```bash
# Install the active framework
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

## The incubation pipeline

| Stage | Description | Gate |
|-------|-------------|------|
| **1. Discover** | Validate the opportunity | Go/No-Go scorecard |
| **2. Shape** | Define scope + manifest | AgentManifest + success metrics |
| **3. Build** | Implement + sandbox test | Tests pass, edge cases logged |
| **4. Validate** | Prove value vs. baseline | ROI report |
| **5. Govern** | Compliance sign-off | Officer approval |
| **6. Scale** | Live in production | Runbook + monitoring |

---

## Architecture & onboarding

- Platform architecture: [agent-foundry/docs/platform-architecture.md](agent-foundry/docs/platform-architecture.md)
- Engineering overview: [docs/vision/engineering-overview.docx](docs/vision/engineering-overview.docx)
- Team onboarding: [agent-foundry/docs/team-onboarding.md](agent-foundry/docs/team-onboarding.md)
- Effect reference: [agent-foundry/docs/effects-reference.md](agent-foundry/docs/effects-reference.md)

---

Apache-2.0 · [ravi-labs](https://github.com/ravi-labs)
