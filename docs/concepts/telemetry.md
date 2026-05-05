# Telemetry — operational metrics for arc agents

> **Code:** [`arc/packages/arc-core/src/arc/core/telemetry.py`](../../arc/packages/arc-core/src/arc/core/telemetry.py)
> **Public API:** `from arc.core import Telemetry, NoOpTelemetry, CloudWatchEMFTelemetry, DatadogTelemetry, MultiTelemetry, telemetry_from_env`

Arc's audit log is the **compliance record** — what the agent did, why,
who reviewed it. That belongs in S3 with KMS, retained for years.

The audit log is *not* what an on-call engineer looks at when something
spikes at 2am. For that you need fast, queryable, dashboardable
**operational metrics**: ALLOW/ASK/DENY ratios, effect latency, token
spend, SLO breaches, redaction match counts.

`arc.core.telemetry` is the single emission point those metrics flow
through. Two production targets ship in-the-box and can run together:

- **CloudWatch (EMF)** — structured JSON to stdout. CloudWatch Logs
  auto-extracts metrics from any line containing the `_aws.CloudWatchMetrics`
  envelope. Works in Lambda, ECS Fargate, EKS, anywhere stdout reaches
  CloudWatch. Zero extra cost beyond the log line itself.
- **Datadog (DogStatsD)** — UDP packets to `127.0.0.1:8125`. Picked up
  by the Datadog Agent (sidecar on ECS/EKS) or the Datadog Lambda
  Extension. Fire-and-forget — silently drops if no listener is present.

The default is `NoOpTelemetry` — zero overhead in tests, sandboxes, and
any code path that doesn't opt in. Telemetry is *additive*; nothing
breaks if you don't configure it.

---

## Audit vs. telemetry — the bright line

| | Audit log | Telemetry |
|---|---|---|
| **Question it answers** | "What happened, why, who approved?" | "How is the agent behaving right now?" |
| **Consumer** | Auditors, compliance officers | On-call engineers, ops, PMs |
| **Retention** | Years (compliance-driven) | Days to weeks (operational) |
| **Storage** | S3 + KMS, immutable, versioned | CloudWatch / Datadog (mutable, expiring) |
| **Format** | Full structured rows with PII redacted | Counters, gauges, histograms with bounded tags |
| **Latency** | Eventual (write-once) | Near real-time |
| **Cost model** | Cheap per row, expensive at scale | Cheap at low cardinality, expensive if tags explode |

Same events flow into both — but the *shape* differs. The audit row has
the full `params`, `reason`, and `intent`. The metric has only:
`agent_id`, `effect`, `decision` (a tiny tag set, by design).

This split is deliberate. **Don't ever pipe raw audit rows into Datadog
or CloudWatch as logs** — that's mixing two retention models, two
access models, and two cost models.

---

## The metric vocabulary

All emitted metrics use the `arc.` prefix. Tags are snake_case and
bounded-cardinality (no `run_id`, no `user_id` — those go in the audit
row).

| Metric | Type | Tags | Emitted from |
|---|---|---|---|
| `arc.effect.outcome` | counter | `agent_id`, `effect`, `decision` (ALLOW/ASK/DENY/ERROR) | `BaseAgent.run_effect` |
| `arc.effect.latency_ms` | timing | `agent_id`, `effect` | `BaseAgent.run_effect` |
| `arc.llm.tokens_in` | counter | `agent_id`, `model`, `provider` | LLM clients |
| `arc.llm.chars_out` | counter | `agent_id`, `model`, `provider` | LLM clients |
| `arc.outcome.event` | counter | `agent_id`, `event_type` | `OutcomeTracker.record` |
| `arc.outcome.latency_ms` | timing | `agent_id`, `event_type` | `OutcomeTracker.record` (when `data.latency_ms` present) |
| `arc.redaction.match` | counter | `pattern` (SSN, EMAIL, ...) | `Redactor._redact_string` |

Notes:

- The `decision` tag carries the resolved outcome from Tollgate. ASK
  approvals that resolved to `approved` show as `ALLOW`; rejected ASKs
  and policy DENYs show as `DENY`. Errors (anything other than
  `TollgateDenied`) show as `ERROR`.
- LLM token counts are **estimates** (word-split for prompts; output
  characters for response). Exact provider-reported token usage is in
  the audit row's metadata, not the metric.
- `arc.redaction.match` is one counter call per pattern that matched in
  a string, with `value` = number of substitutions. So a string
  containing two SSNs emits one call with `value=2.0`.

---

## Wiring it

