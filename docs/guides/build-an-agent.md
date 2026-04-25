# Build an agent

This guide walks through building a governed agent end-to-end:
declaring scope in a manifest, writing a policy, implementing the
agent, and exercising it in the harness. Read [Architecture](../architecture.md)
first if you haven't — this guide assumes you know what an effect, a
manifest, and ControlTower are.

By the end you'll have:

- A working `manifest.yaml`, `policy.yaml`, and `agent.py` you can run.
- A failing test (because the agent calls an undeclared effect).
- A passing test (after you fix the manifest).
- A "production-style" run wired through `RuntimeBuilder` instead of
  `HarnessBuilder`, with no agent-code change.

We'll build a tiny **document-summarizer** agent. Not a real production
use case — small enough to fit in this guide, complete enough to show
the whole shape.

---

## Prerequisites

Install the platform from the monorepo root:

```bash
pip install -e tollgate/
pip install -e arc/packages/arc-core/
pip install -e arc/packages/arc-harness/
pip install -e arc/packages/arc-runtime/
pip install -e arc/packages/arc-cli/
```

Check it works:

```bash
arc effects list | head
arc --help
```

---

## Step 1 — Scaffold the directory

```bash
mkdir -p agents/doc-summarizer/tests
cd agents/doc-summarizer
touch manifest.yaml policy.yaml agent.py tests/test_agent.py
```

Layout we're aiming for:

```
agents/doc-summarizer/
├── manifest.yaml        ← scope: which effects, what stage
├── policy.yaml          ← per-agent rules (layered on shared defaults)
├── agent.py             ← BaseAgent subclass with execute()
└── tests/
    └── test_agent.py    ← exercises the agent in harness mode
```

---

## Step 2 — Write the manifest

The manifest is the agent's static contract: what stage it's at, what
effects it may invoke, what data it reads.

```yaml
# manifest.yaml
agent_id: doc-summarizer
version: "0.1.0"
owner: docs-team
description: >
  Reads internal documents, generates a short summary, and emits the
  summary to the requesting user. Sandbox-only until validated.

lifecycle_stage: BUILD       # see docs/concepts/lifecycle.md
environment: sandbox

# Effects this agent may request. Anything not on this list raises
# PermissionError at runtime, even if the policy would have allowed it.
allowed_effects:
  - knowledge.article.read       # ITSMEffect — Tier 1, default ALLOW
  - email.draft                  # ITSMEffect — Tier 3, default ALLOW
  - email.send                   # ITSMEffect — Tier 4, default ASK

# Data sources the gateway is allowed to reach
data_access:
  - knowledge_base
  - smtp_outbound

policy_path: policy.yaml

success_metrics:
  - "Summaries reduce read time by 50% vs. baseline"
  - "Recipient confirmation rate >= 70%"

team_repo: https://github.com/your-org/doc-summarizer-agent
arc_version: ">=0.1.0"
```

Validate it:

```bash
arc agent validate manifest.yaml
```

If a field is missing or an effect doesn't exist, you'll see an explicit
error pointing at the offending line.

---

## Step 3 — Write the policy

The policy file is YAML rules consumed by `tollgate.YamlPolicyEvaluator`.
It's *layered* on top of the shared `policies/` defaults (which set
sensible org-wide rules) — so this file is only the rules that *deviate*
from the defaults.

```yaml
# policy.yaml
rules:
  # Outbound email always routes to human review for now.
  # (Will be tightened to `if recipient.external` only after VALIDATE stage.)
  - resource_type: "email.send"
    decision: ASK
    reason: >
      Outbound communications are reviewed by a docs-team lead during
      sandbox + validate stages. Tighten in policy after VALIDATE.

  # Knowledge base reads are unconditionally allowed.
  - resource_type: "knowledge.article.read"
    decision: ALLOW
    reason: Read-only access to the public knowledge corpus.
```

Default decisions for unmatched effects fall through to the effect's
own `default_decision` in the metadata table.

---

## Step 4 — Write the agent

```python
# agent.py
from arc.core import BaseAgent, ITSMEffect

class DocSummarizerAgent(BaseAgent):
    """Reads documents, drafts a summary, sends it to the requester."""

    async def execute(self, *, doc_id: str, requester: str) -> dict:
        # 1. Tier 1 — read the doc. Default ALLOW.
        doc = await self.run_effect(
            effect = ITSMEffect.KNOWLEDGE_ARTICLE_READ,
            tool   = "knowledge",
            action = "fetch",
            params = {"id": doc_id},
        )

        # 2. Tier 3 — draft a summary using the LLM. Default ALLOW.
        draft = await self.run_effect(
            effect = ITSMEffect.EMAIL_DRAFT,
            tool   = "summarizer",
            action = "summarize",
            params = {"text": doc["body"], "max_words": 200},
        )

        # 3. Tier 4 — send. Policy says ASK, so this blocks on the approver.
        return await self.run_effect(
            effect = ITSMEffect.EMAIL_SEND,
            tool   = "smtp",
            action = "send",
            params = {"to": requester, "subject": doc["title"],
                      "body": draft["summary"]},
        )
```

Three things to notice:

- The agent never imports `boto3`, `httpx`, or any SDK. Tool wiring
  happens at construction time, outside the agent.
- Each `run_effect` call carries an explicit `effect=` argument. That's
  what links the call to the manifest (does this agent declare it?) and
  the policy (what does the rule say?).
- The third call's behavior depends on policy + approver. In a sandbox,
  `AutoApprover` returns immediately. In production, an
  `AsyncQueueApprover` blocks until a reviewer clicks approve in the
  queue. Same line of code.

---

## Step 5 — Write a harness test

Tests run the agent against fixtures, with a mock gateway and an
in-memory audit sink. No network, no AWS, no LLM.

