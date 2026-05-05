# AgentCore portability demo

> **The thesis:** arc is a thin governance layer over vendor commodity.
> Take any arc agent, swap the orchestrator from local LangGraph to AWS
> Bedrock AgentCore, and **the audit log + telemetry shape don't
> change.** Effects are still gated by Tollgate, decisions still log,
> SLOs still evaluate, redaction still fires. The orchestrator is a
> substrate, not a governance layer.
>
> This demo proves that claim end-to-end with the email-triage agent.

---

## What this demo shows

```
            ┌──────────────────────────────────────────┐
            │  email-triage agent                       │
            │  (manifest, policy, graph — unchanged)    │
            └────────┬─────────────────────────┬───────┘
                     │                         │
        --mode local │                         │ --mode agentcore
                     │                         │
           ┌─────────▼────────┐       ┌────────▼──────────────┐
           │ LangGraph        │       │ AgentCoreOrchestrator │
           │ (in-process)     │       │ (AWS Bedrock          │
           │                  │       │  AgentCore runtime)   │
           └─────────┬────────┘       └────────┬──────────────┘
                     │                         │
           ┌─────────▼─────────────────────────▼──────────┐
           │  arc.core: BaseAgent.run_effect →            │
           │            ControlTower → policy + audit +   │
           │            telemetry                          │
           └──────────────────────────────────────────────┘
                     │                         │
           ┌─────────▼────────┐       ┌────────▼──────────┐
           │ ./out/local/     │       │ ./out/agentcore/  │
           │ • audit.jsonl    │  ===  │ • audit.jsonl     │
           │ • outcomes.jsonl │       │ • outcomes.jsonl  │
           │ • telemetry.ndjson│      │ • telemetry.ndjson│
           └──────────────────┘       └───────────────────┘
                          │                 │
                          └────────┬────────┘
                                   ▼
                         compare.py — same shape,
                          different substrate
```

---

## Run it

### Local mode (no AWS required)

```bash
python demos/agentcore-portability/run_demo.py --mode local
```

This runs end-to-end against the email-triage fixtures in
`arc/agents/email-triage/fixtures/emails.yaml`. Output lands in
`./out/local/`:

```
out/local/
├── summary.json           # JSON dict: runtime, audit_rows, decisions, ...
├── audit.jsonl            # Tollgate audit rows (one per effect)
├── outcomes.jsonl         # OutcomeTracker events (one per business outcome)
└── telemetry.ndjson       # CloudWatch EMF metrics (one per emit call)
```

### AgentCore mode (needs AWS)

Prerequisites:

1. AWS credentials reachable (env vars, profile, or IRSA)
2. A Bedrock Agent deployed via
   [`deploy/cdk/bedrock_agent_stack.py`](../../deploy/cdk/bedrock_agent_stack.py)
3. The agent ID exposed as an env var:

   ```bash
   export AWS_REGION=us-east-1
   export AGENTCORE_AGENT_ID=<from CDK output>
   export AGENTCORE_MEMORY_ID=<optional — for cross-session state>
   ```

Then:

```bash
python demos/agentcore-portability/run_demo.py --mode agentcore
```

Output lands in `./out/agentcore/` with the same file shape.

### Compare

```bash
python demos/agentcore-portability/compare.py
```

You'll see a table like:

```
Field                          Local                AgentCore
----------------------------------------------------------------------
Runtime                        local                agentcore
Audit rows written             14                   14                  ✓
Decision distribution          {ALLOW:9, ASK:5}     {ALLOW:9, ASK:5}    ✓
Telemetry metrics emitted      {arc.effect.outcome:14, ...}             ✓
```

Three checkmarks = portability proven.

---

## What's identical (by design)

The whole point of arc is that these don't change when the orchestrator changes:

| What | Why |
|---|---|
| **Audit row count** | Same agent + same fixtures = same effect graph |
| **Decision distribution** | Same `policy.yaml` evaluated by the same `YamlPolicyEvaluator` |
| **Telemetry metric set** | Same `arc.core.telemetry` emit paths fire either way |
| **Manifest scope checks** | `BaseAgent.run_effect` validates against the same manifest |
| **Effect taxonomy** | `ITSMEffect.*` is the same enum either way |
| **Redaction patterns** | `Redactor` is constructed in arc code, not the orchestrator |

---

## What differs (expected)

| What | Why |
|---|---|
| `run_id` | UUID per orchestrator invocation — different by definition |
| `metadata.framework` | `langgraph` vs `agentcore+langgraph` |
| Wall-clock latency | AgentCore adds network hop + cold-start time |
| Memory backend | Local uses `MemorySaver()`; AgentCore uses Sessions API |

These are **substrate-level** differences. They don't affect the
governance contract or what shows up in the audit log.

---

## What this demo does NOT prove

Honest about the gaps so the pitch holds up to scrutiny:

1. **Streaming behaviour.** `AgentCoreOrchestrator.stream()` currently
   returns `"streaming_not_yet_implemented"`. For chat-style agents this
   matters; for batch agents like email-triage it doesn't.
2. **AgentCore Memory persistence.** The current orchestrator uses
   `MemorySaver()` (in-process) as the LangGraph checkpointer. State
   doesn't survive Lambda cold-starts. Real production needs a
   DynamoDB-backed checkpointer or AgentCore's native Sessions
   integration. ~1 day of work; not in this demo.
3. **ASK approval flow under AgentCore.** When `OrchestratorSuspended`
   fires inside an AgentCore action group, the cleanest resumption
   path is AgentCore's session pause/resume primitive. This demo
   doesn't exercise that — it uses the local SQS+Dynamo approver path
   for both modes.
4. **Multi-tenancy.** Single-tenant. Multi-tenant tag scoping is a
   separate roadmap item.

These are honest known gaps, not blockers — the demo still proves the
*architectural* claim: the boundary is correct.

---

## Where to read next

- [`docs/concepts/governance.md`](../../docs/concepts/governance.md) — the run_effect
  → ControlTower → policy + audit pipeline
- [`docs/concepts/telemetry.md`](../../docs/concepts/telemetry.md) — the metric vocabulary
  this demo emits
- [`deploy/cdk/bedrock_agent_stack.py`](../../deploy/cdk/bedrock_agent_stack.py) — the CDK
  stack that creates the AgentCore agent + alias
- [`arc/packages/arc-orchestrators/src/arc/orchestrators/agentcore.py`](../../arc/packages/arc-orchestrators/src/arc/orchestrators/agentcore.py)
  — the orchestrator itself, with TODOs marking the production gaps
