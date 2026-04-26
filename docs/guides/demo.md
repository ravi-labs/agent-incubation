# End-to-end lifecycle demo

A 20-minute runnable demo that walks an agent from `arc agent new` all
the way to a live SCALE deployment, then triggers the auto-demotion
watcher to send it back. Designed to show every governance touchpoint
without skipping over them.

> **Audience:** mixed — business sponsors care about stages 1-5 (the
> manifest, the approval queue, the dashboard); engineers care about
> stages 6-8 (the LLM wiring, watcher, kill switch). Plan ~2 minutes per
> stage; cut whichever audience is missing.

---

## Audience cheat sheet

| Stage | What to highlight | Audience |
|---|---|---|
| 1. Setup | "fresh checkout, no cloud creds needed" | both |
| 2. DISCOVER → SHAPE | "the manifest IS the agreed scope" | business |
| 3. BUILD | "every action runs through ControlTower" | engineers |
| 4. VALIDATE | "ROI evidence + outcome log" | business |
| 5. GOVERN | "compliance officer approves in the dashboard" | business |
| 6. SCALE | "manifest stage flips, agent goes live" | both |
| 7. Auto-demotion | "watcher proposes rollback when SLOs breach" | engineers |
| 8. Kill switch | "one env var halts everything" | both |

---

## Pre-demo checklist

Run this 5 minutes before the call. Everything below assumes the repo
root as the working directory.

```bash
# 1. Fresh venv (skip if you've already got one with arc-core installed)
python3 -m venv .venv
source .venv/bin/activate

# 2. Install all editable workspace packages
pip install -e tollgate \
            -e arc/packages/arc-core \
            -e arc/packages/arc-connectors \
            -e arc/packages/arc-cli \
            -e arc/packages/arc-harness \
            -e arc/packages/arc-runtime \
            -e arc/packages/arc-platform \
            -e arc/packages/arc-orchestrators

# 3. Make a sandbox dir for the demo (everything writes here)
export DEMO=/tmp/arc-demo && rm -rf $DEMO && mkdir -p $DEMO/registry
cd $DEMO

# 4. Sanity check
arc --help | head -3
arc agent --help | tail -10
```

If `arc` resolves and `arc agent watch` shows up in help, you're ready.

---

## Stage 1 — Scaffold a new agent (DISCOVER → SHAPE)

**Talk track:** *"Every agent on this platform starts the same way — a
manifest. The manifest is the artifact that moves through the
incubation pipeline. There is no agent without a manifest, and there
is no run-time effect without an entry in `allowed_effects`."*

```bash
cd $DEMO/registry
arc agent new claims-triage
cd claims-triage
cat manifest.yaml | head -25
```

Show the audience:
- `lifecycle_stage: DISCOVER` — the entry stage.
- `allowed_effects: []` — empty by design. Engineers add only what they need.
- `success_metrics:` — placeholder TODOs the team must fill in.

**Time:** 2 min.

---

## Stage 2 — Validate + promote DISCOVER → SHAPE → BUILD

```bash
arc agent validate manifest.yaml      # passes — required fields present

# Promote two stages forward to BUILD
arc agent promote manifest.yaml --to SHAPE
arc agent promote manifest.yaml --to BUILD
arc agent list --dir $DEMO/registry   # claims-triage now at BUILD
```

**Talk track:** *"Promotion is auditable — every transition writes one
row to a JSONL audit log. The CLI is the dev path; in production the
same calls happen via `PromotionService` from a CI pipeline or a PR
merge."*

**Time:** 1 min.

---

## Stage 3 — Wire effects + run in the harness

Edit `manifest.yaml` to declare a couple of effects and an SLO block
(the watcher will use this in stage 7). Replace the `allowed_effects`
and add an `slo:` block:

```yaml
allowed_effects:
  - participant.data.read
  - risk.score.compute
  - intervention.draft

slo:
  window:        1h
  min_volume:    10
  rules:
    - metric:    error_rate
      op:        "<"
      threshold: 0.05
  demotion_mode: proposed
```

Then validate:

```bash
arc agent validate manifest.yaml
arc effects show risk.score.compute   # show what the engineer just declared
```

**Talk track:** *"The SLO block is opt-in — agents without one are
never auto-demoted. We're declaring 'error rate must stay below 5% over
a rolling 1-hour window, but only evaluate when we have at least 10
events.'"*

