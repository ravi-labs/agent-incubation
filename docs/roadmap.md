# Roadmap

Single source of truth for what's shipped, what's in flight, and what's
on the backlog. Commit hashes link to evidence; doc links jump to the
concept page that covers the feature.

> **How this is maintained.** When a feature ships: move it to the
> Shipped table with the merge commit. When a follow-up is committed
> in code (TODO/FIXME) or in a doc ("What's next" section), it lives in
> In-flight. Pure ideas with no implementation start on the Backlog.

---

## Shipped

The platform-level capabilities below are live on this branch and have
test coverage in `arc/packages/*/tests/`.

### Foundation

| Feature | Concept doc | Evidence |
|---|---|---|
| Effect taxonomy (5 domains, 6 tiers, default decisions) | [Effects](concepts/effects.md) | `arc.core.effects.*` |
| Manifest schema (YAML + dataclass + back-compat loader) | [Build an agent](guides/build-an-agent.md) | `arc.core.manifest.AgentManifest` |
| `BaseAgent.run_effect()` — single governance entry point | [Governance](concepts/governance.md) | `arc.core.agent.BaseAgent` |
| Tollgate `ControlTower` (policy + audit + approver) | [Governance](concepts/governance.md) | sibling `tollgate/` package |
| Gateway abstraction + `MultiGateway` router | [Architecture](architecture.md) | `arc.core.gateway` |

### Lifecycle

