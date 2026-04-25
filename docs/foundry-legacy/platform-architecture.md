# Agent Foundry — Platform Architecture

## Overview

Agent Foundry is a multi-team AI agent platform for financial services. It separates three concerns that must not be conflated:

1. **The framework** (`agent-foundry`) — shared infrastructure every team uses
2. **Team agent repos** — each team's own code, built on the framework
3. **The registry** (`agent-registry`) — lightweight governance catalog of registered agents

```
┌─────────────────────────────────────────────────────────────────────┐
│                         PLATFORM LAYER                              │
│                   (ravi-labs/agent-foundry)                         │
│                                                                     │
│  EffectTaxonomy  │  BaseAgent  │  Tollgate  │  Gateway  │  CLI     │
│  erisa.yaml      │  Manifest   │  (Policy)  │  Tracker  │         │
└─────────────────────────────────────────────────────────────────────┘
         │ pip install agent-foundry     │ pip install agent-foundry
         ▼                               ▼
┌────────────────────┐       ┌────────────────────────┐
│  retirement-agents │       │  compliance-agents     │
│  (team repo)       │       │  (team repo)           │
│                    │       │                        │
│  agents/           │       │  agents/               │
│    rt-trajectory/  │       │    fiduciary-watchdog/ │
│    life-event/     │       │                        │
└────────────────────┘       └────────────────────────┘
         │                               │
         │ manifest PR                   │ manifest PR
         ▼                               ▼
┌─────────────────────────────────────────────────────────────────────┐
│                    REGISTRY LAYER                                   │
│                  (ravi-labs/agent-registry)                         │
│                                                                     │
│  registry/retirement-trajectory/manifest.yaml                       │
│  registry/fiduciary-watchdog/manifest.yaml                          │
│  registry/life-event-anticipation/manifest.yaml                     │
│  registry.yaml  ← auto-generated catalog                           │
└─────────────────────────────────────────────────────────────────────┘
```

---

## The Framework (agent-foundry)

### What It Provides

**Effect Taxonomy** (`foundry.policy.effects`): 30 named effects across 6 tiers (Data Access → System Control). Every agent tool call declares an effect. Effects map to Tollgate's `resource_type` field for YAML policy matching.

**BaseAgent** (`foundry.scaffold.base`): Abstract base class that enforces:
- Kill switch: suspended agents are blocked at the class level
- Manifest enforcement: undeclared effects raise `PermissionError`
- Policy engine integration: every `run_effect()` call goes through Tollgate's `ControlTower`
- Audit trail: all decisions logged automatically

**AgentManifest** (`foundry.scaffold.manifest`): YAML artifact that declares an agent's scope. Contains: `agent_id`, `version`, `owner`, `lifecycle_stage`, `status`, `allowed_effects`, `data_access`, `success_metrics`, `team_repo`, `foundry_version`.

**Tollgate** (`foundry.tollgate`): Vendored policy engine. `ControlTower` evaluates every tool call against YAML rules. Hard denies cannot be overridden. HMAC identity, cryptographic audit log, approval workflows.

**Shared Policies** (`policies/financial_services/`):
- `defaults.yaml`: ALLOW/ASK/DENY rules for all 30 effects
- `erisa.yaml`: Regulatory overlay — ERISA §404, DOL AI guidance, FINRA rules. Immutable from agent policy.yaml.

**Gateway** (`foundry.gateway`): Data access abstraction. Agents declare what data sources they need; Gateway enforces access centrally.

**OutcomeTracker** (`foundry.observability`): JSONL event recorder for ROI measurement.

**CLI** (`foundry`): `foundry agent new/list/validate/promote/suspend/resume`, `foundry registry submit/list`, `foundry effects list/show`.

**Registry Catalog** (`foundry.registry`): `build_catalog()` generates `registry.yaml` from manifests; `RegistryCatalog` provides queries (by stage, owner, effect, status).

### What Teams Get When They `pip install agent-foundry`

- The full effect taxonomy
- BaseAgent, AgentManifest, and all scaffold primitives
- Shared policies (defaults.yaml + erisa.yaml) baked in
- The CLI
- MockGatewayConnector for sandbox testing

### What Teams Do NOT Get (by design)

- Each other's agent code
- Deployment infrastructure
- Production data connectors (those are team-specific)

---

## Team Agent Repos