**Time:** 3 min.

> **Note for engineers:** in a real demo you'd also `arc agent` run the
> harness against a fixture dataset to show audit JSONL appearing live.
> The retirement-trajectory example agent under `arc/agents/` is the
> canonical demo target — it ships with sample participant data and a
> working algorithmic path so no LLM is required.

---

## Stage 4 — VALIDATE + GOVERN promotions

```bash
arc agent promote manifest.yaml --to VALIDATE
arc agent promote manifest.yaml --to GOVERN
```

**Talk track:** *"VALIDATE and GOVERN are where the human gates live —
business owner signs off on ROI evidence at VALIDATE, compliance
officer signs off at GOVERN. The CLI promote is the lightweight path;
the full path runs through `PromotionService.promote(...)` with
`require_human={LifecycleStage.SCALE}` set, which auto-defers and
enqueues a `PendingApproval` for the dashboard."*

**Time:** 1 min.

---

## Stage 5 — Approval queue in the dashboard

Open two terminals.

**Terminal A — backend:**

```bash
cd $REPO_ROOT      # the agent-incubation checkout
arc platform serve --port 8000
```

**Terminal B — frontend:**

```bash
cd arc/packages/arc-platform/frontend
npm install        # first time only
npm run dev:ops    # → http://localhost:5173
```