| Feature | Concept doc | Evidence |
|---|---|---|
| 6-stage pipeline (DISCOVER → SCALE) | [Lifecycle](concepts/lifecycle.md) | `arc.core.lifecycle.stages` |
| `PromotionService` + gate checks + audit log | [Lifecycle](concepts/lifecycle.md) | `arc.core.lifecycle.pipeline` |
| Manifest write-back (`ManifestStore` + `apply_decision`) | [Lifecycle](concepts/lifecycle.md) | [`afc3af3`](https://github.com/ravi-labs/agent-incubation/commit/afc3af3) |
| Approval queue handoff (DEFERRED → human → resolved) | [Lifecycle](concepts/lifecycle.md) | [`ffcbb9f`](https://github.com/ravi-labs/agent-incubation/commit/ffcbb9f) |
| Anomaly auto-demotion (SLO schema + watcher + `arc agent watch`) | [Lifecycle](concepts/lifecycle.md) | [`f409be3`](https://github.com/ravi-labs/agent-incubation/commit/f409be3) |

### LLMs

| Feature | Concept doc | Evidence |
|---|---|---|
| `LLMClient` Protocol (Bedrock + LiteLLM impls) | [LLM clients](concepts/llm-clients.md) | [`8a5ecf9`](https://github.com/ravi-labs/agent-incubation/commit/8a5ecf9) |
| `LLMConfig` precedence: explicit > manifest > platform default | [LLM clients](concepts/llm-clients.md) | [`3f3ae84`](https://github.com/ravi-labs/agent-incubation/commit/3f3ae84) |

### Platform / surface

| Feature | Concept doc | Evidence |
|---|---|---|
| FastAPI backend + two React dashboards (`ops`, `dev`) | [`arc-platform/README`](../arc/packages/arc-platform/README.md) | [`d5d4c8a`](https://github.com/ravi-labs/agent-incubation/commit/d5d4c8a) |
| `arc-cli`: agent new / list / validate / promote / suspend / resume / watch, registry submit / list, effects list / show | [Build an agent](guides/build-an-agent.md) | `arc.cli.main` |
| HarnessBuilder (sandbox runs, fixtures, mock LLM) | [Build an agent](guides/build-an-agent.md) | `arc.harness.builder` |
| RuntimeBuilder (production wiring, env-driven config) | — | `arc.runtime.builder` |
| 7 reference agents under `arc/agents/` | — | `retirement-trajectory`, `fiduciary-watchdog`, `email-triage`, `care-coordinator`, `life-event-anticipation`, `plan-design-optimizer`, `contract-review` |
| Connectors: Outlook, ServiceNow, Pega Case, Pega Knowledge, Bedrock Agent / KB / Guardrails, mock fixtures | — | `arc.connectors.*` |

### Naming + cleanup

| Feature | Evidence |
|---|---|
| Foundry → arc rebrand (hard rename, tollgate canonicalization) | [`c44c2a8`](https://github.com/ravi-labs/agent-incubation/commit/c44c2a8), [`c6ecdb2`](https://github.com/ravi-labs/agent-incubation/commit/c6ecdb2), [`0323613`](https://github.com/ravi-labs/agent-incubation/commit/0323613) |
| `manifest.foundry_version` → `arc_version` (back-compat loader keeps old key working) | [`c6ecdb2`](https://github.com/ravi-labs/agent-incubation/commit/c6ecdb2) |
| Architecture + concept docs rewritten to match shipped state | [`9b0cd02`](https://github.com/ravi-labs/agent-incubation/commit/9b0cd02), [`f8aee03`](https://github.com/ravi-labs/agent-incubation/commit/f8aee03) |

---

## In flight

Started or unblocked, partial in code or docs. Each links to the
concrete next step.

| Feature | What remains | Evidence |
|---|---|---|
| **Atomic manifest writes** | `save_manifest` does plain open-write-close; switch to write-temp-then-rename | [`lifecycle.md` → "Follow-ups"](concepts/lifecycle.md#whats-next-on-this-layer) |
| **Demotion / proposal webhook** | Optional `demotion_webhook_url` on `RuntimeConfig`; POST decision JSON on demote/proposed | [`lifecycle.md` → "Follow-ups"](concepts/lifecycle.md#whats-next-on-this-layer) |
| **Multi-host watcher safety** | File lock or registry backend so two `arc agent watch` hosts can't trample appends | [`lifecycle.md` → "Single-watcher constraint"](concepts/lifecycle.md#single-watcher-constraint) |
| **Cost / token telemetry** | `LLMClient` records prompt size today; aggregate cost/token rollups not shipped | [`llm-clients.md` → "What's NOT in this layer"](concepts/llm-clients.md) |
| **Streaming LLM responses** | Both clients return full strings; streaming is a harness/runtime task | [`llm-clients.md` → "What's NOT in this layer"](concepts/llm-clients.md) |
| **CloudWatch + S3 audit sinks** | `RuntimeBuilder._build_audit_sink` falls back to JSONL with a TODO | [`builder.py:272`](../arc/packages/arc-runtime/src/arc/runtime/builder.py:272) |
| **CORS hardening for arc-platform** | Env-var support drafted, not integrated; blocks real cloud deploy | [`arc-platform/src/arc/platform/api/server.py`](../arc/packages/arc-platform/src/arc/platform/api/server.py) |

---

## Backlog

Captured ideas without implementation. Each has a one-pager in
[`docs/backlog.md`](backlog.md) covering the case for / against.

| Item | Status | Doc |
|---|---|---|
| 🟡 LangGraph governance gap — `governed_chat_model` adapter wrapping `BaseChatModel` so LangGraph node LLM calls route through `run_effect()` | Idea, unblocked | [backlog.md → LangGraph](backlog.md) |
| 🟡 Language-agnostic agent runtime — wire protocol (gRPC/REST), Tollgate as a sidecar, per-language SDKs (Java, Go, Node) | Idea, blocked on demand | [backlog.md → Multi-language](backlog.md) |

---

## Phasing summary

How the work has bucketed historically:

- **Phase 1** (foundation) — effect taxonomy, manifest, ControlTower, gateway. *Shipped.*
- **Phase 2** (lifecycle + write-back) — 6-stage pipeline, manifest store, approval queue. *Shipped.*
- **Phase 3** (production surface) — LLM layer, two-dashboard arc-platform, anomaly auto-demotion, foundry rebrand. *Shipped.*
- **Phase 4** (hardening + reach) — atomic writes, multi-host watcher, webhooks, telemetry, LangGraph adapter. *In-flight.*

Phase boundaries aren't deadlines — they're a way to talk about the
shape of the platform at each milestone. Anything in the In-flight
table is a candidate for the next focused work session.

---

## Where to read next

- [Architecture](architecture.md) — how the layers fit together.
- [Lifecycle](concepts/lifecycle.md) — the deepest concept doc; covers
  promotion, demotion, and the audit trail.
- [Backlog](backlog.md) — long-form case for the two pending ideas.
- [Demo plan](guides/demo.md) — runnable script for showing the
  end-to-end lifecycle in 20 minutes.