```python
# tests/test_agent.py
import pytest
from arc.core import MockGatewayConnector, AutoApprover
from arc.harness import HarnessBuilder

from agent import DocSummarizerAgent


@pytest.mark.asyncio
async def test_happy_path_drafts_and_sends():
    gateway = MockGatewayConnector(fixtures={
        "knowledge.fetch": {"id": "doc-1",
                            "title": "Quarterly review",
                            "body": "Long text here..."},
        "summarizer.summarize": {"summary": "Short version."},
        "smtp.send": {"status": "sent", "id": "msg-001"},
    })

    agent = HarnessBuilder(
        agent_class = DocSummarizerAgent,
        manifest_path = "manifest.yaml",
        policy_path   = "policy.yaml",
        gateway       = gateway,
        approver      = AutoApprover(),     # auto-approves the ASK on email.send
    ).build()

    result = await agent.execute(doc_id="doc-1", requester="alice@team")

    assert result["status"] == "sent"
    # Verify what the agent actually did
    assert gateway.was_called("knowledge.fetch")
    assert gateway.was_called("smtp.send")
```

Run it:

```bash
pytest tests/ -v
```

---

## Step 6 — See the audit decisions

In harness mode, every decision lands in a JSONL audit sink that the
builder wires for you. Dump it:

```python
from arc.harness import HarnessBuilder

agent = HarnessBuilder(...).build()
await agent.execute(...)

for row in agent.tower.audit.entries:
    print(row.timestamp, row.request.resource_type, row.decision)
# 2026-04-26T... knowledge.article.read ALLOW
# 2026-04-26T... email.draft            ALLOW
# 2026-04-26T... email.send             ALLOW       (auto-approved in sandbox)
```

For a richer view, generate the HTML report:

```bash
arc audit report --in audit.jsonl --out audit.html
open audit.html
```

This is the same format compliance reviews work from in production.

---

## Step 7 — Force a failure to see the guards

Try calling an effect not on the manifest:

```python
# In agent.py, add a fourth step
await self.run_effect(
    effect = ITSMEffect.TICKET_CREATE,    # NOT in manifest.yaml
    tool   = "tickets",
    action = "create",
    params = {...},
)
```

Run the test. You'll see:

```
PermissionError: Effect 'ticket.create' is not in manifest.allowed_effects
                 for agent doc-summarizer@0.1.0
```

This is the manifest-scope guard from `BaseAgent`. The check happens
*before* anything calls the executor — there's no path around it.

Revert that change before moving on.

---

## Step 8 — Promote to VALIDATE

When sandbox tests are green, request promotion through the lifecycle
pipeline:

```python
from arc.core import (
    PromotionService, PromotionRequest, GateChecker, LifecycleStage,
    apply_decision, LocalFileManifestStore,
    stage_order_check, evidence_field_check,
)

checker = GateChecker()
checker.register(LifecycleStage.VALIDATE, stage_order_check())
checker.register(LifecycleStage.VALIDATE, evidence_field_check("test_results"))

service = PromotionService(checker)
store   = LocalFileManifestStore("manifest.yaml")

decision = service.promote(PromotionRequest(
    agent_id      = "doc-summarizer",
    current_stage = LifecycleStage.BUILD,
    target_stage  = LifecycleStage.VALIDATE,
    requester     = "alice@team",
    justification = "Sandbox tests passing, edge cases logged",
    evidence      = {"test_results": "tests/results-2026-04-26.json"},
))

manifest = apply_decision(decision, store)
print(manifest.lifecycle_stage)   # LifecycleStage.VALIDATE if approved
```

This writes the new `lifecycle_stage` to `manifest.yaml` on disk. The
audit log records who promoted, when, and on what evidence.

See [Lifecycle](../concepts/lifecycle.md) for the full stage map.

---

## Step 9 — Switch to production wiring

The same agent code goes to production behind `RuntimeBuilder` instead
of `HarnessBuilder`. The agent doesn't change; the wiring does:

```python
# handler.py — your Lambda entry point
from arc.runtime.deploy.lambda_handler import make_handler
from arc.connectors import HttpGateway
from agent import DocSummarizerAgent

handler = make_handler(
    DocSummarizerAgent,
    gateway = HttpGateway("https://internal-api.company.com/"),
).handler
```

`make_handler` reads `ARC_*` environment variables (manifest path,
policy directory, DynamoDB table for the approval store, SQS queue URL
for human review notifications) and assembles the same surface
`HarnessBuilder` did — just with production backends.

See [`deploy/bedrock-agent-core.md`](../../deploy/bedrock-agent-core.md)
for the full Lambda + Bedrock setup.

---

## What you've actually built

A small agent that:

- Declares its scope in a manifest. Anything off-manifest is a hard fail.
- Routes every action through `ControlTower`, with policy + audit
  baked in.
- Runs identically in test, sandbox, and production. Only the
  builder changes.
- Has a typed lifecycle stage you can promote forward (or demote
  backward) through the pipeline.
- Produces a JSONL audit row per action that any compliance reviewer
  can read directly.

Real reference agents — [`arc/agents/email-triage/`](../../arc/agents/email-triage/),
[`arc/agents/retirement-trajectory/`](../../arc/agents/retirement-trajectory/),
and five others — show this same pattern at production scale.
Read those next.

---

## Where to read next

- [Effects](../concepts/effects.md) — the typed vocabulary you just used.
- [Governance](../concepts/governance.md) — how `ControlTower` actually
  evaluates each `run_effect` call.
- [Lifecycle](../concepts/lifecycle.md) — how to move agents through
  stages safely.
- [`arc/agents/`](../../arc/agents/) — seven full reference agents.
