# Architecture diagrams

Visual companion to [architecture.md](architecture.md). Eight diagrams,
ordered from highest level (one-page platform picture) to lowest level
(sequence diagrams of individual call paths + the data artifact map).

> All diagrams are **Mermaid** — they render natively in the GitHub web
> view and live in the repo as plain text. No external tool needed.

---

## 1. Platform at a glance

The whole platform in one picture: an idea enters at DISCOVER and an
agent that has earned production trust exits at SCALE. Auto-demotion
provides the fall-back arrow when SLOs breach in production.

```mermaid
flowchart LR
    Idea([💡 Business idea]) --> D
    subgraph Pipeline["6-stage incubation pipeline"]
        direction LR
        D[DISCOVER<br/><i>business sponsor</i>]
        S[SHAPE<br/><i>product owner</i>]
        B[BUILD<br/><i>tech lead</i><br/>sandbox]
        V[VALIDATE<br/><i>business owner</i><br/>sandbox]
        G[GOVERN<br/><i>compliance officer</i><br/>sandbox]
        SC[SCALE<br/><i>operations owner</i><br/>🟢 production]
        D --> S --> B --> V --> G --> SC
    end
    SC -. "SLO breach<br/>(3× consecutive)" .-> G
    G -. "SLO breach<br/>(3× consecutive)" .-> V
    SC --> Live([🟢 Live agent<br/>governed every action])

    classDef stage fill:#eef,stroke:#557,stroke-width:1px,color:#000
    classDef prod  fill:#cfe,stroke:#393,stroke-width:2px,color:#000
    class D,S,B,V,G stage
    class SC prod
```

Each stage has an **entry criteria checklist**, **exit artifacts**, and a
**named reviewer role** (defined in [`stages.py`](../arc/packages/arc-core/src/arc/core/lifecycle/stages.py)).
The dashed lines are the auto-demotion path covered in §6.

---

## 2. Package architecture

Where the code lives. `arc/packages/` is a Python monorepo with seven
packages plus three siblings (`tollgate/`, `agent-registry/`,
`agent-team-template/`).

```mermaid
flowchart TB
    subgraph External["External surface"]
        CLI["arc-cli<br/>(arc agent ..., arc registry ..., arc agent watch)"]
        Plat["arc-platform<br/>(FastAPI + ops + dev React dashboards)"]
    end

    subgraph Orchestration["Orchestration layer"]
        RT["arc-runtime<br/>(RuntimeBuilder, deploy adapters)"]
        HR["arc-harness<br/>(HarnessBuilder, sandbox testing)"]
        OR["arc-orchestrators<br/>(LangGraph, AgentCore, Strands adapters)"]
    end

    subgraph Foundation["Foundation"]
        Core["arc-core<br/>effects · manifest · BaseAgent · gateway · memory · tools<br/>observability · lifecycle (promotion + auto-demotion)"]
        Conn["arc-connectors<br/>BedrockLLM · LiteLLM · Outlook · Pega · ServiceNow"]
        Eval["arc-eval<br/>scenario evaluation framework"]
    end

    subgraph Siblings["Sibling packages (not in arc/)"]
        Tg["tollgate<br/>canonical policy engine<br/>(ControlTower, YamlPolicyEvaluator,<br/>JsonlAuditSink, AsyncQueueApprover)"]
        Reg["agent-registry<br/>(central manifest catalog)"]
        Tpl["agent-team-template<br/>(starter for new teams)"]
    end

    CLI  --> RT
    CLI  --> HR
    CLI  --> Core
    Plat --> Core
    Plat --> Tg
    RT   --> Core
    RT   --> Conn
    HR   --> Core
    OR   --> Core
    Core --> Tg
    Conn --> Core
    Eval --> Core
    Reg  -.governance catalog.-> Core
    Tpl  -.scaffolds against.-> Core

    classDef foundation fill:#fef3c7,stroke:#b45309,color:#000
    classDef external   fill:#dbeafe,stroke:#1e40af,color:#000
    classDef orch       fill:#fce7f3,stroke:#9d174d,color:#000
    classDef sibling    fill:#e5e7eb,stroke:#374151,color:#000
    class Core,Conn,Eval foundation
    class CLI,Plat external
    class RT,HR,OR orch
    class Tg,Reg,Tpl sibling
```

