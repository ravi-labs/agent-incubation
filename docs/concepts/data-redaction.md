# Data redaction — keeping PII inside the trust boundary

> **Code:** [`arc/packages/arc-core/src/arc/core/redactor.py`](../../arc/packages/arc-core/src/arc/core/redactor.py)
> **Public API:** `from arc.core import Redactor, RedactingAuditSink, Pattern`

Arc audits everything an agent does. That trail is operationally
valuable but is also a liability surface — a participant emailing
"my SSN is 123-45-6789" sends that string straight into:

- The audit log (long-term retention)
- Datadog / CloudWatch / Splunk (operational logs)
- The LLM prompt (third-party provider with its own retention policy)

The redactor is the bright line between *"agent code in our trust
boundary sees the real value"* and *"values leaving the boundary are
redacted."* Two boundaries are surfaced explicitly.

---

## Two trust boundaries

```
        ┌─────────────────────────────────────┐
        │  Inside our trust boundary           │
        │                                      │
        │   Agent code     ◄─── real values    │   ← agent sees PII to do its job
        │                                      │
        │   ↓                                  │
        │                                      │
        └────────┬────────────┬────────────────┘
                 │            │
                 │            │   ↓ redacted ↓
                 │            │
   ┌─────────────┴───┐   ┌────┴──────────────┐
   │ System of record │   │ Third-party LLM   │
   │ (Pega, etc.)     │   │ (Bedrock, OpenAI) │
   │                  │   │                   │
   │ ✅ unredacted    │   │ ❌ redacted only   │
   │   (encrypted    │   │   (provider may   │
   │    in transit + │   │    log prompts)   │
   │    at rest)     │   │                   │
   └──────────────────┘   └───────────────────┘
                 │
                 ↓
        ┌─────────────────┐
        │  Audit log       │
        │  ❌ redacted only │   (downstream → Datadog / SIEM)
        └─────────────────┘
```

**Agent in: real values** — that's its job.
**Pega in: real values** — that's the system of record.
**LLM out: redacted** — third-party boundary.
**Audit log out: redacted** — operational logs may be widely accessible.

---

## What ships in the default pattern set

Conservative — only universally-sensitive shapes that are safe to apply
to free text without false-positive concerns:

| Pattern | What it catches |
|---|---|
| `SSN`         | `\b\d{3}-\d{2}-\d{4}\b` (dashed form only by default) |
| `CREDIT_CARD` | 13–19 digit sequences with optional separators |
| `ROUTING`     | `ABA: 026073150` style US bank routing numbers |
| `EMAIL`       | RFC-shaped email addresses |
| `PHONE`       | US phone formats — `555-123-4567`, `(555) 123-4567`, `+1 555.123.4567` |

Each match becomes `[REDACTED-LABEL]` (e.g. `[REDACTED-SSN]`) — labelled,
not blanked, so the audit trail stays readable.

### Opt-in additions

| Pattern | Why opt-in |
|---|---|
| `BARE_SSN_PATTERN` (9 bare digits) | Over-redacts (zip+4 codes, account suffixes); enable only when you've audited the false-positive cost |
| Custom domain patterns | Plan IDs, account numbers — the shape varies per tenant; declare per agent |

---

## Where to wire the redactor

### Boundary 1 — audit sink

Wrap any `AuditSink`. Every dict / list / string field gets redacted
*before* the inner sink writes:

```python
from arc.core import RedactingAuditSink, Redactor
from tollgate import JsonlAuditSink, ControlTower, YamlPolicyEvaluator

sink = RedactingAuditSink(
    inner    = JsonlAuditSink("audit.jsonl"),
    redactor = Redactor(),
)
tower = ControlTower(policy=..., approver=..., audit=sink)
```

Structural fields (`timestamp`, `agent_id`, `manifest_version`,
`decision`, `outcome`, etc.) pass through untouched — the dashboard and
SIEM still index decisions correctly.

