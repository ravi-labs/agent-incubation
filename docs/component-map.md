# Arc — component map & data flow

One diagram showing every major component in the codebase, what it's
responsible for, and the data flow from inbound email through the
agent to Pega (the system of record). The numbered arrows trace one
real triage run end-to-end.

```mermaid
flowchart TB
    %% ── External systems (above the platform) ───────────────────────
    subgraph EXT["🌐 External systems"]
        direction LR
        EMAIL["📧 Email system<br/><i>Outlook · Gmail · Exchange</i>"]
        BEDROCK["🤖 Bedrock / LiteLLM<br/><i>Foundation models</i>"]
        PEGA["🗄️ Pega<br/><i>System of record</i>"]
        DDOG["📊 Datadog<br/><i>Dashboards · alerts</i>"]
        S3["📦 S3 + KMS<br/><i>Audit log · 7y retention</i>"]
    end

    %% ── Agent layer (one per business agent) ────────────────────────
    subgraph AGENTS["🧩 Agent layer — what teams write"]
        direction TB
        MANIFEST["manifest.yaml<br/><b>Declared scope:</b> agent_id, version, allowed_effects, lifecycle_stage, SLOs"]
        POLICY["policy.yaml<br/><b>Decision rules:</b> ALLOW / ASK / DENY per resource_type"]
        AGENT_CODE["agent.py + graph.py<br/><b>Business logic:</b> 9 LangGraph nodes for email-triage"]
        SCHEMAS["pega_schemas/*.yaml<br/><b>Field map:</b> distribution / hardship / sponsor case types"]
    end

    %% ── Orchestration layer (interchangeable) ───────────────────────
    subgraph ORCH["🔀 Orchestration — interchangeable runtimes"]
        direction LR
        LG["LangGraphOrchestrator<br/><b>In-process graph executor</b>"]
        AC["AgentCoreOrchestrator<br/><b>AWS Bedrock AgentCore</b>"]
        DIRECT["DirectOrchestrator<br/><b>No graph; sequential run_effect</b>"]
        GCM["governed_chat_model<br/><b>LangChain LLM wrapper:</b><br/>routes ainvoke through run_effect"]
    end

    %% ── Arc core — governance contract ──────────────────────────────
    subgraph CORE["⚙️ arc.core — governance contract (every agent inherits this)"]
        direction TB

        BA["BaseAgent.run_effect()<br/><b>Single entry point</b><br/>for every governed action"]

        subgraph CORE_PRIMITIVES["primitives"]
            direction LR
            EFFECTS["Effects taxonomy<br/>5 domain enums × 6 tiers<br/><i>FinancialEffect, ITSMEffect, ...</i>"]
            BUILDER["EffectRequestBuilder<br/>Builds typed ToolRequest +<br/>Intent for ControlTower"]
            MEM["Memory<br/>ConversationBuffer +<br/>AgentMemoryStore<br/>(Local / DynamoDB)"]
            TOOLS["AgentToolRegistry<br/>governed_tool decorator"]
            GW["Gateway<br/>Mock / Http / Multi<br/><i>Declared data sources</i>"]
        end

        subgraph CORE_GOV["governance"]
            direction LR
            REDACT["Redactor<br/>PII patterns:<br/>SSN, card, email, phone, ABA"]
            TRACK["OutcomeTracker<br/>JSONL events for ROI<br/>+ SLO window stats"]
            TEL["Telemetry<br/>NoOp / CloudWatch EMF /<br/>Datadog DogStatsD"]
            LIFE["Lifecycle<br/>6-stage promotion +<br/>DemotionWatcher"]
            REG["ManifestStore<br/>Per-agent YAML registry"]
        end
    end

    %% ── Tollgate (sibling package — trust boundary) ─────────────────
    subgraph TOLLGATE["🛡️ tollgate — trust boundary (sibling package)"]
        direction TB
        TOWER["ControlTower<br/><b>Orchestrates every gate:</b><br/>policy → audit → approver"]
        POL_EVAL["YamlPolicyEvaluator<br/>Layered rule evaluation,<br/>compliance-readable"]
        AUDIT_PIPE["Audit pipeline<br/>JsonlAuditSink ·<br/>S3AuditSink · Composite ·<br/>Webhook · Redacting wrapper"]
        APPROVERS["Approvers<br/>Auto · Cli ·<br/>AsyncQueue (SQS+Dynamo)"]
        STORES["Stores<br/>Grants · Approvals ·<br/>Rate limiter · Circuit breaker"]
    end

    %% ── Connectors (external system clients) ────────────────────────
    subgraph CONN["🔌 Connectors — typed external clients"]
        direction LR
        BL["BedrockLLMClient<br/><i>+ Redactor injection</i>"]
        LL["LiteLLMClient<br/><i>cross-provider</i>"]
        BKB["BedrockKB<br/>+ BedrockGuardrails"]
        OUT["Outlook<br/>email connector"]
        PCASE["PegaCaseConnector<br/><b>creates ITSM cases</b>"]
        PKB["PegaKnowledgeConnector<br/><b>RAG knowledge lookup</b>"]
        SN["ServiceNowConnector"]
    end

    %% ── Platform support (cross-cutting) ────────────────────────────
    subgraph SUPPORT["🏗️ Platform support — cross-cutting"]
        direction LR
        HARNESS["arc-harness<br/><b>HarnessBuilder:</b> wires<br/>manifest + policy + tower<br/>+ fixtures for sandbox/eval"]
        RUNTIME["arc-runtime<br/><b>Lambda handler,</b><br/>Secrets Manager,<br/>Bedrock action group schema"]
        EVAL["arc-eval<br/><b>Replay + scoring</b><br/>against golden fixtures"]
        CLI["arc-cli<br/><b>Operator commands:</b><br/>lifecycle, manifest, watch"]
        PLATFORM["arc-platform<br/><b>FastAPI backend +</b><br/>React ops console<br/>(suspend, resume, feedback)"]
    end

    %% ── Deploy (infra-as-code) ──────────────────────────────────────
    subgraph DEPLOY["☁️ deploy — infra-as-code"]
        direction LR
        CDK_ARC["ArcAgentStack<br/>Lambda + DDB + SQS +<br/>S3 + KMS + IAM + CW"]
        CDK_BEDROCK["BedrockAgentStack<br/>Bedrock Agent + Action Group"]
        CDK_DD["DatadogForwarderConstruct<br/>CW Logs → Datadog"]
        DOCKERFILE["Dockerfile<br/>multi-stage container"]
        DASHBOARDS["Datadog dashboards/<br/>3× JSON: live ops,<br/>quality, PII heatmap"]
    end

    %% ─── Wiring (composition) ───────────────────────────────────────
    AGENTS    -->|builds| ORCH
    ORCH      -->|runs|   CORE
    CORE      ==>|every effect routes through| TOLLGATE
    BA        --> EFFECTS
    BA        --> BUILDER
    BA        -.->|reads/writes| MEM
    BA        -.->|tracks| TRACK
    BA        -.->|emits| TEL
    AGENT_CODE -.->|reads| MANIFEST
    AGENT_CODE -.->|reads| POLICY
    AGENT_CODE -.->|reads| SCHEMAS
    TOWER     --> POL_EVAL
    TOWER     --> AUDIT_PIPE
    TOWER     --> APPROVERS
    AUDIT_PIPE -.->|wraps with| REDACT
    POL_EVAL  -.->|reads| POLICY
    LIFE      -.->|reads| TRACK
    LIFE      -.->|reads| REG

    CORE -->|invokes| CONN
    GCM  -.->|wraps| BL

    %% ── Numbered email-to-Pega data flow ────────────────────────────
    EMAIL  -.->|① inbound email| OUT
    OUT    -.->|② Gateway.fetch email.inbox| GW
    GW     -.->|③ pass to agent| AGENT_CODE
    AGENT_CODE -.->|④ run_effect EMAIL_CLASSIFY| BA
    BA     -.->|⑤ ControlTower.execute_async| TOWER
    TOWER  -.->|⑥ policy ALLOW| POL_EVAL
    BA     -.->|⑦ governed LLM call| GCM
    GCM    -.->|⑧ redact PII| REDACT
    GCM    -.->|⑨ Bedrock invoke| BL
    BL     -.->|⑩ tokens via API| BEDROCK
    BEDROCK -.->|⑪ response| BL
    AGENT_CODE -.->|⑫ 7 more nodes\nentities, user, KB, dup, draft| BA
    BA     -.->|⑬ run_effect TICKET_CREATE| TOWER
    TOWER  -.->|⑭ P1/P2 → ASK gate| APPROVERS
    APPROVERS -.->|⑮ on APPROVED| BA
    BA     -.->|⑯ create_case| PCASE
    PCASE  -.->|⑰ Pega Case API| PEGA
    AGENT_CODE -.->|⑱ run_effect TRIAGE_LOG_WRITE| BA
    AUDIT_PIPE -.->|⑲ redacted rows| S3
    TEL    -.->|⑳ EMF + DogStatsD| DDOG

    %% ── Styling ─────────────────────────────────────────────────────
    classDef ext     fill:#e0e7ff,stroke:#3730a3,color:#1e1b4b
    classDef agent   fill:#dbeafe,stroke:#1d4ed8,color:#172554
    classDef orch    fill:#cffafe,stroke:#0e7490,color:#083344
    classDef core    fill:#fef3c7,stroke:#a16207,color:#451a03
    classDef tollg   fill:#fed7aa,stroke:#c2410c,color:#431407
    classDef conn    fill:#e9d5ff,stroke:#7e22ce,color:#3b0764
    classDef support fill:#fce7f3,stroke:#be185d,color:#500724
    classDef deploy  fill:#dcfce7,stroke:#15803d,color:#052e16

    class EXT,EMAIL,BEDROCK,PEGA,DDOG,S3 ext
    class AGENTS,MANIFEST,POLICY,AGENT_CODE,SCHEMAS agent
    class ORCH,LG,AC,DIRECT,GCM orch
    class CORE,CORE_PRIMITIVES,CORE_GOV,BA,EFFECTS,BUILDER,MEM,TOOLS,GW,REDACT,TRACK,TEL,LIFE,REG core
    class TOLLGATE,TOWER,POL_EVAL,AUDIT_PIPE,APPROVERS,STORES tollg
    class CONN,BL,LL,BKB,OUT,PCASE,PKB,SN conn
    class SUPPORT,HARNESS,RUNTIME,EVAL,CLI,PLATFORM support
    class DEPLOY,CDK_ARC,CDK_BEDROCK,CDK_DD,DOCKERFILE,DASHBOARDS deploy
```