Read this as a strict dependency graph: arrows point from consumer to
producer. Foundation packages know nothing about orchestration or the
external surface. Tollgate is a **sibling** package, not a child — it's
intentionally swappable.

---

## 3. Layered governance — every action funnels through the same stack

The platform's central claim: *every* effect — internal compute, an
external API call, an LLM prompt — travels the same governance path. No
back doors.

```mermaid
flowchart TB
    AC["Agent code<br/><code>self.run_effect(...)</code>"]
    BA["BaseAgent.run_effect<br/>1. <code>manifest.allows_effect()</code> → PermissionError if no<br/>2. Build Intent + ToolRequest<br/>3. Hand off to ControlTower"]
    CT["ControlTower<br/>1. Policy evaluator → ALLOW / ASK / DENY<br/>2. On ASK: approver (sync auto, async SQS, CLI)<br/>3. Audit sink: write outcome to JSONL"]
    EX["Effect executor<br/>(internal fn · gateway connector ·<br/>LLM provider · tool registry)"]

    AC --> BA --> CT
    CT -- "DENY: PermissionError" --> AC
    CT -- "ASK pending: TollgateDeferred" --> AC
    CT -- "ALLOW: invoke" --> EX
    EX --> CT
    CT -- "result + audit row" --> BA
    BA --> AC

    classDef agent fill:#dbeafe,stroke:#1e40af,color:#000
    classDef gov   fill:#fef3c7,stroke:#b45309,color:#000
    classDef exec  fill:#dcfce7,stroke:#166534,color:#000
    class AC agent
    class BA,CT gov
    class EX exec
```

Two non-obvious properties:
- **The agent never decides whether to run** — it only declares intent. ControlTower decides.
- **The audit row is written before the executor returns** — even on a panic, the audit trail captures the decision.

---

## 4. Sequence: one effect call

The canonical end-to-end flow when an agent calls `self.run_effect(...)`.
Every column is a real object you can grep for in the codebase.

```mermaid
sequenceDiagram
    autonumber
    participant A as Agent (BaseAgent subclass)
    participant T as ControlTower
    participant P as PolicyEvaluator<br/>(Yaml)
    participant V as Approver
    participant L as AuditSink<br/>(JSONL)
    participant X as Effect executor

    A->>A: manifest.allows_effect(...)?
    Note over A: PermissionError if effect<br/>not in allowed_effects
    A->>T: evaluate(intent, tool_request)
    T->>P: evaluate(intent)
    P-->>T: Decision (ALLOW / ASK / DENY)

    alt DENY
        T->>L: record(decision, outcome=denied)
        T-->>A: TollgateDenied
    else ASK
        T->>V: request_approval(intent)
        V-->>T: ApprovalOutcome
        opt async (SQS / human review)
            V-->>T: deferred → returns later
            T->>L: record(decision, outcome=deferred)
            T-->>A: TollgateDeferred
        end
    else ALLOW (or ASK approved)
        T->>X: invoke(exec_fn)
        X-->>T: result
        T->>L: record(decision, outcome=allowed)
        T-->>A: result
    end
```

Audit rows always land — even on DENY and DEFERRED. That's the central
property compliance reviews care about.

---

## 5. Sequence: SCALE promotion with human approval

The DEFERRED → human → resolved flow. The promotion is split across
three actors and lands in three persistent stores; the dashboard is the
only synchronous human touchpoint.

```mermaid
sequenceDiagram
    autonumber
    participant E  as Engineer (CLI / CI)
    participant PS as PromotionService
    participant GC as GateChecker
    participant AL as PromotionAuditLog<br/>(JSONL)
    participant PA as PendingApprovalStore<br/>(JSONL)
    participant D  as Ops dashboard
    participant R  as Compliance reviewer
    participant MS as ManifestStore

    E->>PS: promote(req: GOVERN → SCALE)
    PS->>GC: evaluate(req)
    GC-->>PS: GateCheckResult[] (all passed)
    Note over PS: target SCALE in require_human?<br/>→ outcome = DEFERRED
    PS->>AL: record(deferred decision)
    PS->>PA: enqueue(deferred decision) → approval_id
    PS-->>E: DEFERRED (no manifest change yet)

    Note over D,R: ... time passes ...
    R->>D: open /approvals
    D->>PA: list_pending()
    PA-->>D: [PendingApproval{kind: promotion, ...}]
    R->>D: click "Approve"
    D->>PS: resolve_approval(id, approve=true, reviewer)
    PS->>PA: resolve(id, approved=true)
    PS->>AL: record(APPROVED decision)
    PS-->>D: PromotionDecision (APPROVED)
    D->>MS: apply_decision(decision)
    MS-->>D: manifest with lifecycle_stage=SCALE
    D-->>R: ✓ promoted to SCALE
```

