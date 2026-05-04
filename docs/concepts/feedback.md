# Feedback loop — humans correcting agent decisions

When a reviewer disagrees with an agent's decision, that disagreement
should be **captured, surfaced, and (eventually) fed back to the agent**
so it stops repeating the mistake. This page describes how arc captures
and surfaces corrections; in-context replay is on the roadmap.

> **Code:** [`arc/packages/arc-core/src/arc/core/feedback.py`](../../arc/packages/arc-core/src/arc/core/feedback.py)
> **Public API:** `from arc.core import Correction, CorrectionsStore, JsonlCorrectionsStore`

---

## The five-layer model

```
Layer 1   Capture corrections          Layer 2   Surface them in dashboards
   │                                       │
   └──→  reviewer flags an audit row   ─→  see "20 corrections this week,
         from the Investigate page          70% are case-type misclassif."
   │                                       │
   ▼                                       ▼
Layer 3   Inject as few-shot examples   Layer 4   Auto-propose policy changes
                                           │
                                           ▼
                              Layer 5   Pega → arc back-flow
                                       (HITL touchpoint #3)
```

**Layers 1 and 2 ship today.** Layer 3+ are roadmap items — but the
data model + API are designed so the later layers fit on top without
breaking changes.

---

## What a `Correction` is

```python
from arc.core import Correction

c = Correction.new(
    agent_id           = "email-triage",
    audit_row_id       = "abc-123-def",         # links back to the original audit row
    reviewer           = "alice@compliance",     # required — no anonymous corrections
    severity           = "moderate",             # minor | moderate | critical
    reason             = "case_type misclassification — was actually a distribution",
    original_decision  = {"case_type": "loan_hardship"},
    corrected_decision = {"case_type": "distribution"},
    schema_version     = "0.1.0-placeholder",    # of the agent at the time
)
```

The `original_decision` and `corrected_decision` are domain-agnostic
dicts — every agent stuffs its own structured fields in (`case_type`,
`team`, `severity`, …). This keeps the correction log useful across
all agents without per-agent schema work.

Each correction also auto-gets:
- `correction_id` — `corr-<12-hex-uuid>`
- `timestamp` — ISO 8601 UTC

---

## How corrections are captured

### From the dashboard (the typical path)

The "Flag as wrong" button on the `/agents/<id>/live` page (when it lands)
or the Investigate view opens a modal:

```
What was it actually?  case_type: [ distribution ▾ ] subtype: [ rmd ▾ ]
Reason (optional)      [ Email mentioned "hardship" but it was actually... ]
Severity               [ Minor ] [● Moderate ] [ Critical ]
                                                          [ Submit ]
```

POSTs to `/api/agents/<agent_id>/corrections` → `JsonlCorrectionsStore.record()` →
appends one row to `corrections.jsonl`.

### From a Pega webhook (Layer 5 — when the integration ships)

When an adjuster in Pega reclassifies the case the agent just created
(changes `pyCaseType` from `AutoClaim` to `PropertyClaim`), arc captures
that reclassification as an **implicit correction** — no human had to
click "Flag as wrong" in arc; the downstream system told us we were wrong.

Same `Correction` shape, same `JsonlCorrectionsStore`, same downstream
pipeline. The `reviewer` becomes the Pega adjuster's identity.

---

## How corrections are surfaced

Three places, today + planned:

| Surface | Today | Planned |
|---|---|---|
| Health view "corrections" panel | Roll-up: total / by severity / top patterns | (no change) |
| Live page corrections feed | — | Last 5 corrections, inline next to the activity stream |
| Investigate view inline annotations | — | Each audit row with a correction shows "◀ corrected by alice@" |

The `top_patterns` rollup is the most operationally useful number —
it catches recurring failure shapes without a human having to read every row:

```json
{
  "total":         17,
  "by_severity":   { "minor": 5, "moderate": 9, "critical": 3 },
  "by_reviewer":   { "alice@compliance": 12, "bob@ops": 5 },
  "top_patterns": [
    { "pattern": "loan_hardship → distribution", "count": 9 },
    { "pattern": "team: loans-standard → loans-senior", "count": 4 },
    { "pattern": "case_type: sponsor_inquiry → distribution", "count": 2 }
  ]
}
```

