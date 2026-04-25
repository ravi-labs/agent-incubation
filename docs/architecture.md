# Architecture

Arc is an agent incubation platform for regulated domains. Every action an
agent takes is **declared in a manifest, evaluated against policy, and
audited** before it executes. Agents move through a six-stage pipeline —
DISCOVER → SHAPE → BUILD → VALIDATE → GOVERN → SCALE — with explicit gates
between stages.

This document is the top of the docs tree. Mid-level concept guides live
under [`concepts/`](concepts/); hands-on walkthroughs under [`guides/`](guides/).

---

## The four big abstractions

Everything in arc reduces to four primitives. Learn these and the rest of
the surface follows.

| Primitive | What it is | Where it lives |
|---|---|---|
| **Effect** | A typed, named capability the agent can request (e.g., `participant.communication.send`) | `arc.core.effects` |
| **Manifest** | The agent's declared scope: which effects, which data, what stage | `arc.core.manifest` |
| **ControlTower** | Runtime policy enforcement — every tool call passes through it | `tollgate` (canonical) |
| **Pipeline** | Lifecycle stages + the promotion service that moves agents through them | `arc.core.lifecycle` |

The execution loop is short:

```
agent.execute()
    ↓
agent.run_effect(effect=..., tool=..., action=..., params=...)
    ↓
ControlTower checks: does the manifest declare this effect?
                     does policy ALLOW / ASK / DENY it?
    ↓
on ALLOW → executor runs, audit log written, result returned
on ASK   → human review queue (sync or async approval)
on DENY  → PermissionError, audit log written, no execution
```

No tool call bypasses ControlTower. The kill switch (`status: suspended`
on the manifest) blocks at the class level, so even buggy agent code
can't run.

---

## Package layout

Arc is a multi-package monorepo. Each package has a clear audience and
narrow surface.

```
arc/
└── packages/
    ├── arc-core/         governance engine — every other package depends on this
    ├── arc-harness/      sandbox testing — fixtures, shadow audit, decision reports
    ├── arc-runtime/      production wiring — RuntimeConfig, Lambda + Bedrock + secrets adapters
    ├── arc-cli/          `arc agent new/list/validate/promote/suspend`
    ├── arc-eval/         scenario-based regression evaluation
    ├── arc-orchestrators/ adapters for LangGraph, AgentCore, Strands, LangChain
    ├── arc-connectors/   real-system gateways — Outlook, Pega, ServiceNow, Bedrock
    └── arc-platform/     web portals (reserved for Phase 3)
```

Plus three sibling packages at the repo root:

- **[`tollgate/`](../tollgate/)** — the policy engine. Provides `ControlTower`,
  `YamlPolicyEvaluator`, `JsonlAuditSink`, async approver primitives. arc
  depends on tollgate; tollgate has no dependency on arc.
- **[`agent-registry/`](../agent-registry/)** — the central governance catalog.
  Manifests only; no code. Submit a PR here when an agent reaches GOVERN.
- **[`agent-team-template/`](../agent-team-template/)** — starter template for
  a new team's agent repo.

The reference agents under [`arc/agents/`](../arc/agents/) (seven of them,
across financial-services, healthcare, legal, ITSM) are not a package —
they're concrete implementations you can read, copy, and adapt.

---

## Layered architecture

From the top down:

### 1. The pipeline layer (governance over time)

[`arc.core.lifecycle`](../arc/packages/arc-core/src/arc/core/lifecycle/) —
defines the six stages, the gate-check primitives, and the
`PromotionService` that orchestrates transitions. `apply_decision()` writes
the new stage back to a `ManifestStore` (single file or registry directory).

This is the layer that compliance officers and platform engineers care
about: who promotes agents, on what evidence, and where the audit trail
lives.

See: [Lifecycle concepts](concepts/lifecycle.md).

### 2. The governance layer (governance over individual actions)

`tollgate.ControlTower` + the YAML policy file + `JsonlAuditSink`. Every
tool call gets a typed `Decision` (ALLOW / ASK / DENY) and a row in the
audit log. ControlTower is the trust boundary; nothing executes that the
tower didn't approve.

This is the layer that auditors and security reviewers inspect.

See: [Governance concepts](concepts/governance.md).

### 3. The agent layer (developer surface)