Same pattern works for any stage in `require_human`. Reject path is
symmetric — just `approve=false` and no `apply_decision` call.

---

## 6. Sequence: auto-demotion watcher pass

What happens on each `arc agent watch` invocation. Stateless by
design — every signal is read fresh from disk so the watcher is safe
on a 5-min cron.

```mermaid
sequenceDiagram
    autonumber
    participant CR as Cron / k8s CronJob
    participant W  as DemotionWatcher<br/>(arc agent watch)
    participant MS as ManifestStore<br/>(directory)
    participant OT as OutcomeTracker<br/>(outcomes.jsonl)
    participant SE as SLO evaluator<br/>(pure)
    participant BS as BreachStateStore<br/>(JSONL counter)
    participant AL as PromotionAuditLog
    participant PA as PendingApprovalStore

    CR->>W: arc agent watch --registry ...
    Note over W: Kill switch?<br/>ARC_AUTO_DEMOTE_DISABLED=1 → exit
    W->>MS: agent_ids()
    MS-->>W: [agent-1, agent-2, ...]

    loop for each agent
        W->>MS: load(agent_id)
        MS-->>W: manifest (may carry slo: block)
        Note over W: skip if no slo, or stage<br/>not in {VALIDATE,GOVERN,SCALE}
        W->>OT: window_stats(agent_id, window_seconds)
        OT-->>W: {event_count, error_rate, p95_latency_ms, ...}
        W->>SE: evaluate_slo(slo_config, stats)
        SE-->>W: SLOReport (skipped or breaches[])

        alt below min_volume
            Note over W: skipped:no-data<br/>(don't touch breach counter)
        else breach observed
            W->>BS: record(agent_id, breached=True)
            BS-->>W: state{consecutive_breaches: N}
            alt N < threshold (3)
                Note over W: action=breach-pending
            else N ≥ threshold
                W->>AL: history(agent_id) → cooldown check
                AL-->>W: recent APPROVED decisions
                alt within 24h cooldown
                    Note over W: action=cooldown
                else cooldown expired
                    alt demotion_mode=auto
                        W->>AL: record(demote APPROVED)
                        Note over W: action=demoted
                    else demotion_mode=proposed (default)
                        W->>AL: record(demote DEFERRED)
                        W->>PA: enqueue(deferred, kind=demotion)
                        Note over W: action=proposed<br/>(human resolves via dashboard)
                    end
                end
            end
        else no breach
            W->>BS: record(agent_id, breached=False)
            Note over W: counter resets to 0<br/>action=ok
        end
    end

    W-->>CR: exit 0 (idempotent — restart-safe)
```

Read this as four guards stacked: kill switch → eligibility (stage +
SLO presence) → min_volume floor → 3-evaluation hysteresis → 24h
cooldown. Only after **all** five pass does the watcher fire.

---

## 7. Sequence: LLM call with policy

LLMs are not special. The `LLMClient` Protocol routes every model call
through `agent.run_effect()` so prompt size, model id, and provider land
in the same audit row as any tool call.

```mermaid
sequenceDiagram
    autonumber
    participant A  as Agent
    participant LC as LLMClient<br/>(Bedrock or LiteLLM)
    participant BA as BaseAgent.run_effect
    participant CT as ControlTower
    participant L  as AuditSink
    participant PR as Provider<br/>(boto3 / litellm)

    A->>LC: generate(agent=self, effect=..., prompt=..., system=...)
    LC->>BA: run_effect(effect, tool="bedrock_llm",<br/>action="invoke", params, exec_fn=provider_call)
    BA->>CT: evaluate(intent, tool_request)
    Note over CT: Same policy + approver + audit<br/>flow as any other effect
    CT-->>BA: Decision (ALLOW / ASK / DENY)
    alt ALLOW
        BA->>PR: provider_call() (the exec_fn)
        PR-->>BA: completion text
        BA->>L: record(outcome=allowed,<br/>metadata={provider, model, prompt_size})
        BA-->>LC: completion
        LC-->>A: response text
    else DENY / DEFERRED
        BA->>L: record(outcome=denied|deferred)
        BA-->>LC: TollgateDenied | TollgateDeferred
        LC-->>A: ⚠ raised — agent can fall back
    end
```

