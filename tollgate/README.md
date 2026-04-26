# Tollgate — Runtime Policy Enforcement Engine

Tollgate is the policy enforcement layer for the [arc](../arc) platform. Every tool call an agent makes flows through Tollgate's ControlTower, which applies declarative YAML policies, routes human-review requests, and writes tamper-evident audit logs before any action executes.

## Core Concepts

**Three-outcome decision model:**
- `ALLOW` — execute immediately, log, continue
- `ASK` — pause execution, route to human approver, resume (or block) on decision
- `DENY` — block unconditionally, log the attempt, raise `TollgateDenied`

**Policy evaluation** is purely YAML — no code changes required to add or tighten a rule:

```yaml
rules:
  - resource_type: "participant.communication.send"
    decision: ASK
    reason: >
      Outbound participant communications require human review
      before transmission.

  - resource_type: "plan.config.write"
    decision: DENY
    reason: >
      Agents may never modify plan configuration directly.
```

**Audit trail** — every decision (ALLOW, ASK-approved, ASK-denied, DENY) is written as a structured JSON-lines record with the agent context, intent, policy match, and outcome.

## Installation

```bash
# Core (no optional backends)
pip install tollgate

# With AWS backends (DynamoDB approval store, SQS approver)
pip install "tollgate[aws]"

# With Redis (rate limiting, distributed grant store)
pip install "tollgate[redis]"

# Full production stack
pip install "tollgate[full]"
```

## Quick Start

```python
from tollgate import ControlTower, YamlPolicyEvaluator, AutoApprover, JsonlAuditSink

tower = ControlTower(
    policy=YamlPolicyEvaluator("policies/"),
    approver=AutoApprover(),        # replace with SQSApprover in production
    audit=JsonlAuditSink("audit.jsonl"),
)

# In your agent:
result = await tower.execute_async(
    agent_ctx=agent_ctx,
    intent=Intent(action="send_intervention", reason="Participant at risk"),
    tool_request=ToolRequest(
        tool="email_gateway",
        action="send",
        resource_type="participant.communication.send",
        effect=Effect.NOTIFY,
        params={"participant_id": "p-001", "content": draft},
    ),
    exec_async=send_email_fn,   # only called if ALLOW or ASK-approved
)
```

## What's Inside

| Module | Purpose |
|---|---|
| `tower.py` | `ControlTower` — main enforcement pipeline |
| `policy.py` | `YamlPolicyEvaluator` — YAML rule evaluation |
| `approvals.py` | `Approver` protocol + `AutoApprover`, `CliApprover`, `AsyncQueueApprover` |
| `audit.py` | `JsonlAuditSink`, `WebhookAuditSink`, `CompositeAuditSink` |
| `types.py` | Core types: `AgentContext`, `Intent`, `ToolRequest`, `Decision`, `AuditEvent` |
| `backends/` | `DynamoDBApprovalStore`, `SQSApprover`, `RedisRateLimiter`, `SQLiteStore` |
| `security/` | `FieldEncryptor`, `ImmutableAuditSink` (requires `cryptography`) |
| `workflow.py` | Multi-step approval workflow engine |
| `slo.py` | SLO monitoring and alerting |
| `reputation.py` | Per-agent reputation scoring |
| `policy_versioning.py` | Policy version history and diff |
| `integrations/` | MCP and Strands Agents adapters |

## Relationship to arc

Tollgate is the enforcement layer embedded inside the arc platform:

```
agent-incubation/
├── tollgate/           ← this package (standalone)
├── arc/                ← imports tollgate directly (every arc-* package)
├── agent-registry/
└── agent-team-template/
```

Agent teams use tollgate indirectly through `BaseAgent.run_effect()` — they never call ControlTower directly. This keeps the enforcement layer transparent and non-bypassable.

## Development

```bash
pip install -e ".[dev]"
pytest
```