`arc.core.BaseAgent` — every team subclasses this. Inside `execute()`,
business logic calls `self.run_effect(...)` instead of touching SDKs
directly. `arc.core.AgentManifest` declares the agent's scope. Memory,
tools, and the gateway are wired via the manifest at construction time.

This is the layer agent developers spend their time in.

See: [Build an agent](guides/build-an-agent.md).

### 4. The taxonomy layer (the language of effects)

`arc.core.effects` — five domain enums (`FinancialEffect`, `HealthcareEffect`,
`LegalEffect`, `ITSMEffect`, `ComplianceEffect`), six tiers (Data Access →
Computation → Draft → Output → Persistence → System), default decisions
(ALLOW, ASK, DENY) and metadata for every effect.

This is the layer that determines what agents *can* express. New domains
get added here.

See: [Effects](concepts/effects.md).

### 5. The substrate (everything plugs into this)

`arc.core.gateway` (data access), `arc.core.memory` (persistent state),
`arc.core.tools` (governed tool registry), `arc.core.observability`
(outcome tracker + audit reports). All independently swappable: an
agent in a test harness uses `MockGatewayConnector` and a JSONL audit
sink; the same agent in production uses `HttpGateway` and DynamoDB.

The harness/runtime split (`arc.harness.HarnessBuilder` vs
`arc.runtime.RuntimeBuilder`) wires the substrate two different ways
without any change to agent code.

---

## Two execution surfaces, one agent

Agents are written once. Where they run is a deploy concern.

| Surface | Purpose | Wiring |
|---|---|---|
| **Harness** (`arc.harness`) | Local + CI — exercise agents against fixtures, dump decision reports, run eval scenarios | `HarnessBuilder` |
| **Runtime** (`arc.runtime`) | Production — Lambda handler, Bedrock Agent adapter, AWS Secrets Manager loader | `RuntimeBuilder` / `RuntimeConfig.from_env()` |

The same `BaseAgent.execute()` runs in both. Only the gateway, approver,
audit sink, and memory backend differ.

---

## How effects flow through the system

A concrete trace, top-to-bottom:

1. **Engineer** declares the agent: writes `manifest.yaml` listing
   `allowed_effects: [participant.communication.send, ...]`, writes
   `policy.yaml` with rules, writes `agent.py` extending `BaseAgent`.

2. **Pipeline** validates the manifest, runs gate checks for the next
   stage (BUILD → VALIDATE → ...), records the promotion decision to
   the audit log, and calls `apply_decision()` to update the manifest's
   `lifecycle_stage`.

3. **Compliance officer** reviews the manifest in the agent-registry PR.
   On approval, the manifest moves to GOVERN; SCALE requires a second
   manual sign-off (`require_human={LifecycleStage.SCALE}`).

4. **Runtime** loads the manifest at cold start, builds a `ControlTower`
   wired to the YAML policy, the production approver (DynamoDB-backed),
   and the JSONL audit sink. Hands the tower to the agent.

5. **Agent** at request time: `await self.run_effect(effect=..., tool=...,
   action=..., params=...)`. The tower:
   - Checks the manifest declares the effect → else `PermissionError`.
   - Calls the policy evaluator → ALLOW / ASK / DENY.
   - On ASK, blocks until the approver returns (sync auto, or async
     human review via SQS + DynamoDB).
   - Writes an audit row.
   - On ALLOW, runs the executor and returns the result.

6. **Observer** tails the JSONL audit log and the OutcomeTracker stream.
   Anomalies feed the (forthcoming) auto-demotion watcher, which calls
   `service.demote()` to roll back stage state.

Every step has a typed primitive, a test fixture, and an audit row.
Nothing is implicit.

---

## Where to read next

| If you want to… | Read |
|---|---|
| Build an agent end-to-end | [Build an agent](guides/build-an-agent.md) |
| Understand effects + tiers | [Effects](concepts/effects.md) |
| Understand policy enforcement | [Governance](concepts/governance.md) |
| Understand stages + promotion | [Lifecycle](concepts/lifecycle.md) |
| Deploy to AWS | [`deploy/bedrock-agent-core.md`](../deploy/bedrock-agent-core.md) |
| See real agents | [`arc/agents/`](../arc/agents/) (7 reference implementations) |
