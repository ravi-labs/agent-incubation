# your-team-agents

AI agents built on [arc](https://github.com/ravi-labs/agent-incubation) — the enterprise agent incubation platform.

## Getting Started

```bash
# Install arc (editable, from a sibling clone of the agent-incubation monorepo)
pip install -e ../agent-incubation/tollgate
pip install -e ../agent-incubation/arc/packages/arc-core
pip install -e ../agent-incubation/arc/packages/arc-harness
pip install -e ../agent-incubation/arc/packages/arc-cli

# Then install this repo in editable mode (with dev extras)
pip install -e ".[dev]"
```

## Creating a New Agent

```bash
# Scaffold a new agent in the agents/ directory
arc agent new your-agent-name --dir agents

# Validate your manifest
arc agent validate agents/your-agent-name/manifest.yaml

# List all agents in this repo
arc agent list
```

## Agent Structure

```
agents/
└── your-agent-name/
    ├── manifest.yaml    ← Declare effects, data access, success metrics
    ├── policy.yaml      ← Per-agent policy overrides (optional)
    ├── agent.py         ← Agent implementation (extends BaseAgent)
    └── tests/
        └── test_your_agent.py
```

## Incubation Pipeline

| Stage | What Happens | Gate |
|-------|-------------|------|
| DISCOVER | Problem definition, feasibility | Team review |
| SHAPE | Manifest drafted, effects declared | Platform review |
| BUILD | Implementation in sandbox | CI passing |
| VALIDATE | Tested against production-representative data | QA sign-off |
| GOVERN | Registry PR opened, compliance review | Compliance officer approval |
| SCALE | Live in production | Deployment approval |

## Registering an Agent

When your agent reaches `lifecycle_stage: GOVERN`:

```bash
# Update stage in manifest
arc agent promote agents/your-agent-name/manifest.yaml --to GOVERN

# Submit to the central registry
arc registry submit agents/your-agent-name/manifest.yaml \
  --registry-dir ../agent-registry
```

This copies your manifest to `agent-registry/registry/your-agent-name/manifest.yaml`
and opens a PR checklist for compliance review.

## Browsing the Taxonomy

```bash
# See all available effects
arc effects list

# Get details on a specific effect
arc effects show participant.communication.send
```

## Running Tests

```bash
pytest agents/ -v
```