Three patterns, in order of how most teams adopt:

### 1. Environment-driven (recommended for production)

```bash
export ARC_TELEMETRY=cloudwatch+datadog
export ARC_TELEMETRY_NAMESPACE=Arc           # CloudWatch namespace
export DD_AGENT_HOST=127.0.0.1               # Datadog Agent host
export DD_DOGSTATSD_PORT=8125                # Datadog Agent port
```

```python
from arc.core import telemetry_from_env, BaseAgent

telemetry = telemetry_from_env()
agent = MyAgent(
    manifest=...,
    tower=...,
    gateway=...,
    telemetry=telemetry,        # zero overhead if env says noop
)
```

Valid `ARC_TELEMETRY` values: `noop` (default), `cloudwatch`, `datadog`,
`cloudwatch+datadog` (or comma-separated `cloudwatch,datadog`).

### 2. Explicit construction (for clarity in code)

```python
from arc.core import (
    BaseAgent,
    CloudWatchEMFTelemetry, DatadogTelemetry, MultiTelemetry,
)

telemetry = MultiTelemetry([
    CloudWatchEMFTelemetry(namespace="Arc"),
    DatadogTelemetry(host="127.0.0.1", port=8125, namespace="arc"),
])

agent = MyAgent(..., telemetry=telemetry)
```

### 3. Wired at `OutcomeTracker` and `Redactor` too

`BaseAgent` is the main emit site, but the other two carry their own
optional `telemetry` kwarg so all three signals flow through the same
emitter:

```python
from arc.core import OutcomeTracker, Redactor

tracker  = OutcomeTracker(path="outcomes.jsonl", telemetry=telemetry)
redactor = Redactor(telemetry=telemetry)

# Agent + tracker + redactor all emit into the same CloudWatch + Datadog stream.
```

---

## Three dashboards every agent should have

Same metric stream → three audiences. Build these in Datadog (or
CloudWatch dashboards if Datadog isn't available):

### Live operations (real-time, on-call)

- Current run count, status breakdown
- Last 60 min: ALLOW / ASK / DENY ratio bar
- Tail latency (p50, p95, p99) by effect
- Token burn rate (`arc.llm.tokens_in` rate per minute)
- Active suspended agents (kill-switch state — read from manifest store)

### Quality & governance (daily, ops + compliance)

- Decision distribution trend (7d / 30d)
- Top 10 most-asked effects (where humans are bottlenecked)
- Top 10 most-denied effects (where policy is biting)
- Approval latency p95 over time
- SLO breach count per week

### PII heatmap (compliance)

- `arc.redaction.match` rate per pattern over time
- Sudden spikes mean upstream data shape changed
- Drops mean either the data is cleaner *or* the pattern broke — both
  worth investigating

---

## Design rules the module enforces

Three rules every emitter must obey, baked into the contract:

1. **Never raises.** Telemetry that crashes business logic is worse
   than telemetry that silently misses. Every emit path swallows
   exceptions and logs at DEBUG.
2. **Never blocks.** Stdout writes are buffered; UDP is fire-and-forget.
   No emit path makes a blocking network call.
3. **Cardinality discipline.** Tag values are bounded by the metric
   vocabulary above. Never tag with `run_id`, `user_id`, request ID,
   email, or any free-form value — that lives in the audit log.

The wiring tests (`tests/test_telemetry_wiring.py`) explicitly cover
the "broken emitter doesn't break the agent" path for each call site.

---

## What this is NOT

| Limit | Why |
|---|---|
| **No traces / spans.** Counters, gauges, timings only. | Arc has audit rows for causal reconstruction; APM tracing is a Datadog product feature, not an arc concern. |
| **No metric pre-aggregation.** Every emit is one wire event. | Datadog and CloudWatch aggregate downstream. Adding aggregation here means another moving piece. |
| **No retry on emit failure.** | Telemetry is best-effort. A retried metric with stale timestamps is worse than a missed one. |
| **No backend-specific feature flags.** | If you need Datadog tags that don't fit DogStatsD, write a custom emitter — don't bend the protocol. |

---

## Where to read next

- [Governance](governance.md) — the broader trust model around audit + policy
- [Data redaction](data-redaction.md) — the PII boundary; `arc.redaction.match` lives there
- [Effects](effects.md) — the typed vocabulary `arc.effect.outcome` is tagged with
- [`arc/packages/arc-core/src/arc/core/telemetry.py`](../../arc/packages/arc-core/src/arc/core/telemetry.py) — the source. Short, readable; one file