The `LLMClient` is **stateless w.r.t. the agent** — `agent` is a
per-call argument, not a constructor argument. One client instance is
shared safely across agents. Provider selection is governed by
[`LLMConfig`](concepts/llm-clients.md): platform default ←
manifest override ← `with_llm()` explicit injection.

---

## 8. Data artifacts — who reads, who writes

Five JSONL files own all of the platform's persistent state. All
append-only, all crash-safe (a torn line is just skipped on read).

```mermaid
flowchart LR
    subgraph Writers["Writers"]
        AG["Agent (run_effect)"]
        OT["OutcomeTracker.record"]
        PS["PromotionService<br/>.promote / .demote / .resolve_approval"]
        W["DemotionWatcher<br/>(arc agent watch)"]
        D["Dashboard /approvals"]
    end

    subgraph Files["Append-only JSONL files"]
        AUD["audit.jsonl<br/><i>(Tollgate effect audit)</i>"]
        OUT["outcomes.jsonl<br/><i>(business-level outcome events)</i>"]
        PAU["promotion_audit.jsonl<br/><i>(stage transitions)</i>"]
        BRE["breach_state.jsonl<br/><i>(consecutive-breach counter,<br/>latest line wins)</i>"]
        APP["pending_approvals.jsonl<br/><i>(DEFERRED queue,<br/>latest line wins)</i>"]
    end

    subgraph Readers["Readers"]
        Dops["Ops dashboard<br/>(approval queue, audit view)"]
        Wat["DemotionWatcher<br/>(window_stats, cooldown)"]
        Ana["Analytics / SIEM<br/>(downstream)"]
    end

    AG --> AUD
    OT --> OUT
    PS --> PAU
    PS --> APP
    W  --> BRE
    W  --> PAU
    W  --> APP
    D  --> APP
    D  --> PAU

    AUD --> Dops
    AUD --> Ana
    OUT --> Wat
    OUT --> Ana
    PAU --> Dops
    PAU --> Wat
    PAU --> Ana
    BRE --> Wat
    APP --> Dops

    classDef writer fill:#dbeafe,stroke:#1e40af,color:#000
    classDef file   fill:#fef3c7,stroke:#b45309,color:#000
    classDef reader fill:#dcfce7,stroke:#166534,color:#000
    class AG,OT,PS,W,D writer
    class AUD,OUT,PAU,BRE,APP file
    class Dops,Wat,Ana reader
```

Two of the files (`breach_state`, `pending_approvals`) use a
**latest-line-wins** convention so the file remains append-only while
still representing mutable state — a state change appends a new line
with the same key, and readers reconstruct by walking the file.

---

## How to update these diagrams

When code changes break a diagram:
1. Update the diagram in this file alongside the code change in the
   same commit (don't let docs drift).
2. Mermaid is plain text — diff it like any other code.
3. To preview locally: paste the block into [mermaid.live](https://mermaid.live/),
   or open the file on GitHub.

When adding a **new** diagram:
- Slot it where it belongs in the high → low order.
- Keep one purpose per diagram. If you find yourself drawing two
  things, split them.
- Use Mermaid only — no SVG / PNG. Plain text round-trips through PR
  review.

---

## Where to read next

- [Architecture](architecture.md) — prose narrative of the same picture.
- [Lifecycle](concepts/lifecycle.md) — deep dive on stages, promotion,
  and auto-demotion (diagrams 1, 5, 6).
- [Governance](concepts/governance.md) — deep dive on the layered
  funnel (diagrams 3, 4).
- [LLM clients](concepts/llm-clients.md) — deep dive on the LLM call
  path (diagram 7).
- [Demo plan](guides/demo.md) — runs through the diagrams as a live
  walkthrough.
- [Roadmap](roadmap.md) — what's shipped vs in flight.
