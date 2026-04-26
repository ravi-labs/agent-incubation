# Lifecycle

While [Governance](governance.md) decides whether each *individual* tool
call runs, the lifecycle layer decides whether the *agent* runs at all,
and at what level of autonomy. Every agent moves through six stages,
each with explicit entry criteria, exit artifacts, and a designated
reviewer.

> **Code:** [`arc/packages/arc-core/src/arc/core/lifecycle/`](../../arc/packages/arc-core/src/arc/core/lifecycle/)
> **Public API:** `from arc.core import LifecycleStage, PromotionService, PromotionRequest, GateChecker, apply_decision`

---

## The six stages

```
DISCOVER → SHAPE → BUILD → VALIDATE → GOVERN → SCALE
```

| Stage | What it produces | Reviewer | Environment |
|---|---|---|---|
| **DISCOVER** | Go/No-Go scorecard, named data owner + business sponsor | business sponsor | none |
| **SHAPE** | AgentManifest, success metrics baseline, draft policy, human-in-loop map | product owner | none |
| **BUILD** | Working agent passing sandbox tests, finalized policy, edge case log | tech lead | sandbox |
| **VALIDATE** | ROI report vs. baseline, error rate analysis, outcome log | business owner | sandbox |
| **GOVERN** | Compliance sign-off, regulatory assessment, override protocols | compliance officer | sandbox |
| **SCALE** | Live in production, runbook, KPI dashboard, quarterly review | operations owner | production |

Every stage's full gate definition (entry criteria + exit artifacts +
reviewer + environment) lives in
[`stages.py`](../../arc/packages/arc-core/src/arc/core/lifecycle/stages.py).
That file is short and worth reading — it is the contract.

The progression maps onto autonomy: agents in BUILD and VALIDATE run
in *sandbox* against synthetic or shadow data. Only after GOVERN
sign-off can an agent reach SCALE and execute against production
record systems.

---

## The promotion service

Stages are static definitions; transitions between them are runtime
operations. `PromotionService` orchestrates one transition:

```python
from arc.core import (
    PromotionService, PromotionRequest, GateChecker, LifecycleStage,
    stage_order_check, evidence_field_check, reviewer_present_check,
)

# 1. Register the gates that must pass for each target stage
checker = GateChecker()
checker.register(LifecycleStage.VALIDATE, stage_order_check())
checker.register(LifecycleStage.VALIDATE, evidence_field_check("test_results"))
checker.register(LifecycleStage.VALIDATE, evidence_field_check("edge_case_log"))

checker.register(LifecycleStage.GOVERN, stage_order_check())
checker.register(LifecycleStage.GOVERN, reviewer_present_check())
checker.register(LifecycleStage.GOVERN, evidence_field_check("roi_report"))

# 2. Wire the service. SCALE always defers to a human even if gates pass.
service = PromotionService(checker, require_human={LifecycleStage.SCALE})

# 3. Submit a promotion request
decision = service.promote(PromotionRequest(
    agent_id     = "email-triage",
    current_stage = LifecycleStage.BUILD,
    target_stage  = LifecycleStage.VALIDATE,
    requester     = "alice@team",
    justification = "Sandbox tests green for 7 days",
    evidence      = {
        "test_results":   "tests/build-2026-04-22.json",
        "edge_case_log":  "tests/edge-cases.md",
    },
))
```

Three possible outcomes:

| Outcome | Meaning |
|---|---|
| `APPROVED` | All gates passed; the new stage is committed. |
| `REJECTED` | One or more gates failed. The decision lists which. |
| `DEFERRED` | All gates passed but `require_human` policy says wait for explicit human approval. |

`SCALE` is in `require_human` by default — automated gates cannot promote
an agent to production without an explicit human decision.

---

## Gate checks

A `GateCheck` is just a function `(PromotionRequest) -> GateCheckResult`.
The built-in primitives cover the common cases:

| Primitive | Use |
|---|---|
| `stage_order_check()` | The target must be the immediate next stage. |
| `evidence_field_check(name)` | A specific evidence field must be present and non-empty. |
| `artifact_exists_check(field)` | The path stored in `evidence[field]` must exist on disk. |
| `reviewer_present_check()` | The required reviewer role for the target stage must be named in evidence. |
| `predicate_check(name, fn)` | Wrap any boolean predicate as a gate (escape hatch). |

