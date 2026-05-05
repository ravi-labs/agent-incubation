# Governance

Governance is the runtime layer that decides whether each tool call
runs. It enforces three things, in order, before any side effect occurs:

1. **Manifest scope** — is this effect on the agent's declared list?
2. **Policy** — does the YAML policy ALLOW / ASK / DENY this call right now?
3. **Audit** — is the decision recorded with enough context to reconstruct it later?

The component that does this is `ControlTower` — provided by the
[`tollgate`](../../tollgate/) package, which arc imports directly. Tollgate
has no dependency on arc; it's the trust boundary, designed to be
inspected on its own.

> **Code:** [`tollgate/src/tollgate/`](../../tollgate/src/tollgate/)
> **Public API:** `from tollgate import ControlTower, YamlPolicyEvaluator, JsonlAuditSink, AutoApprover, AsyncQueueApprover`

---

## The three-outcome model

Every effect request resolves to exactly one of:

| Outcome | What happens | When it fires |
|---|---|---|
| **ALLOW** | Executor runs immediately. Audit row written. Result returned. | Default for low-tier reads + computes; effects whose policy permits the current context. |
| **ASK** | Execution pauses. Approver is invoked (sync or async). On approval, the call resumes; on rejection, `TollgateDenied` is raised. | Default for tier-4+ outbound effects; any effect where policy requires human review. |
| **DENY** | Audit row written. `TollgateDenied` raised. Executor never runs. | Hard-denied effects (`legal.advice.render`); effects that violate manifest scope; policy explicit DENY. |

Three outcomes, deterministic at the call site. The agent's `execute()`
either returns a value (ALLOW), suspends until the approver replies
(ASK), or raises (DENY). No fourth case to handle.

---

## ControlTower

`ControlTower` is the conductor. It's stateless: the same instance can
serve every concurrent request the agent makes. Construction wires four
things:

```python
from tollgate import (
    ControlTower, YamlPolicyEvaluator, JsonlAuditSink, AutoApprover,
)

tower = ControlTower(
    policy   = YamlPolicyEvaluator("policies/"),     # rules
    approver = AutoApprover(),                        # what happens on ASK
    audit    = JsonlAuditSink("audit.jsonl"),         # decision log
    # Optional: rate_limiter, circuit_breaker, grant_store, ...
)
```

In production the approver is `AsyncQueueApprover` backed by SQS +
DynamoDB; in a sandbox or eval suite it's `AutoApprover` (always returns
true) or `CliApprover` (prompts the developer in the terminal).

The tower exposes one core method that `BaseAgent.run_effect` calls
internally:

```python
decision = await tower.evaluate(intent, request)
# decision.outcome ∈ {ALLOW, ASK, DENY}
# decision.reason  is the human-readable rationale
```

`Intent` and `ToolRequest` are typed primitives carrying the agent
context (manifest version, tenant, caller) and the effect details
(name, params, tier).

---

## Policy YAML

The policy file is the human-editable rule set. Compliance officers
read and review YAML; engineers do not write code to change rules.

```yaml
# policies/financial_services/erisa.yaml

rules:
  # Default: tighten outbound effects to require human review
  - resource_type: "participant.communication.send"
    decision: ASK
    reason: >
      Outbound communications to plan participants must be reviewed
      by a fiduciary representative before transmission (ERISA §404(a)).

  # Tighten further by context — high-balance accounts always ASK
  - resource_type: "participant.communication.send"
    when:
      params.account_balance: { gt: 100000 }
    decision: ASK
    reason: High-balance accounts require senior advisor review.

  # Hard-deny — irreversible at the policy level
  - resource_type: "fiduciary.advice.render"
    decision: DENY
    reason: >
      Personalized fiduciary advice requires a credentialed advisor.
      The agent must defer to a human via referral.
```

The evaluator walks rules top-to-bottom, returns the first match. Order
matters; specificity goes first.

Policies are *layered*: a team's per-agent `policy.yaml` is layered on
top of the shared `policies/financial_services/{defaults,erisa}.yaml`
files. A team can tighten — never loosen.

---

## Audit log

Every decision (ALLOW, ASK, DENY) writes one JSONL row:

```json
{
  "timestamp":        "2026-04-26T14:31:08Z",
  "agent_id":         "retirement-trajectory",
  "manifest_version": "retirement-trajectory@1.2.0",
  "policy_version":   "erisa-v3",
  "intent":           {"caller": "scheduler", "tenant": "acme", ...},
  "request":          {"resource_type": "participant.communication.send",
                       "params": {...}},
  "decision":         "ASK",
  "reason":           "Outbound communications must be reviewed (ERISA §404(a))",
  "approver":         "alice@compliance",
  "approval_ms":      1840
}
```

`JsonlAuditSink` is the file-backed default. Production deployments use
`S3AuditSink` (write to versioned, immutable storage) or the
`ImmutableAuditSink` wrapper that signs each row.

The audit log is the *primary* compliance artifact. Everything else —
the manifest, the policy file, the evaluator — exists to make every row
in this log defensible.

---

## ASK and the approver protocol

`AutoApprover` is for sandbox + eval; it returns `ApprovalOutcome.APPROVED`
immediately. `AsyncQueueApprover` is for production; it:

1. Writes the request to a `DynamoDBApprovalStore` with `pending` status.
2. Notifies a queue (`SQSApprover` for AWS).
3. Returns a coroutine that suspends until the store row flips to
   `approved` or `rejected`, or the configured timeout fires.

A reviewer hits the queue, reviews the audit context, and clicks
approve / reject. The async store flip resumes the agent.

This means a single agent run can span seconds (auto-approved) or
hours (waiting on a human) without changing the agent code. The same
`await self.run_effect(...)` line covers both.

---

## How ControlTower wires into BaseAgent

`BaseAgent.run_effect()` is the only entry point agent code uses. It does:

1. Builds a typed `ToolRequest` from `(effect, tool, action, params)`.
2. Builds an `Intent` from the agent context (manifest, caller).
3. Calls `tower.evaluate(intent, request)`.
4. On ALLOW: invokes the executor (a callable registered via `tools.governed_tool`
   or passed directly to `run_effect(executor=...)`).
5. On ASK: blocks on the approver; on resolve, branches to ALLOW / DENY.
6. On DENY: raises `TollgateDenied`. Caller code wraps in try/except for
   graceful UX, or lets it propagate.

The agent never sees the policy file, the audit sink, or the approver
directly. They're all wired at construction time and stay invisible to
the business logic — which is exactly what makes the system non-bypassable.

---

## Failure modes the tower guards against

| Failure | Guard |
|---|---|
| Agent calls an undeclared effect | Manifest scope check → `PermissionError` |
| Policy file is malformed | `YamlPolicyEvaluator.__init__` validates on load |
| Audit sink unreachable | `ControlTower` blocks the call rather than executing without an audit row |
| Approver never replies | Timeout (configurable per request); on timeout the call DENYs |
| Suspended agent (`status: suspended`) | `BaseAgent.__init__` raises before any execute starts |

Each is a separate test in `arc-core/tests/test_base_agent.py` and
`tollgate/tests/`.

---

## Where to read next

- [Architecture diagrams](../architecture-diagrams.md#3-layered-governance--every-action-funnels-through-the-same-stack)
  — diagrams 3 and 4 show the layered governance stack and the canonical
  `run_effect` sequence.
- [Data redaction](data-redaction.md) — pattern-based PII redaction at the
  audit sink + LLM boundary. The bright line between agent code (sees
  real values) and external systems (see redacted values).
- [Telemetry](telemetry.md) — operational metrics (CloudWatch EMF +
  Datadog DogStatsD) emitted from `BaseAgent.run_effect`,
  `OutcomeTracker`, and `Redactor`. The bright line between
  *compliance audit* (S3, years) and *operational signals*
  (Datadog/CloudWatch, days).
- [Effects](effects.md) — the typed vocabulary that ControlTower
  evaluates.
- [Lifecycle](lifecycle.md) — governance over time (the promotion
  pipeline) is a sibling of the runtime governance ControlTower
  provides.
- [`tollgate/README.md`](../../tollgate/README.md) — the tollgate
  package overview.
- [`tollgate/src/tollgate/tower.py`](../../tollgate/src/tollgate/tower.py) — the
  ControlTower source. Short, readable; worth a pass.