Each team creates their own GitHub repo (e.g., `your-org/retirement-agents`). Use the [agent-team-template](https://github.com/ravi-labs/agent-team-template) to scaffold it.

### Structure

```
your-team-agents/
├── pyproject.toml            # agent-foundry as dependency
├── agents/
│   └── your-agent/
│       ├── manifest.yaml     # declares scope + effects
│       ├── policy.yaml       # optional overrides on top of defaults.yaml
│       ├── agent.py          # extends BaseAgent
│       └── tests/
└── .github/
    └── workflows/ci.yml      # validates manifests + runs tests
```

### Policy Layering

```
erisa.yaml          ← Regulatory floor (immutable — cannot be loosened)
    +
defaults.yaml       ← Platform defaults (can be tightened by agents)
    +
agent/policy.yaml   ← Per-agent overrides (tightening only)
```

An agent policy.yaml may change `ASK → DENY` (tighter) but never `ASK → ALLOW` (looser) for regulated effects.

---

## The Registry (agent-registry)

The registry is a separate, lightweight repo that contains only manifests. No code lives here.

### Purpose

1. **Governance checkpoint**: A registry PR is Stage 5 (Govern) in the incubation pipeline. Compliance officer review happens here.
2. **Discovery**: Teams can browse what agents exist across the company.
3. **Production license**: Deployment pipelines check registry status before running an agent in production. An agent at `lifecycle_stage: SCALE` with `status: active` is the "green light."
4. **Kill switch**: Setting `status: suspended` in the registry blocks the agent at the framework level.

### How It Works

```
Team opens registry PR
        │
        ▼
CI: foundry agent validate --strict
        │
        ▼
CODEOWNERS routes to compliance officer
        │
        ▼
Compliance reviews: effects, policy, data access
        │
        ▼
PR merged → registry.yaml regenerated by CI
        │
        ▼
Deployment pipeline reads registry.yaml → agent gets prod access
```

---

## The Incubation Pipeline

```
DISCOVER → SHAPE → BUILD → VALIDATE → GOVERN → SCALE
   │          │       │         │          │       │
Team defines  Manifest  Impl in  Test vs   Registry  Live in
the problem   drafted   sandbox  prod-like  PR +      production
              + effects          data       compliance
              declared           review
```

The pipeline maps directly to git/PR workflow:
- **DISCOVER–BUILD**: Work in team's feature branch in their own repo
- **VALIDATE**: Team tests in sandbox environment
- **GOVERN**: Open PR to agent-registry — compliance officer reviews manifest
- **SCALE**: PR merged → agent promoted to production environment

---

## The Kill Switch

If an agent needs to be halted immediately:

1. In the team's repo: `foundry agent suspend --reason "..."`
2. Commit the manifest change
3. Open an emergency PR to agent-registry (fast-tracked by platform team)
4. Once merged, framework blocks all `run_effect()` calls with `PermissionError`

The kill switch works at the **framework layer** — no deployment change required. Any running instance that picks up the updated manifest will be immediately blocked.

---

## Effect Governance (Taxonomy RFC Process)

When a team needs a new effect:

1. Open an issue on `agent-foundry` using the Effect RFC template (`.github/ISSUE_TEMPLATE/effect-rfc.md`)
2. Describe the business need, proposed effect value, tier, and default decision
3. Platform team + compliance officer review the RFC
4. If approved: effect added to `effects.py` + `defaults.yaml` in a new release
5. Teams upgrade their `foundry_version` to access the new effect

This keeps the taxonomy coherent and prevents semantic drift (teams reusing existing effects for unrelated purposes).

---

## Versioning and Dependency Management

Teams pin their `agent-foundry` version in `pyproject.toml` and in the manifest's `foundry_version` field. The registry catalog records which version each agent targets, giving compliance visibility into what policy rules a given agent was running against on any given date.

When `agent-foundry` releases a new version with updated policy rules (e.g., a new DOL guidance is published), the platform team:
1. Updates `erisa.yaml` and releases a new version
2. Publishes a migration guide
3. Teams are expected to upgrade within a defined window (e.g., 60 days)
4. The registry CI can enforce a `foundry_version` freshness check

---

## Security Boundaries

| Boundary | Enforcement | Where |
|----------|-------------|-------|
| Effects must be declared | `BaseAgent.run_effect()` raises `PermissionError` | Framework layer |
| Kill switch | `BaseAgent.run_effect()` checks `manifest.is_active` | Framework layer |
| Hard denies | Tollgate `ControlTower` — DENY decisions block execution | Policy engine |
| Production deployment | Registry status check in CI/CD pipeline | Deployment layer |
| Policy rule changes | `erisa.yaml` rules are `immutable: true` — cannot be overridden | Policy layer |
| Governance | Registry PR requires compliance officer approval | Governance layer |