Project-specific rules — "no critical vulnerabilities open in this
agent's repo," "the agent has at least 7 consecutive days of clean
audit trail" — are added with `predicate_check`. Each is a small,
composable, testable function.

---

## Manifest write-back

`PromotionService.promote()` does **not** mutate the manifest. It returns
a `PromotionDecision` and records it to the audit log. The mutation step
is explicit and decoupled:

```python
from arc.core import (
    apply_decision, DirectoryManifestStore, LocalFileManifestStore,
)

store = DirectoryManifestStore("agent-registry/registry/")

# Or for a single agent in its own repo:
# store = LocalFileManifestStore("agents/email-triage/manifest.yaml")

decision = service.promote(request)
manifest = apply_decision(decision, store)
# - APPROVED: manifest is the updated AgentManifest, written to disk
# - REJECTED / DEFERRED: returns None; manifest stays at current stage
```

`apply_decision` semantics:

| Decision | Effect |
|---|---|
| APPROVED | Load manifest, set `lifecycle_stage = target_stage`, save back, return updated manifest. |
| REJECTED | No-op. Returns `None`. The audit log already records why. |
| DEFERRED | No-op. Returns `None`. The decision is also enqueued in the `PendingApprovalStore`; a reviewer resolves it via `service.resolve_approval(...)` and the caller chains a fresh `apply_decision`. |

The split is intentional: the pipeline produces the *decision*, and the
caller decides *when* to apply it. A workflow that requires a second
out-of-band approval step (e.g., a Slack-mediated review for SCALE)
calls `apply_decision` only after that approval lands.

### Two store implementations

| Store | When to use |
|---|---|
| `LocalFileManifestStore(path)` | Single `manifest.yaml` in a team's own repo. |
| `DirectoryManifestStore(root)` | `<root>/<agent_id>/manifest.yaml` layout — fits both `agent-registry/registry/` and `arc/agents/`. |

Custom backends (S3, DynamoDB, a registry API) implement the
`ManifestStore` protocol: `load(agent_id)`, `save(manifest)`,
`exists(agent_id)`.

---

## Demotion

Promotion has a sibling: `service.demote()`. Demotion bypasses gate
checks (the whole point is to roll back) and is always recorded in
the audit log.

```python
decision = service.demote(
    agent_id    = "email-triage",
    from_stage  = LifecycleStage.SCALE,
    to_stage    = LifecycleStage.GOVERN,
    requester   = "auto-demotion-watcher",
    reason      = "error rate exceeded 5% over 1h window",
)
apply_decision(decision, store)
```

Forward and backward use the same audit mechanism — there's no second
"demotion log." Every state change of every agent lives in one
append-only stream.

---

## Anomaly auto-demotion

`service.demote()` is the primitive; the policy layer that decides *when*
to demote ships as `arc.core.DemotionWatcher` plus the `arc agent watch`
CLI.

### Declaring SLOs in the manifest

An agent opts in by adding an `slo:` block to its `manifest.yaml`. The
block is optional — an agent without one is never auto-demoted.

```yaml
slo:
  window:        7d                 # rolling window the watcher looks at
  min_volume:    100                # skip evaluation below this many events
  rules:
    - metric:    error_rate         # built-in
      op:        "<"
      threshold: 0.05
    - metric:    p95_latency_ms     # built-in
      op:        "<"
      threshold: 2000
    - metric:    intervention_send_success_rate    # custom; agent emits via OutcomeTracker
      op:        ">"
      threshold: 0.8
  demotion_mode: proposed           # or "auto"
```

Built-in metrics (computed by `OutcomeTracker.window_stats`):
`event_count`, `error_rate`, `p50_latency_ms`, `p95_latency_ms`. Custom
metrics are anything the agent writes into `OutcomeEvent.data` — dotted
keys are supported (`plan.approval.rate`). Numeric values are averaged;
booleans become a success rate.

### How the watcher decides

For each agent in a `DirectoryManifestStore`:

1. Load `manifest.slo`. Skip if absent.
2. Skip if `lifecycle_stage` isn't in `{VALIDATE, GOVERN, SCALE}` (early
   stages aren't running on real traffic anyway).
3. Compute window stats from `OutcomeTracker`.
4. Skip if `event_count < min_volume` — not enough data to act on.
5. Evaluate every rule; collect breaches.
6. **Hysteresis:** require **3 consecutive breach evaluations** before
   firing. A single bad window doesn't move the agent; a sustained
   regression does. The counter is persisted in a
   `JsonlBreachStateStore` so it survives across cron runs.
7. **Cooldown:** look back `24h` in the audit log. If the agent had any
   APPROVED state change (promotion or demotion), don't fire again — let
   the recent change settle.
8. **Kill switch:** `ARC_AUTO_DEMOTE_DISABLED=1` short-circuits the
   whole pass with no I/O.
9. **Drop one stage** — `SCALE → GOVERN`, `GOVERN → VALIDATE`. Never two
   stages at once; most regressions are fixable.

### Two modes

`demotion_mode: proposed` (default — safer)

The watcher writes a `PendingApproval` with `request.kind = "demotion"`.
The same approval queue UI surfaces it; a human approves or rejects. The
manifest stage is **not** touched until a human resolves the entry.

`demotion_mode: auto`

The watcher calls `service.demote()` directly. The audit log records it
as APPROVED. Use only when the breach signal is well-understood and the
agent's blast radius is small. The watcher CLI's `--apply` flag is what
flips the manifest stage on disk for AUTO demotions; without it, AUTO
runs are audit-only (handy for dry runs).

### Running the watcher

The watcher is stateless — designed to be a cron job or k8s `CronJob`.

```bash
arc agent watch \
    --registry      arc/agents \
    --outcomes      outcomes.jsonl \
    --audit         promotion_audit.jsonl \
    --breach-state  breach_state.jsonl \
    --approvals     pending_approvals.jsonl
```

One pass, one exit. Re-runs every 15 min – 1 h are typical. Restarting
mid-run loses nothing: cooldown comes from the audit log, hysteresis
from the breach state file, and both are append-only JSONL.

### Single-watcher constraint

The four JSONL stores (audit, breach state, approvals, outcomes) all
use append-only writes; concurrent appends from multiple watchers on the
same machine are not safe (writes can interleave inside a line).
**Run one watcher per registry**. Multi-host or multi-process setups
will get a file lock or a registry backend in a follow-up.

### Safety summary

| Concern | Guard |
|---|---|
| Transient blip | 3 consecutive breach evaluations required |
| Flapping (demote-promote-demote) | 24h cooldown after any state change |
| Cascading demotions | One stage per pass, max one per agent per run |
| Insufficient data | `min_volume` floor; below it, evaluation is skipped |
| False positive on opt-in | No `slo:` block → never auto-demoted |
| Trust building | Default mode `proposed` keeps a human in the loop |
| Kill switch | `ARC_AUTO_DEMOTE_DISABLED=1` skips the entire pass |

---

## Audit log

Every promotion attempt — approved, rejected, deferred, demote — writes
one row to a `PromotionAuditLog`:

```python
from arc.core import JsonlPromotionAuditLog, InMemoryPromotionAuditLog

# Production: persist to disk
audit = JsonlPromotionAuditLog("audit/promotions.jsonl")

# Tests + harness:
audit = InMemoryPromotionAuditLog()

service = PromotionService(checker, audit_log=audit)
```

Each row carries the request, the gate results (per-check pass/fail
with reason), the outcome, the reasoning, the deciding party, and an
ISO timestamp. Reload via `audit.history(agent_id="...")` for that
agent's full transition history.

This is the lifecycle counterpart to the `JsonlAuditSink` for
individual tool calls. Two audit streams, one per layer:

| Layer | Audit log | Granularity |
|---|---|---|
| Governance (per-action) | `JsonlAuditSink` | One row per ALLOW/ASK/DENY decision |
| Lifecycle (per-stage-change) | `JsonlPromotionAuditLog` | One row per promotion / demotion |

