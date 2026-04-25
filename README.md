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
├── arc/                  ← THE PLATFORM. Multi-package monorepo, native, fully tested.
├── agent-foundry/        ← Back-compat shim package. All source files are thin re-exports of arc.*
├── agent-registry/       ← Central governance catalog: manifests only, no code
├── agent-team-template/  ← Starter template: copy this to bootstrap a new team repo
├── tollgate/             ← Vendored policy engine (ControlTower, evaluator, circuit breaker)
├── policies/             ← Shared policy YAMLs (financial-services, etc.)
├── deploy/               ← Container + CDK + ECS deployment artifacts
└── docs/                 ← Vision, research, marketing, and the migration plan
    ├── vision/           ← platform vision, project plan, engineering overview
    ├── research/         ← typed-effect-scopes paper
    ├── marketing/        ← competitive analysis, quickstart, interactive deck
    ├── foundry-legacy/   ← original agent-foundry/docs (concepts apply, paths reference foundry.*)
    └── migration-plan.md ← module-by-module foundry → arc migration history
```

### `arc/` — the platform

Native, self-contained, fully tested. This is what every agent runs on:

- **arc-core** — governance engine. Effect taxonomies (Financial 30+, Healthcare,
  Legal, ITSM, Compliance), `EffectRequestBuilder`, `AgentManifest`, `BaseAgent`,
  `gateway`, `memory`, `tools`, `observability`, lifecycle stages + promotion
  pipeline (`PromotionService`, gates, audit log), `RegistryCatalog`.
- **arc-harness** — sandbox testing with fixture data, shadow audit sink,
  decision reports, automatic ASK-approval in sandbox mode.
- **arc-runtime** — `RuntimeConfig.from_env()` + `RuntimeBuilder` for production
  wiring; deploy adapters (Lambda, Bedrock, secrets).
- **arc-cli** — `arc agent new/list/validate/promote/suspend`, audit dashboard,
  policy/effects browsers. (Legacy `foundry` script still works as an alias.)
- **arc-eval** — scenario-based evaluation framework.
- **arc-orchestrators** — common protocol with adapters for LangGraph, AgentCore,
  and Strands so agent code is orchestrator-agnostic; LangChain bridge.
- **arc-connectors** — Outlook, Pega (case + knowledge), ServiceNow, Bedrock
  (KB, LLM, Guardrails, Agent client), plus a mock for tests.
- **arc-platform** — reserved for Phase 3 web portals (empty placeholder).
- **arc/agents/** — 7 reference agents (retirement-trajectory, fiduciary-watchdog,
  life-event-anticipation, plan-design-optimizer, email-triage, care-coordinator,
  contract-review).

See [arc/README.md](arc/README.md) for the package layout and full API.

### `agent-foundry/` — back-compat shim package

The original implementation, slimmed to a re-export layer. Every file under
`agent-foundry/src/foundry/` is a thin shim that imports from `arc.*`. Existing
callers of `from foundry.X import Y` keep working; new code should use `arc.*`
directly. The package's `[aws]`, `[langchain]`, `[langgraph]`, etc. extras
remain as install-time aliases so legacy `pip install 'agent-foundry[aws]'`
instructions still install the right transitive deps.

### `agent-registry/`
The governance catalog. Teams submit a PR here when an agent is ready for
compliance review. Manifests only — no business logic. Each PR requires
compliance-team sign-off. Catalog generation runs in CI via
`arc.core.registry.build_catalog`.

### `agent-team-template/`
Boilerplate for a new team's agent repo. Copy it, install `arc-core` +
`arc-harness`, and build. Contains a working scaffold with manifest, policy,
agent stub, and tests.

### `tollgate/`
The canonical policy engine: ControlTower, YAML policy evaluator, circuit
breaker, approval primitives. Imported directly by both `arc.*` packages and
the foundry shim layer (no vendored copy anymore).

### `policies/`
Shared policy YAMLs that span teams (e.g., `financial_services/erisa.yaml`).
Per-team policies live in each team's repo.

### `deploy/`
Reusable deployment artifacts: container `Dockerfile`, ECS task definition,
CDK stacks for Lambda + Bedrock Agent.

### `docs/`
Planning and stakeholder artifacts — platform vision deck, project plan,
engineering overview, the typed-effect-scopes research paper, competitive
analysis, the interactive HTML pipeline deck. Plus the
[migration plan](docs/migration-plan.md) (module-by-module history of
foundry → arc) and [foundry-legacy/](docs/foundry-legacy/) (the original
`agent-foundry/docs/` content, preserved for concept reference).

---

## Quick start

```bash
# Install the platform (editable, monorepo)
pip install -e tollgate/
pip install -e arc/packages/arc-core/
pip install -e arc/packages/arc-harness/
pip install -e arc/packages/arc-runtime/
pip install -e arc/packages/arc-cli/
pip install -e arc/packages/arc-eval/
pip install -e arc/packages/arc-orchestrators/
pip install -e arc/packages/arc-connectors/

# (Optional) Install the back-compat shim — adds `from foundry.X import Y` support
pip install -e agent-foundry/

# Browse the effect taxonomy
arc effects list

# Scaffold a new agent
arc agent new my-agent

# Run a reference implementation
python arc/agents/retirement-trajectory/agent.py

# Validate a manifest
arc agent validate my-agent/manifest.yaml --strict
```

The legacy `foundry` script is still wired up as an alias for `arc`, so older
docs and shell history keep working.

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

- Platform architecture: [docs/foundry-legacy/platform-architecture.md](docs/foundry-legacy/platform-architecture.md) *(concepts apply identically to arc; import paths reference foundry.*)*
- Engineering overview: [docs/vision/engineering-overview.docx](docs/vision/engineering-overview.docx)
- Team onboarding: [docs/foundry-legacy/team-onboarding.md](docs/foundry-legacy/team-onboarding.md)
- Effect reference: [docs/foundry-legacy/effects-reference.md](docs/foundry-legacy/effects-reference.md)
- Migration history: [docs/migration-plan.md](docs/migration-plan.md)

---

Apache-2.0 · [ravi-labs](https://github.com/ravi-labs)