### Boundary 2 — LLM provider

Pass a `Redactor` into the LLM client constructor. Every prompt + system
message is redacted before reaching the provider:

```python
from arc.core import Redactor
from arc.connectors import BedrockLLMClient, LiteLLMClient

llm = BedrockLLMClient(
    model_id = "anthropic.claude-3-5-sonnet-20241022-v2:0",
    redactor = Redactor(),
)

llm = LiteLLMClient(
    model    = "anthropic/claude-3-5-sonnet-20241022",
    redactor = Redactor(),
)
```

For LangGraph agents using `governed_chat_model`:

```python
from arc.core import Redactor
from arc.orchestrators import governed_chat_model

llm = governed_chat_model(
    chat_model    = ChatBedrockConverse(model="..."),
    agent         = self,
    effect        = ITSMEffect.EMAIL_CLASSIFY,
    intent_action = "classify_email",
    intent_reason = f"Classify email {email_id}",
    redactor      = Redactor(),
)
```

The audit row's `prompt_chars` reflects the **original** input size
(what the agent saw), not the redacted size — so audit metrics aren't
distorted. Only the wire goes redacted.

---

## Adding domain-specific patterns

Retirement domain example: redact participant IDs + plan IDs:

```python
import re
from arc.core import Redactor, Pattern

retirement_patterns = (
    Pattern("PARTICIPANT_ID", re.compile(r"\bP-\d{5,}\b")),
    Pattern("PLAN_ID",         re.compile(r"\bPLAN-\d{4,6}\b")),
)

redactor = Redactor(extra=retirement_patterns)
```

Custom replacement to preserve the last 4 of a card number:

```python
last_four = Pattern(
    "CARD_LAST4",
    re.compile(r"\b(?:\d{4}[\s-]?){3}(\d{4})\b"),
    replacement = r"[REDACTED-CARD-…\1]",
)
redactor = Redactor(patterns=(last_four,))
```

---

## What the redactor does NOT do

| Limit | Why |
|---|---|
| **No NER / ML-based detection** — pattern matching only | Auditable; compliance reviewers can read the patterns. ML-based PII detection is opaque + flaky |
| **No encryption-at-rest** — that's the storage layer's job (S3 + KMS, etc.) | Different concern; see `tollgate.security.encryption` for AES-GCM at-rest if needed |
| **No tenant-scoped redaction** | Single-tenant today |
| **Doesn't redact images / audio / binary content** | Pattern-based. Multimodal redaction is a separate problem |
| **Never raises** | A redactor that crashes on malformed input is worse than one that occasionally misses; we log and pass through |

---

## Recommended deployment for the retirement email-triage pilot

```python
from arc.core import Redactor, Pattern, RedactingAuditSink
from tollgate import JsonlAuditSink, ControlTower, YamlPolicyEvaluator
import re

# Domain extras — retirement plan + participant IDs are PII-adjacent.
retirement_patterns = (
    Pattern("PARTICIPANT_ID", re.compile(r"\bP-\d{5,}\b")),
    Pattern("PLAN_ID",         re.compile(r"\bPLAN-\d{4,6}\b")),
)
redactor = Redactor(extra=retirement_patterns)

# Boundary 1 — audit sink wrapping
audit = RedactingAuditSink(
    inner    = JsonlAuditSink("audit.jsonl"),
    redactor = redactor,
)

# Boundary 2 — LLM client (passed into governed_chat_model in graph.py)
llm = BedrockLLMClient(model_id="...", redactor=redactor)
```

That's it. **Two construction calls; PII never leaves the trust boundary
into the audit stream or the LLM provider.** Pega still gets unredacted
case data via the connector, because that's the system of record.

---

## Where to read next

- [Governance](governance.md) — the broader trust model around audit + policy
- [LLM clients](llm-clients.md) — provider-agnostic LLM routing
- [Email-triage SSN scenario](../guides/email-triage-integration.md) — concrete example of the trust-boundary discipline