Compliance reviews use both: the per-action log shows what the agent
*did* in each stage, the per-stage log shows *who approved* moving the
agent to that stage and *why*.

---

## Approval queue handoff (DEFERRED → human → resolved)

When `PromotionService` is constructed with a `PendingApprovalStore` and
`promote()` produces a `DEFERRED` outcome, the decision is enqueued to
the store in addition to landing in the audit log. A reviewer later
resolves it via `service.resolve_approval(approval_id, *, approve,
reviewer, reason)`, which:

1. Marks the entry in the pending store as `approved` or `rejected`.
2. Records a fresh `APPROVED` / `REJECTED` decision in the audit log
   carrying the original gate results and reviewer name.
3. Returns the new decision so the caller can chain `apply_decision`
   to update the manifest.

Two store implementations:

| Store | When to use |
|---|---|
| `InMemoryPendingApprovalStore` | Tests + harness — state lost on process restart. |
| `JsonlPendingApprovalStore(path)` | File-backed, append-only. Resolution writes a new line; readers keep the latest entry per `approval_id`. Crash-safe (torn JSON lines skipped). `list_history(id)` returns every state line for a full audit trail. |

Wiring example:

```python
from arc.core import (
    GateChecker, JsonlPendingApprovalStore, JsonlPromotionAuditLog,
    LifecycleStage, PromotionService,
)

audit  = JsonlPromotionAuditLog("promotions.jsonl")
queue  = JsonlPendingApprovalStore("pending-approvals.jsonl")
service = PromotionService(
    GateChecker(),
    audit_log=audit,
    require_human={LifecycleStage.SCALE},
    approval_store=queue,
)

# At promotion time:
decision = service.promote(req)        # DEFERRED → audit row + queue entry

# Later, after a reviewer decides:
new_decision = service.resolve_approval(
    approval_id, approve=True, reviewer="alice@compliance", reason="ROI verified",
)
manifest = apply_decision(new_decision, manifest_store)   # writes SCALE to disk
```

**Dashboard integration:** the ops React dashboard at
`arc-platform/frontend/ops/src/pages/Approvals.tsx` reads
`/api/approvals` and posts to `/api/approvals/{id}/decide`. A reviewer's
single click flips the queue entry, audits the resolution, and updates
the manifest in one round trip. See [arc-platform README](../../arc/packages/arc-platform/README.md) for the full flow.

---

## What's next on this layer

The lifecycle layer's core is shipped:

| Feature | Status |
|---|---|
| Manifest write-back (`ManifestStore` + `apply_decision`) | **Shipped** |
| Approval queue handoff for `DEFERRED` decisions | **Shipped** |
| Anomaly auto-demotion (`DemotionWatcher` + `arc agent watch`) | **Shipped** |

Follow-ups (not yet built):

- **Atomic manifest writes** — `save_manifest` currently uses a plain
  open-write-close. A write-temp-then-rename swap would survive a kill
  mid-write. Single-watcher operation makes the current shape safe in
  practice; this is hardening for multi-writer setups.
- **Demotion webhook** — fire a Slack/PagerDuty payload on demotion +
  proposal. The audit log + structured logs cover the same signal today,
  but a push notification is friendlier for on-call.
- **Multi-host watcher safety** — file lock or a registry backend so two
  watcher hosts can't trample each other's appends.

---

## Where to read next

- [Roadmap](../roadmap.md) — what's shipped vs in-flight on the lifecycle
  layer (and the platform overall).
- [Demo plan](../guides/demo.md) — runnable script that walks the
  lifecycle end-to-end, including auto-demotion.
- [Architecture](../architecture.md) — how the lifecycle layer fits with
  governance and runtime.
- [Governance](governance.md) — the runtime sibling: governance over
  individual actions.
- [Build an agent](../guides/build-an-agent.md) — including how to
  declare manifests that move through the pipeline.
- [`stages.py`](../../arc/packages/arc-core/src/arc/core/lifecycle/stages.py) — the
  full stage definitions.
- [`pipeline.py`](../../arc/packages/arc-core/src/arc/core/lifecycle/pipeline.py) — the
  promotion service implementation.