---

## Component responsibilities — by zone

### 🌐 External systems

| Component | Responsibility |
|---|---|
| **Email system** | Outlook / Gmail / Exchange — inbound message source |
| **Bedrock / LiteLLM** | Foundation model APIs — third-party LLM providers |
| **Pega** | System of record for ITSM cases (the destination of work) |
| **Datadog** | Operational observability surface for humans |
| **S3 + KMS** | Compliance-grade audit storage, 7-year retention |

### 🧩 Agent layer (what teams write)

| Component | Responsibility |
|---|---|
| **manifest.yaml** | Declared scope: agent_id, version, allowed_effects, SLOs, stage |
| **policy.yaml** | Per-agent decision rules — ALLOW / ASK / DENY by `resource_type` |
| **agent.py + graph.py** | Business logic; for email-triage, 9 LangGraph nodes |
| **pega_schemas/*.yaml** | Per-case-type field map (distribution, hardship, sponsor) |

### 🔀 Orchestration (interchangeable runtimes)

| Component | Responsibility |
|---|---|
| **LangGraphOrchestrator** | In-process LangGraph executor (default for dev + sandbox) |
| **AgentCoreOrchestrator** | Forward to AWS Bedrock AgentCore runtime |
| **DirectOrchestrator** | Sequential `run_effect` calls; no graph |
| **governed_chat_model** | Wraps any LangChain `BaseChatModel` so `ainvoke` routes through `run_effect` |

### ⚙️ arc.core (governance contract)

| Component | Responsibility |
|---|---|
| **BaseAgent.run_effect()** | The single entry point for every governed action |
| **Effects taxonomy** | 5 domain enums × 6 tiers: `FinancialEffect`, `ITSMEffect`, `HealthcareEffect`, `LegalEffect`, `ComplianceEffect` |
| **EffectRequestBuilder** | Builds typed `ToolRequest` + `Intent` for ControlTower |
| **Memory** | `ConversationBuffer` + `AgentMemoryStore` (Local JSON / DynamoDB backends) |
| **AgentToolRegistry** | `@governed_tool` decorator + tool registration |
| **Gateway** | Declared data sources: `Mock` / `Http` / `Multi` |
| **Redactor** | PII patterns (SSN, card, email, phone, ABA) — applied at LLM + audit boundaries |
| **OutcomeTracker** | JSONL outcome events for ROI + SLO window stats |
| **Telemetry** | NoOp / CloudWatch EMF / Datadog DogStatsD emitters |
| **Lifecycle** | 6-stage promotion + DemotionWatcher (auto-demotion on SLO breach) |
| **ManifestStore** | Per-agent YAML registry; source of truth for scope + status |

### 🛡️ tollgate (sibling package — trust boundary)

| Component | Responsibility |
|---|---|
| **ControlTower** | Orchestrates every gate: policy → audit → approver |
| **YamlPolicyEvaluator** | Layered, compliance-readable rule evaluation |
| **Audit pipeline** | `JsonlAuditSink`, `S3AuditSink`, `CompositeAuditSink`, `WebhookAuditSink`; wrappable with `RedactingAuditSink` |
| **Approvers** | `AutoApprover`, `CliApprover`, `AsyncQueueApprover` (SQS + DynamoDB) |
| **Stores** | Grants, approvals, rate limiter, circuit breaker (in-memory + persistent backends) |

### 🔌 Connectors (typed external clients)

| Component | Responsibility |
|---|---|
| **BedrockLLMClient** | Bedrock invocation with redactor injection |
| **LiteLLMClient** | Cross-provider LLM (OpenAI, Anthropic, Bedrock, Vertex) |
| **BedrockKB / BedrockGuardrails** | RAG knowledge base + Bedrock Guardrails |
| **OutlookConnector** | Email inbound source |
| **PegaCaseConnector** | Creates ITSM cases via Pega Case API |
| **PegaKnowledgeConnector** | Pega Knowledge Buddy RAG lookups |
| **ServiceNowConnector** | Alternative ITSM destination |

### 🏗️ Platform support (cross-cutting)

| Component | Responsibility |
|---|---|
| **arc-harness** (`HarnessBuilder`) | Wires manifest + policy + tower + fixtures for sandbox / eval |
| **arc-runtime** | Lambda handler, Secrets Manager, Bedrock action group schema generator |
| **arc-eval** | Replay + scoring against golden fixtures |
| **arc-cli** | Operator commands — lifecycle, manifest, watch |
| **arc-platform** | FastAPI backend + React ops console (suspend, resume, feedback capture) |

### ☁️ deploy (infra-as-code)

| Component | Responsibility |
|---|---|
| **ArcAgentStack** | Per-agent CDK: Lambda + DynamoDB + SQS + S3 + KMS + IAM + CloudWatch |
| **BedrockAgentStack** | Bedrock Agent + Action Group + alias |
| **DatadogForwarderConstruct** | CloudWatch Logs → Datadog Lambda forwarder |
| **Dockerfile** | Multi-stage container build |
| **Datadog dashboards/** | Three importable JSONs: live ops, quality, PII heatmap |

---

## The 20-step email → Pega data flow

What the numbered arrows in the diagram represent:

| # | Step | Component(s) involved |
|---|---|---|
| ① | Inbound email arrives | Email system → Outlook connector |
| ② | Agent fetches via declared data source | Gateway (`email.inbox`) |
| ③ | Email passed into LangGraph graph | `agent.execute()` |
| ④ | First node calls `run_effect(EMAIL_CLASSIFY)` | BaseAgent |
| ⑤ | run_effect routes to ControlTower | tollgate |
| ⑥ | Policy evaluator returns ALLOW | YamlPolicyEvaluator |
| ⑦ | Node makes governed LLM call | governed_chat_model |
| ⑧ | Prompt redacted before leaving boundary | Redactor |
| ⑨ | Bedrock invocation prepared | BedrockLLMClient |
| ⑩ | Tokens flow over API | Bedrock |
| ⑪ | Response returned to agent | BedrockLLMClient |
| ⑫ | 7 more nodes fire (entities, user, KB, dup, draft, ...) | run_effect × 7 |
| ⑬ | Final node: `run_effect(TICKET_CREATE)` | BaseAgent |
| ⑭ | P1/P2 priority → ASK gate | Approver path |
| ⑮ | Human approves; flow resumes | AsyncQueueApprover |
| ⑯ | Pega case creation requested | PegaCaseConnector |
| ⑰ | Pega Case API call | Pega |
| ⑱ | Outcome logged | run_effect(TRIAGE_LOG_WRITE) |
| ⑲ | Every effect's audit row → S3 | RedactingAuditSink → S3AuditSink |
| ⑳ | Telemetry to CloudWatch + Datadog | CloudWatch EMF + DogStatsD |

The entire flow is governed: **8 effects fire per email**, each one
gated by policy, recorded in audit, and emitted as telemetry.

---

## How the harness layer composes (reading the diagram)

Three composition arrows tell the story:

```
AGENTS ─builds→ ORCH ─runs→ CORE ─every effect through→ TOLLGATE
```

What this means in code (`arc-harness/HarnessBuilder`):

```python
HarnessBuilder(manifest=MANIFEST, policy=POLICY)
    .with_fixtures(FIXTURES)
    .with_tracker("outcomes.jsonl")
    .build(EmailTriageAgent)
```

The builder:

1. Loads `manifest.yaml` + `policy.yaml` from disk
2. Constructs `ControlTower` with `YamlPolicyEvaluator(policy)` +
   `JsonlAuditSink` + `AutoApprover`
3. Wires a `MockGatewayConnector` from `fixtures.yaml`
4. Constructs `OutcomeTracker` writing to the given path
5. Optionally wires `Telemetry` (CloudWatch EMF / Datadog) from env
6. Instantiates the agent class with all of the above injected

**Same builder works for production** — swap the audit sink
(`S3AuditSink`), the approver (`AsyncQueueApprover` with SQS + Dynamo),
the orchestrator (`AgentCoreOrchestrator`), and the manifest stays
unchanged. **That's the portability claim.**

---

## What this diagram is NOT showing (to keep it readable)

- **Internal field-by-field structure** of `AuditEvent`, `Decision`,
  `ToolRequest`, etc. (see `tollgate/types.py`)
- **The 7 reference agents** beyond email-triage (fiduciary-watchdog,
  retirement-trajectory, plan-design-optimizer, life-event-anticipation,
  contract-review, care-coordinator)
- **Runtime kill-switch detail** (manifest status, ENV variable,
  watcher kill switch)
- **Promotion pipeline gates** (`GateChecker`, `GateCheck` types,
  evidence checks)

Each of those has its own diagram in
[`docs/architecture-diagrams.md`](architecture-diagrams.md). This map
is for the "what's in the codebase and how does an email become a
Pega case" question that comes up in stakeholder conversations.