Read the top_patterns: *"the agent is mistaking distributions for
hardships nine times — go fix the keyword classifier or tighten the
LLM prompt."*

---

## API surface

| Method | Path | What it does |
|---|---|---|
| `POST` | `/api/agents/{agent_id}/corrections` | Record one correction (Layer 1) |
| `GET`  | `/api/agents/{agent_id}/corrections` | List recent corrections (`?limit=`, `?since=`) |
| `GET`  | `/api/agents/{agent_id}/corrections/summary` | Aggregated panel data (Layer 2) |

### Recording a correction

```bash
curl -X POST http://localhost:8000/api/agents/email-triage/corrections \
  -H 'Content-Type: application/json' \
  -d '{
    "audit_row_id":      "abc-123",
    "reviewer":          "alice@compliance",
    "severity":          "moderate",
    "reason":            "Was actually a distribution, not a hardship",
    "original_decision": {"case_type": "loan_hardship"},
    "corrected_decision": {"case_type": "distribution"}
  }'
```

Returns the full `Correction` object including `correction_id` + `timestamp`.

### Validation rules

| Input | Constraint | What happens if violated |
|---|---|---|
| `reviewer` | non-empty | 422 — anonymous corrections rejected |
| `severity` | one of `minor` / `moderate` / `critical` | 422 |
| `agent_id` (path) | must be a real agent | 404 |
| `corrections_log_path` | must be configured on `PlatformDataConfig` | 503 |

---

## Roadmap — Layers 3, 4, 5

### Layer 3 — in-context injection

When the agent's `classify_node` runs the LLM call, query recent
corrections matching this case (similar subject, same case_type
candidates) and prepend them as **counter-examples** in the prompt:

```
Past examples where you classified emails like this incorrectly:

  • [2 days ago] Email mentioning "hardship withdrawal for medical bills":
    you said loan_hardship; should have been distribution.

Now classify this email: ...
```

No retraining. No model change. The agent **learns immediately** on its
next call. Layer 1 already captures the data; Layer 3 just queries it
and shapes the prompt.

**Two safeguards:**
1. Cap to last 30 days of corrections.
2. Require ≥ 2 reviewers concur on a pattern before promoting it to the
   few-shot set — prevents one outlier from poisoning the agent.

### Layer 4 — auto-propose policy / threshold changes

Patterns the system could detect from `corrections_summary`:

| Pattern | Auto-proposal |
|---|---|
| Confidence < 0.85 corrections > 8% of audit rows | Tighten ASK threshold to 0.92 |
| Recurring `case_type: A → B` cluster | Add clarifying rule to the LLM system prompt |
| Reviewer rejects 80% of high-value distributions | Lower the ASK threshold from $25k to $10k |

Proposals open as PRs against `policy.yaml` for compliance to merge.
The platform suggests; humans decide.

### Layer 5 — Pega → arc back-flow (HITL touchpoint #3)

The Pega connector polls case state once a day, compares against the
agent's original decisions in the audit log, and writes corrections
when adjusters reclassify / close-as-duplicate / escalate. Same
`JsonlCorrectionsStore` — Layer 1 doesn't know whether the trigger was
a click in arc's dashboard or an external system.

---

## What this gives the regulatory story

The compliance pitch shifts from *"the agent decided X"* to:

> **"The agent decided X. A human reviewed and flagged the decision.
> The platform recorded the correction, surfaced it in the trend
> dashboard, and the next time the agent sees a similar email it will
> have past mistakes as counter-examples in its prompt."**

That's the loop that makes the agent **provably improving under human
supervision** — exactly the picture compliance officers in regulated
domains want to see.

---

## Where to read next

- [Live console](../guides/live-console.md) — where the "Flag as wrong"
  button lives (when shipped)
- [Lifecycle](lifecycle.md) — corrections feed the auto-demotion
  watcher's outcome metrics
- [Architecture diagrams](../architecture-diagrams.md) — the full
  governance picture