In the browser:
1. **Overview** — agent count, allow/ask/deny totals.
2. **Agents** — `claims-triage` shows up at GOVERN.
3. **Approvals** — empty for now. (We'll fill this in stage 7.)

**Talk track:** *"Two dashboards on a shared backend — `ops` for
business reviewers, `dev` for engineers. Approve / reject from the
queue here flips the manifest stage on disk and writes an audit row in
one round trip."*

**Time:** 2 min.

---

## Stage 6 — SCALE promotion + LLM provider wiring

```bash
arc agent promote manifest.yaml --to SCALE
arc agent list --dir $DEMO/registry  # SCALE, environment=production
```

**Talk track:** *"SCALE promotion sets `environment: production`. The
agent is now live. Notice we never picked an LLM provider — that's
because the platform default is set via env vars on the runtime, and
each agent can override in its manifest."*

Show the LLM precedence in one shell:

```bash
# Platform default (would be set on the ECS task in prod)
export ARC_LLM_PROVIDER=bedrock
export ARC_LLM_MODEL=anthropic.claude-3-5-sonnet-20241022-v2:0

# Per-agent override — uncomment the llm: block in manifest.yaml to pin
# this agent to a specific provider/model regardless of platform default.
```

Show [llm-clients.md](../concepts/llm-clients.md) precedence stack
section if asked.

**Time:** 2 min.

---

## Stage 7 — Trigger auto-demotion

This is the headline moment. Seed errors into an outcomes JSONL, then
run `arc agent watch` three times to walk through hysteresis.

```bash
# Seed 50 error events for claims-triage
python3 - <<'PY'
import json, datetime
now = datetime.datetime.now(datetime.timezone.utc).isoformat()
with open("/tmp/arc-demo/outcomes.jsonl", "w") as f:
    for _ in range(50):
        f.write(json.dumps({
            "agent_id":   "claims-triage",
            "event_type": "run",
            "data":       {"status": "error"},
            "timestamp":  now,
        }) + "\n")
print("seeded 50 error events")
PY

# Watcher run #1 — first breach observed
arc agent watch \
    --registry      $DEMO/registry \
    --outcomes      $DEMO/outcomes.jsonl \
    --audit         $DEMO/audit.jsonl \
    --breach-state  $DEMO/breach.jsonl \
    --approvals     $DEMO/approvals.jsonl \
    --consecutive   3
# → claims-triage    breach-pending    1/3 consecutive breaches

# Run #2 — still under threshold
arc agent watch --registry $DEMO/registry --outcomes $DEMO/outcomes.jsonl \
    --audit $DEMO/audit.jsonl --breach-state $DEMO/breach.jsonl \
    --approvals $DEMO/approvals.jsonl --consecutive 3
# → claims-triage    breach-pending    2/3 consecutive breaches

# Run #3 — fires!
arc agent watch --registry $DEMO/registry --outcomes $DEMO/outcomes.jsonl \
    --audit $DEMO/audit.jsonl --breach-state $DEMO/breach.jsonl \
    --approvals $DEMO/approvals.jsonl --consecutive 3
# → claims-triage    proposed         SCALE → GOVERN
```

**Now refresh the ops dashboard** — `Approvals` page shows a new
proposal with `kind=demotion`.

**Talk track:** *"The watcher is stateless — every run reads its state
from disk, so it's safe to put on a 5-minute cron. The defaults are
tuned for safety: 3 consecutive evaluations required before firing, 24h
cooldown after any state change, and the default mode is `proposed`
(human in the loop). We'd only flip an agent to `auto` mode after we
trust the SLO signal."*

**Time:** 5 min — the centerpiece.

---

## Stage 8 — Kill switch + resume

End the demo by showing the panic button.

```bash
# Kill switch on
ARC_AUTO_DEMOTE_DISABLED=1 arc agent watch \
    --registry $DEMO/registry --outcomes $DEMO/outcomes.jsonl \
    --audit $DEMO/audit.jsonl --breach-state $DEMO/breach.jsonl \
    --approvals $DEMO/approvals.jsonl
# → claims-triage    skipped:disabled

# Suspend the agent entirely (different mechanism — manifest-level)
arc agent suspend manifest.yaml --reason "Demo: unexpected error volume"
arc agent list --dir $DEMO/registry  # status = suspended

# Resume
arc agent resume manifest.yaml
```

**Talk track:** *"Two layers of off switch. `ARC_AUTO_DEMOTE_DISABLED`
halts the watcher only — promotions and runtime actions still happen.
`arc agent suspend` halts the agent itself — no effects execute until
someone runs `resume`. Both write audit rows so the trail is complete."*

**Time:** 2 min.

---

## Total runtime

| Block | Min |
|---|---|
| Setup (pre-demo, off-camera) | 5 |
| Stages 1-2 (scaffold + early promotion) | 3 |
| Stages 3-4 (effects + SLO + VALIDATE/GOVERN) | 4 |
| Stage 5 (dashboard tour) | 2 |
| Stage 6 (SCALE + LLM wiring) | 2 |
| Stage 7 (auto-demotion) | 5 |
| Stage 8 (kill switch + resume) | 2 |
| Buffer for questions | 4 |
| **Total** | **~22** |

If you have 15 minutes, drop stage 8 (kill switch can be a footnote).
If you have 30 minutes, add a real harness run between stages 3 and 4 —
seed sample participants and show the audit JSONL filling up live.

---

## Things that often come up

**"What if the LLM goes down?"** — `LiteLLMClient` ships with a
fallback chain (`fallback_models=[...]`); on rate-limit / 5xx, it tries
the next model. `LLMConfig` exposes this from env or manifest.

**"Can we run this without AWS?"** — Yes for everything except the
production deploy story. The harness uses `MockGatewayConnector`, the
audit log is a JSONL file, the approval store is JSONL on disk, and
LiteLLM can route to a local Ollama. AWS is an option, not a prereq.

**"How do we add Salesforce / Dynamics / Pega CDH?"** — Implement
`GatewayConnector` (one method: `fetch(DataRequest) → DataResponse`)
and register it in `MultiGateway`. The agent code doesn't change. See
the existing connectors under `arc.connectors`.

**"What stops an agent calling an effect it didn't declare?"** —
`BaseAgent.run_effect()` checks `manifest.allows_effect(effect)` first
and raises `PermissionError` if not. The effect taxonomy is enum-typed;
no string typos. Then ControlTower runs the policy. Two layers.

**"Where does cost / token telemetry land?"** — Each LLM call's audit
row carries `metadata` with provider, model, prompt size. Aggregated
cost rollups are on the [In-flight roadmap](../roadmap.md) — the data
is captured today, the rollup view isn't built.

---

## Where to read next

- [Architecture](../architecture.md) — the layered picture this demo walks through.
- [Lifecycle](../concepts/lifecycle.md) — the deepest concept doc;
  covers promotion, demotion, audit trail, and SLO schema in full.
- [Build an agent](build-an-agent.md) — the long-form version of stages
  1-3, for engineers writing their first agent.
- [Roadmap](../roadmap.md) — what's shipped, in flight, on the backlog.
