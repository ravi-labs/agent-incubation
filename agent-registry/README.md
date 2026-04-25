# agent-registry

The central manifest registry for all AI agents built on the [arc](https://github.com/ravi-labs/agent-incubation) platform.

This repo contains **only manifests** — no agent code lives here. Each team's code stays in their own repo. The registry is the governance checkpoint: an agent must be registered here before it can be deployed to production.

---

## What This Repo Is

| What | Where |
|------|-------|
| Agent manifests (governance artifacts) | `registry/<agent-id>/manifest.yaml` |
| Auto-generated catalog | `registry.yaml` |
| PR template for new registrations | `.github/PULL_REQUEST_TEMPLATE.md` |
| CODEOWNERS (who reviews what) | `CODEOWNERS` |

## What This Repo Is NOT

- Agent implementation code (stays in each team's repo)
- Policy files (shared policies live in `policies/` at the repo root)
- Runtime infrastructure (deployment pipelines handle that)

---

## How to Register an Agent

### Prerequisites

1. Your agent must be at lifecycle stage `GOVERN` or above
2. Run `arc agent validate --strict` and confirm it passes
3. Your team must have a GitHub repo for the agent code

### Steps

```bash
# 1. Clone this repo
git clone https://github.com/ravi-labs/agent-registry
cd agent-registry

# 2. Create a branch for your agent
git checkout -b register/your-agent-id

# 3. Create the registry directory
mkdir -p registry/your-agent-id

# 4. Copy your manifest
cp path/to/your/manifest.yaml registry/your-agent-id/manifest.yaml

# 5. Commit and push
git add registry/your-agent-id/
git commit -m "Register agent: your-agent-id"
git push -u origin register/your-agent-id

# 6. Open a PR
# The PR template will guide you through the compliance review checklist
```

Alternatively, use the CLI shortcut:

```bash
arc registry submit --registry-dir ../agent-registry
```

---

## Registry Structure

```
registry/
├── retirement-trajectory/
│   └── manifest.yaml        ← Retirement Trajectory Intervention Agent
├── fiduciary-watchdog/
│   └── manifest.yaml        ← Fiduciary Watchdog Agent
├── life-event-anticipation/
│   └── manifest.yaml        ← Life Event Anticipation Agent
└── plan-design-optimizer/
    └── manifest.yaml        ← Plan Design Optimizer Agent

registry.yaml                ← Auto-generated catalog (do not edit manually)
CODEOWNERS                   ← Who reviews which agents
```

---

## Browsing the Registry

```bash
# Using the arc CLI
arc registry list --registry-dir .

# View the catalog directly
cat registry.yaml
```

---

## Governance Model

Every PR to this repo represents a **Stage 5 (Govern)** checkpoint in the incubation pipeline:

1. **Platform CI** validates manifest syntax, effect declarations, and schema
2. **CODEOWNERS** routes the PR to the appropriate domain reviewer
3. **Compliance officer** reviews effects, data access, and policy overrides
4. **Merge** = agent is officially registered and licensed for production deployment

A registered agent at `lifecycle_stage: SCALE` with `status: active` is the production "license" checked by deployment pipelines.

---

## Kill Switch

If an agent needs to be immediately halted:

```bash
cd path/to/team-repo
arc agent suspend --reason "Unexpected output volume"
git add manifest.yaml && git commit -m "SUSPEND: your-agent-id — <reason>"

# Open an emergency PR to this registry
cp manifest.yaml ../agent-registry/registry/your-agent-id/manifest.yaml
# PR will be fast-tracked by platform team
```

---

## CODEOWNERS

See `CODEOWNERS` for who reviews which agents. Platform team reviews all changes to `registry.yaml` and CI configuration.
