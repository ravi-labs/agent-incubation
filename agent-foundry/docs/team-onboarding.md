# Team Onboarding Guide — Building Agents on agent-foundry

Welcome to the agent-foundry platform. This guide gets your team from zero to a working agent in your own repo, connected to the platform governance workflow.

---

## Prerequisites

- Python 3.10 or higher
- Git and GitHub access
- Access to `ravi-labs/agent-registry` (request from platform team)

---

## Step 1: Create Your Team Repo

Use the [agent-team-template](https://github.com/ravi-labs/agent-team-template) to bootstrap your repo.

```bash
# Option A: Use GitHub's "Use this template" button on the template repo

# Option B: Manual setup
mkdir your-team-agents && cd your-team-agents
git init
pip install agent-foundry
```

Update `pyproject.toml`:
- Set `name = "your-team-agents"`
- Verify `agent-foundry>=0.1.0` is in `dependencies`

---

## Step 2: Install agent-foundry

```bash
pip install -e ".[dev]"
```

Verify the CLI works:

```bash
foundry --version
foundry effects list
```

---

## Step 3: Scaffold Your First Agent

```bash
foundry agent new your-agent-name --dir agents
```

This creates:
```
agents/your-agent-name/
├── manifest.yaml    ← fill in effects and success metrics
├── policy.yaml      ← add overrides if needed (usually empty to start)
├── agent.py         ← implement your logic here
└── tests/
```

---

## Step 4: Define Your Manifest

Open `agents/your-agent-name/manifest.yaml` and fill in:

1. **`description`** — what does this agent do in 2–3 sentences?
2. **`allowed_effects`** — which effects does it need? Run `foundry effects list` to browse. Start minimal.
3. **`data_access`** — which data sources does it read from?
4. **`success_metrics`** — what does "good" look like at 30 days?
5. **`team_repo`** — your repo URL

Validate:

```bash
foundry agent validate agents/your-agent-name/manifest.yaml
```

---

## Step 5: Understand the Effect Taxonomy

Every action your agent takes must be declared as a `FinancialEffect`. Browse the taxonomy:

```bash
# List all effects
foundry effects list

# List only output effects (most scrutinized)
foundry effects list --tier 4

# Get details on a specific effect
foundry effects show participant.communication.send
```

**Key rules:**
- Tier 1–3 effects are ALLOW by default (reads, computations, drafts)
- Tier 4 effects (Output) are ASK by default — they reach real people
- Tier 6 effects (System Control) are ASK or DENY
- Hard denies (`account.transaction.execute`, `participant.data.write`, `plan.data.write`) cannot be declared by any agent

If you need an effect that doesn't exist, open an [Effect RFC](https://github.com/ravi-labs/agent-foundry/issues/new?template=effect-rfc.md) on agent-foundry.

---

## Step 6: Implement Your Agent

Open `agents/your-agent-name/agent.py`. Your agent extends `BaseAgent` and implements `execute()`.

Key patterns:

```python
class MyAgent(BaseAgent):
    async def execute(self, **kwargs) -> dict:

        # ── Fetch data ─────────────────────────────────────────────
        response = await self.gateway.fetch(DataRequest(
            source="participant.data",
            params={"participant_id": "p-001"},
        ))
        data = response.data

        # ── Run effect (goes through policy engine) ─────────────────
        result = await self.run_effect(
            effect=FinancialEffect.RISK_SCORE_COMPUTE,
            tool="my_scorer",
            action="compute",
            params={"participant_id": "p-001"},
            intent_action="score_participant",           # Short descriptor
            intent_reason="Assess retirement readiness", # Why you're doing this
            exec_fn=lambda: my_scoring_function(data),  # The actual work
        )

        # ── Log outcome ─────────────────────────────────────────────
        await self.log_outcome("score_computed", {
            "participant_id": "p-001",
            "score": result["score"],
        })

        return result
```

**Rules:**
- Every tool call must go through `self.run_effect()` — no bypassing
- Effects not in `allowed_effects` will raise `PermissionError`
- `exec_fn` is the actual callable — the policy engine wraps it

---

## Step 7: Test in Sandbox

Wire up your agent with `MockGatewayConnector` to test locally:

```python
gateway = MockGatewayConnector({
    "participant.data": {
        "p-001": {"id": "p-001", "name": "Test", "balance": 100_000},
    },
})
```

Run your agent:
```bash
python agents/your-agent-name/agent.py
```

Write tests:
```bash
pytest agents/your-agent-name/tests/ -v
```

---

## Step 8: The Incubation Pipeline

Your agent progresses through 6 stages. Promote it as you complete each:

```bash
# Promote from DISCOVER to SHAPE
foundry agent promote agents/your-agent-name/manifest.yaml

# Promote to a specific stage
foundry agent promote agents/your-agent-name/manifest.yaml --to VALIDATE

# Preview what would change
foundry agent promote agents/your-agent-name/manifest.yaml --dry-run
```

| Stage | What You Do |
|-------|------------|
| DISCOVER | Define the problem. Write your first manifest. |
| SHAPE | Finalize effect declarations. Get platform team feedback. |
| BUILD | Implement in sandbox. Tests passing. |
| VALIDATE | Test against production-representative synthetic data. |
| GOVERN | **Open registry PR.** Compliance review. |
| SCALE | Live in production. |

---

## Step 9: Register Your Agent (Stage GOVERN)

When your agent is ready for compliance review:

```bash
# Validate one last time (strict mode matches CI)
foundry agent validate agents/your-agent-name/manifest.yaml --strict

# Promote to GOVERN
foundry agent promote agents/your-agent-name/manifest.yaml --to GOVERN

# Submit to the registry
foundry registry submit agents/your-agent-name/manifest.yaml \
  --registry-dir ../agent-registry

# Or do it manually:
cp agents/your-agent-name/manifest.yaml \
   ../agent-registry/registry/your-agent-name/manifest.yaml
# Open a PR to ravi-labs/agent-registry
```

The compliance officer will review your manifest in the PR. Expect questions about:
- Why each effect is needed
- Data access scope (minimum-necessary principle)
- Success metrics
- Any policy overrides

---

## Step 10: The Kill Switch

If your agent ever needs to be halted in production:

```bash
foundry agent suspend agents/your-agent-name/manifest.yaml \
  --reason "Unexpected output volume — investigating"

# Commit and push — then open emergency PR to agent-registry
git add agents/your-agent-name/manifest.yaml
git commit -m "SUSPEND: your-agent-name — unexpected output volume"
```

The framework will immediately block all `run_effect()` calls for suspended agents.

To resume:
```bash
foundry agent resume agents/your-agent-name/manifest.yaml
git add agents/your-agent-name/manifest.yaml
git commit -m "RESUME: your-agent-name — issue resolved"
```

---

## Getting Help

- **Browse the taxonomy:** `foundry effects list`
- **Platform architecture:** [docs/platform-architecture.md](platform-architecture.md)
- **Reference implementations:** [`examples/`](../examples/) in agent-foundry
- **Effect RFC (need a new effect):** [GitHub issue template](https://github.com/ravi-labs/agent-foundry/issues/new?template=effect-rfc.md)
- **Questions:** Open an issue on agent-foundry or reach out to the platform team
