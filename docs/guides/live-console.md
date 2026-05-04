# Live console + kill switches + feedback capture

The ops dashboard's per-agent operational surface — what's happening
right now, who flagged what as wrong, and a permanent kill switch
top-of-page. This guide is the user-facing spec for what's shipping
and what's coming next.

---

## What's shipped today

| Feature | Where it lives | Status |
|---|---|---|
| **Suspend / Resume buttons** on the Agents inventory page | `/agents` → `Controls` column | ✅ Shipped |
| **Modal with required reason field** for suspend | Click ⚠ Suspend on a row | ✅ Shipped |
| **Audit trail** of every suspend/resume action | Audit log + dashboard | ✅ Shipped |
| **Suspended-state badge** in the inventory | Status column shows red SUSPENDED badge | ✅ Shipped |
| **`Correction` capture API** | `POST /api/agents/{id}/corrections` | ✅ Shipped |
| **Corrections list + summary API** | `GET /api/agents/{id}/corrections[/summary]` | ✅ Shipped |
| **Live stats API** | `GET /api/agents/{id}/stats?window_minutes=N` | ✅ Shipped |

## What's coming (next PR)

| Feature | Effort | Notes |
|---|---|---|
| Per-agent live page at `/agents/<id>/live` | 2 days | SSE stream of audit rows, top stats card, currently-processing pane, verbosity toggle |
| Inline "Flag as wrong" button on each activity row | 0.5 day | Calls the corrections API |
| "Recent corrections" panel at the bottom of the live page | 0.5 day | Reads corrections summary endpoint |
| Top-of-page kill switch, always visible while scrolling | 0.5 day | Same modal, stickier placement |

The backend is built; the frontend page comes in a follow-up PR once
the live API surface has run for a day or two in dev.

---

## Using the kill switch today

Anyone with access to the ops dashboard can suspend an agent. The
mechanism mirrors `arc agent suspend` exactly — same audit row, same
manifest write — but reachable from a browser instead of an SSH session.

**To suspend:**

1. Open `http://localhost:5173` (the ops dashboard)
2. Navigate to **Agents**
3. Find the agent's row → click the red **⚠ Suspend** button in the
   `Controls` column
4. In the modal:
   - Enter your username (e.g. `alice@compliance`) — required
   - Enter a reason (e.g. `incident-1234, classifier returning wrong case_type`)
     — **required for suspend** (every kill-switch action gets a reason)
   - Click **Suspend agent**
5. The row updates: status flips to red `SUSPENDED`. New runs of the
   agent will refuse to instantiate (`BaseAgent.__init__` raises if
   `manifest.status == suspended`).

**To resume:**

1. Same row, the button now reads **Resume**
2. Modal asks for username (required) + optional reason
3. Click **Resume agent**
4. Status flips back to `active`. New runs work again.

Re-suspending a suspended agent (or resuming an active one) returns
HTTP 409 — the dashboard surfaces "agent is already suspended/active"
inline. **You can't double-suspend by accident.**

---

## Capturing a correction (via API today; UI in next PR)

Until the live page ships, corrections are captured via `curl` (useful
for scripting Pega webhook ingestion now):

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

That records a `Correction` to `corrections.jsonl`. See
[concepts/feedback.md](../concepts/feedback.md) for the full data model
+ how it feeds Layer 3 (in-context injection) when that ships.

The roll-up the dashboard will eventually render is already callable:

```bash
curl http://localhost:8000/api/agents/email-triage/corrections/summary
```

```json
{
  "total":        17,
  "by_severity":  { "minor": 5, "moderate": 9, "critical": 3 },
  "by_reviewer":  { "alice@compliance": 12, "bob@ops": 5 },
  "top_patterns": [
    { "pattern": "loan_hardship → distribution",       "count": 9 },
    { "pattern": "team: loans-standard → loans-senior", "count": 4 }
  ]
}
```

---

## What the live page will look like (next PR)

```
╭─ email-triage · LIVE ──────────────  [● ACTIVE]   ⚠ Suspend ╮
│                                                              │
│  Today                                                       │
│  ┌──────────┬──────────┬──────────┬──────────┬───────────┐  │
│  │ Emails   │ Pending  │ Avg lat  │ Error    │ Top type  │  │
│  │ 47       │ 3 ASK    │ 4.2s     │ 2.1%     │ distrib.  │  │
│  └──────────┴──────────┴──────────┴──────────┴───────────┘  │
│  ALLOW ████████████  78%   ASK ███  18%   DENY ░  4%         │
│                                                              │
│  Verbosity:  [ Quiet ] [● Normal ] [ Verbose ]               │
│                                                              │
│  Live activity                                               │
│  ──────────────────────────────────────────────────────────  │
│  14:32:08  RM-...0047  loan_hardship/hardship  ✓ [Flag wrong]│
│  14:32:06  RM-...0046  ticket.create distribution  ✓         │
│  14:31:51  RM-...0045  ticket.create  ASK queued  ⏸          │
│  14:31:33  RM-...0044  fraud_flag → DENY          ✕          │
│                                                              │
│  Recent corrections (last 7 days)                            │
│  17 flagged · 12 case-type misclassifications · 3 wrong-team │
╰──────────────────────────────────────────────────────────────╯
```

Three verbosity levels (resist a fourth):

| Level | Stream contents | Use case |
|---|---|---|
| **Quiet** | DENIES + ASKs + errors only | Daily ops monitoring |
| **Normal** | Above + every ticket creation + approval resolutions | Spot-checking during pilot (default) |
| **Verbose** | Above + every effect call (classify, extract, route, draft) | Engineer debugging |

Verbosity is **per-session** — toggling Verbose in your tab doesn't
flood Datadog / increase log ingestion costs; it just streams more to
your browser. The audit log itself is unchanged.

---

## Reference

- [`Suspend / Resume` API](../../arc/packages/arc-platform/src/arc/platform/api/routes.py) — `POST /api/agents/{id}/suspend`, `POST /api/agents/{id}/resume`
- [`Corrections` API](../../arc/packages/arc-platform/src/arc/platform/api/routes.py) — `POST /api/agents/{id}/corrections`, `GET /api/agents/{id}/corrections[/summary]`
- [`Stats` API](../../arc/packages/arc-platform/src/arc/platform/api/routes.py) — `GET /api/agents/{id}/stats?window_minutes=N`
- [`arc.core.feedback`](../../arc/packages/arc-core/src/arc/core/feedback.py) — `Correction` data model + `JsonlCorrectionsStore`
- [Feedback concept](../concepts/feedback.md) — the five-layer model + roadmap

---

## Where to read next

- [Feedback loop concepts](../concepts/feedback.md) — what corrections
  are for, how they feed back into the agent (Layer 3+)
- [Lifecycle](../concepts/lifecycle.md) — auto-demotion already reads
  outcomes; corrections feed the same metric stream
- [Architecture diagrams](../architecture-diagrams.md) — where this UI
  fits in the layered governance picture
