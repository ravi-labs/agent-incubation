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
├── agent-registry/       ← Central governance catalog: manifests only, no code
├── agent-team-template/  ← Starter template: copy this to bootstrap a new team repo
├── tollgate/             ← Canonical policy engine (ControlTower, evaluator, circuit breaker)
├── policies/             ← Shared policy YAMLs (financial-services, etc.)
├── deploy/               ← Container + CDK + ECS deployment artifacts
└── docs/                 ← Vision, research, marketing, and migration history
    ├── vision/           ← platform vision, project plan, engineering overview
    ├── research/         ← typed-effect-scopes paper
    ├── marketing/        ← competitive analysis, quickstart, interactive deck
    ├── foundry-legacy/   ← legacy architecture docs (paths reference foundry.*; concepts map 1:1 to arc.*)
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
  policy/effects browsers.
- **arc-eval** — scenario-based evaluation framework.
- **arc-orchestrators** — common protocol with adapters for LangGraph, AgentCore,
  and Strands so agent code is orchestrator-agnostic; LangChain bridge.
- **arc-connectors** — Outlook, Pega (case + knowledge), ServiceNow, Bedrock
  (KB, LLM, Guardrails, Agent client), plus a mock for tests.
- **arc-platform** — FastAPI backend + two React dashboards. **`frontend/ops`** for business users (approval queue, agent inventory, audit trail) and **`frontend/dev`** for engineers (audit details, agent dev workflow). Launch the API with `arc platform serve`; run frontends from `arc/packages/arc-platform/frontend/` via `npm run dev:ops` / `dev:dev`.
- **arc/agents/** — 7 reference agents (retirement-trajectory, fiduciary-watchdog,
  life-event-anticipation, plan-design-optimizer, email-triage, care-coordinator,
  contract-review).

See [arc/README.md](arc/README.md) for the package layout and full API.

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
breaker, approval primitives. Imported directly by every `arc.*` package.

### `policies/`
Shared policy YAMLs that span teams (e.g., `financial_services/erisa.yaml`).
Per-team policies live in each team's repo.

### `deploy/`
Reusable deployment artifacts: container `Dockerfile`, ECS task definition,
CDK stacks for Lambda + Bedrock Agent.

### `docs/`
Engineering documentation, organized high-level → low-level:
- [`docs/architecture.md`](docs/architecture.md) — top-level platform architecture
- [`docs/concepts/`](docs/concepts/) — the four big abstractions: effects, governance, lifecycle
- [`docs/guides/`](docs/guides/) — hands-on walkthroughs (start with [build-an-agent](docs/guides/build-an-agent.md))
- [`docs/vision/`](docs/vision/), [`docs/research/`](docs/research/), [`docs/marketing/`](docs/marketing/) — stakeholder artifacts (vision deck, typed-effect-scopes paper, competitive analysis)

---

## Quick start

One script sets up the whole environment — venv, every workspace package
(in dependency order), and optional extras for the profile you pick.

**macOS / Linux / WSL:**

```bash
./setup.sh                            # dev profile (default)
./setup.sh --mode aws                 # production-like (boto3 + langchain-aws)
./setup.sh --mode dev --with-frontend # also npm install the React dashboards
```

**Windows:**

```cmd
setup.bat
setup.bat --mode aws
setup.bat --mode dev --with-frontend
```

After it finishes:

```bash
source .venv/bin/activate          # or .venv\Scripts\activate.bat on Windows
arc --help
```

The two profiles:

| Profile | Adds | Use for |
|---|---|---|
| `dev` (default) | `[dev]` extras: pytest, ruff, mypy. Light orchestrator deps. | Local development, harness runs, demos, tests. |
| `aws` | `[aws]` on `arc-connectors` + `arc-runtime` (boto3, langchain-aws), all orchestrators (`langgraph`, `agentcore`, `strands`), all connectors (Outlook, Pega, ServiceNow, LiteLLM). | ECS task images, production-like local runs. |

The script is idempotent — re-run it any time. See [setup.sh](setup.sh) /
[setup.bat](setup.bat) for the full flag list (`--python`, `--venv`,
`--with-frontend`, `--help`).

### What you get after install

```bash
# Browse the effect taxonomy
arc effects list

# Scaffold a new agent
arc agent new my-agent

# Run a reference implementation
python arc/agents/retirement-trajectory/agent.py

# Validate a manifest
arc agent validate my-agent/manifest.yaml --strict

# Run the auto-demotion watcher (cron-friendly)
arc agent watch --registry arc/agents --outcomes outcomes.jsonl \
    --audit promotion_audit.jsonl --breach-state breach_state.jsonl \
    --approvals pending_approvals.jsonl
```

For the full end-to-end walk-through, see the [demo plan](docs/guides/demo.md).
For a complete worked example — building one specific agent
(claims-triage for an insurance company) from scaffold to dashboard
with both bash and PowerShell commands — see the
[claims-triage demo](docs/guides/claims-triage-demo.md).
To take the shipped `email-triage` agent end-to-end against your
org's *real* sandbox tenants (real Outlook, real Bedrock, real
ServiceNow / Pega — no mocks), see the
[email-triage integration guide](docs/guides/email-triage-integration.md).

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

- **Start here:** [docs/architecture.md](docs/architecture.md) — what arc is, the four big abstractions, package layout, end-to-end execution flow
- **The four big concepts:**
  - [Effects](docs/concepts/effects.md) — typed taxonomy, six tiers, default decisions
  - [Governance](docs/concepts/governance.md) — Tollgate ControlTower, policy YAML, audit trail
  - [Lifecycle](docs/concepts/lifecycle.md) — six-stage pipeline, promotion service, manifest write-back
  - [LLM clients](docs/concepts/llm-clients.md) — `LLMClient` Protocol with Bedrock + LiteLLM impls
- **Hands-on:** [Build an agent](docs/guides/build-an-agent.md)
- **Deploy:** [`deploy/bedrock-agent-core.md`](deploy/bedrock-agent-core.md)
- Engineering overview: [docs/vision/engineering-overview.docx](docs/vision/engineering-overview.docx)

---

Apache-2.0 · [ravi-labs](https://github.com/ravi-labs)
