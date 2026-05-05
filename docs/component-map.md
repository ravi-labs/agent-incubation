# Arc — component map

One high-level diagram of the arc platform, then a drill-down per
major block. The high-level reads in 30 seconds; the drill-downs are
there when someone asks "what's in that box?"

> Every diagram below uses only `-->` and `-.->` arrows so it renders
> cleanly on GitHub, Notion, Confluence, and mermaid.live.

---

## High-level — the 30-second view

Six blocks. Solid arrows = composition (how the platform is built).
Dotted arrows = data flow at runtime (email in, Pega + audit out).

```mermaid
flowchart TB
    EMAIL["📧 Email system"]
    LLM["🤖 LLM provider<br/>Bedrock · LiteLLM"]
    PEGA["🗄️ Pega<br/>system of record"]

    subgraph PLATFORM["Arc Platform"]
        direction TB
        AGENT["🧩 Agent layer<br/>manifest + policy + business logic"]
        ORCH["🔀 Orchestration<br/>LangGraph · AgentCore · Direct"]
        CORE["⚙️ arc.core<br/>BaseAgent · run_effect · effects · memory"]
        GOV["🛡️ Governance (tollgate)<br/>policy · audit · approver"]
        CONN["🔌 Connectors<br/>LLM · Pega · email · ServiceNow"]

        AGENT --> ORCH
        ORCH --> CORE
        CORE --> GOV
        CORE --> CONN
    end

    OBS["📊 Observability<br/>S3 audit · CloudWatch · Datadog"]

    EMAIL -.->|inbound| AGENT
    CONN -.->|invokes| LLM
    CONN -.->|creates case| PEGA
    GOV -.->|redacted rows| OBS
    CORE -.->|metrics| OBS

    classDef ext fill:#e0e7ff,stroke:#3730a3,color:#1e1b4b
    classDef plt fill:#fef3c7,stroke:#a16207,color:#451a03
    classDef obs fill:#dcfce7,stroke:#15803d,color:#052e16

    class EMAIL,LLM,PEGA ext
    class AGENT,ORCH,CORE,GOV,CONN plt
    class OBS obs
```

| Block | What it does | One-line summary |
|---|---|---|
| 🧩 **Agent layer** | What teams write | manifest.yaml + policy.yaml + agent.py + graph.py |
| 🔀 **Orchestration** | Runs the graph | LangGraph in-process today; AgentCore for AWS deployment |
| ⚙️ **arc.core** | Governance contract | `BaseAgent.run_effect()` is the only entry point for any action |
| 🛡️ **Governance (tollgate)** | Policy + audit + approve | Every effect goes through here; non-bypassable |
| 🔌 **Connectors** | Talks to external systems | Bedrock, LiteLLM, Pega, Outlook, ServiceNow |
| 📊 **Observability** | Where humans look | S3 (audit) + CloudWatch (substrate) + Datadog (dashboards) |

---

## 🧩 Agent layer — drill-down

What an agent team writes for one agent. Three files; everything else is inherited from arc.

```mermaid
flowchart LR
    MAN["manifest.yaml<br/>agent_id · version<br/>allowed_effects<br/>lifecycle_stage · SLOs"]
    POL["policy.yaml<br/>rules:<br/>resource_type → ALLOW/ASK/DENY"]
    AGENT_CODE["agent.py<br/>extends BaseAgent"]
    GRAPH["graph.py<br/>LangGraph nodes<br/>(9 for email-triage)"]
    SCHEMAS["pega_schemas/*.yaml<br/>per-case-type field maps"]

    MAN -.-> AGENT_CODE
    POL -.-> AGENT_CODE
    AGENT_CODE --> GRAPH
    GRAPH -.-> SCHEMAS
```

| File | Responsibility |
|---|---|
| **manifest.yaml** | Declared scope: `agent_id`, `version`, `allowed_effects`, `lifecycle_stage`, SLOs, owner |
| **policy.yaml** | Per-agent decision rules — `ALLOW` / `ASK` / `DENY` keyed on `resource_type` |
| **agent.py** | Business class extending `BaseAgent`. Implements `execute()` |
| **graph.py** | LangGraph node definitions; 9 nodes for email-triage |
| **pega_schemas/*.yaml** | Per-case-type Pega field maps (distribution, hardship, sponsor) |

---

## 🔀 Orchestration — drill-down

Interchangeable runtimes. All implement the same `OrchestratorProtocol`, so swapping one for another doesn't touch governance code.

```mermaid
flowchart LR
    PROTO["OrchestratorProtocol<br/>run() · stream() · resume()"]

    LG["LangGraphOrchestrator<br/>in-process graph executor<br/>default for dev + sandbox"]
    AC["AgentCoreOrchestrator<br/>AWS Bedrock AgentCore<br/>production substrate"]
    DIRECT["DirectOrchestrator<br/>sequential run_effect<br/>no graph"]
    GCM["governed_chat_model<br/>LangChain wrapper:<br/>routes ainvoke through run_effect"]

    PROTO -.->|implemented by| LG
    PROTO -.->|implemented by| AC
    PROTO -.->|implemented by| DIRECT
    GCM -.->|wraps| LG
```

| Component | Responsibility |
|---|---|
| **LangGraphOrchestrator** | Runs the LangGraph in-process; default for dev + sandbox |
| **AgentCoreOrchestrator** | Forwards to AWS Bedrock AgentCore runtime |
| **DirectOrchestrator** | Sequential `run_effect` calls; no graph; for simple agents |
| **governed_chat_model** | Wraps any LangChain `BaseChatModel` so `ainvoke` routes through `run_effect` |

---

## ⚙️ arc.core — drill-down

The governance contract every agent inherits. Shown as inputs (manifest, declarations) → engine (`BaseAgent.run_effect`) → outputs (governance, observability).

```mermaid
flowchart LR
    BA["BaseAgent.run_effect<br/>single entry point<br/>for every governed action"]

    EFFECTS["Effects taxonomy<br/>5 domain enums<br/>6 tiers"]
    BUILDER["EffectRequestBuilder<br/>typed ToolRequest + Intent"]
    MEM["Memory<br/>ConversationBuffer<br/>AgentMemoryStore"]
    TOOLS["AgentToolRegistry<br/>governed_tool"]
    GW["Gateway<br/>declared data sources"]

    REDACT["Redactor<br/>PII patterns:<br/>SSN · card · email · phone"]
    TRACK["OutcomeTracker<br/>JSONL events<br/>+ SLO window stats"]
    TEL["Telemetry<br/>NoOp · CloudWatch · Datadog"]
    LIFE["Lifecycle<br/>6 stages + DemotionWatcher"]
    REG["ManifestStore<br/>per-agent YAML registry"]

    BA --> EFFECTS
    BA --> BUILDER
    BA -.-> MEM
    BA -.-> TOOLS
    BA -.-> GW
    BA -.-> REDACT
    BA -.-> TRACK
    BA -.-> TEL
    LIFE -.-> TRACK
    LIFE -.-> REG
```

| Component | Responsibility |
|---|---|
| **BaseAgent.run_effect()** | The only path an agent can take to execute any action |
| **Effects taxonomy** | `FinancialEffect`, `ITSMEffect`, `HealthcareEffect`, `LegalEffect`, `ComplianceEffect` × 6 tiers |
| **EffectRequestBuilder** | Builds typed `ToolRequest` + `Intent` for ControlTower |
| **Memory** | `ConversationBuffer` + `AgentMemoryStore` (Local JSON or DynamoDB) |
| **AgentToolRegistry** | `@governed_tool` decorator + tool registration |
| **Gateway** | Declared data sources: `MockGatewayConnector`, `HttpGateway`, `MultiGateway` |
| **Redactor** | Pattern-based PII redaction at LLM + audit boundaries |
| **OutcomeTracker** | Records outcome events for ROI + SLO window stats |
| **Telemetry** | Three implementations: `NoOpTelemetry`, `CloudWatchEMFTelemetry`, `DatadogTelemetry` |
| **Lifecycle** | 6-stage promotion pipeline + `DemotionWatcher` (auto-demotion on SLO breach) |
| **ManifestStore** | Per-agent YAML registry; source of truth for scope + status |

---

## 🛡️ Governance (tollgate) — drill-down

The trust boundary. Lives in a separate sibling package — easier to audit on its own. Every `run_effect` call routes through `ControlTower`, which fans out to four pieces.

```mermaid
flowchart LR
    TOWER["ControlTower<br/>orchestrates every gate"]

    POLICY["YamlPolicyEvaluator<br/>layered rule evaluation<br/>compliance-readable YAML"]

    AUDIT["Audit pipeline<br/>JsonlAuditSink<br/>S3AuditSink<br/>RedactingAuditSink wrapper"]

    APPROVE["Approvers<br/>AutoApprover<br/>CliApprover<br/>AsyncQueueApprover<br/>(SQS + DynamoDB)"]

    STORES["Stores<br/>Grants · Approvals<br/>RateLimiter · CircuitBreaker"]

    TOWER --> POLICY
    TOWER --> AUDIT
    TOWER --> APPROVE
    TOWER --> STORES
```

| Component | Responsibility |
|---|---|
| **ControlTower** | Orchestrates every gate: policy → audit → approver → telemetry |
| **YamlPolicyEvaluator** | Reads `policy.yaml` files; layered evaluation; compliance-readable rules |
| **Audit pipeline** | `JsonlAuditSink`, `S3AuditSink`, `WebhookAuditSink`, composable; `RedactingAuditSink` wraps any sink to redact PII |
| **Approvers** | `AutoApprover` (sandbox), `CliApprover` (dev), `AsyncQueueApprover` (production via SQS + DynamoDB) |
| **Stores** | Grants, approvals, rate limiter, circuit breaker — in-memory + persistent backends (SQLite, Redis, DynamoDB) |

---

## 🔌 Connectors — drill-down

Typed clients for external systems. Three families — LLM, ITSM, Email — plus mocks.

```mermaid
flowchart LR
    subgraph LLM_GROUP["LLM"]
        BL["BedrockLLMClient"]
        LL["LiteLLMClient<br/>cross-provider"]
        BKB["BedrockKB"]
        BG["BedrockGuardrails"]
    end

    subgraph ITSM_GROUP["ITSM destinations"]
        PCASE["PegaCaseConnector<br/>creates cases"]
        PKB["PegaKnowledgeConnector<br/>RAG lookup"]
        SN["ServiceNowConnector"]
    end

    subgraph EMAIL_GROUP["Email source"]
        OUT["OutlookConnector"]
    end

    MOCK["MockConnector<br/>for tests"]
```

| Component | Responsibility |
|---|---|
| **BedrockLLMClient** | AWS Bedrock invocation with `Redactor` injection |
| **LiteLLMClient** | Cross-provider (OpenAI, Anthropic, Bedrock, Vertex) |
| **BedrockKB / BedrockGuardrails** | RAG knowledge base + Bedrock Guardrails |
| **PegaCaseConnector** | Creates ITSM cases via Pega Case API |
| **PegaKnowledgeConnector** | Pega Knowledge Buddy RAG lookups |
| **ServiceNowConnector** | Alternative ITSM destination |
| **OutlookConnector** | Email inbound source |
| **MockConnector** | Fixture-driven; for sandbox + tests |

---

## 📊 Observability — drill-down

Three audiences, three retention models, kept deliberately separate. Audit ≠ telemetry.

```mermaid
flowchart LR
    EFFECT["Every run_effect call"]

    AUDIT_PIPE["Audit pipeline<br/>(redacted)"]
    TEL_OUT["Telemetry emit<br/>EMF · DogStatsD"]
    TRACKER["OutcomeTracker"]

    S3["S3 audit log<br/>compliance · 7y · KMS"]
    CW["CloudWatch logs + metrics<br/>AWS substrate · 30d"]
    DD["Datadog<br/>3 dashboards:<br/>live ops · quality · PII heatmap"]

    SLO["SLO evaluator"]
    DEMOTE["DemotionWatcher<br/>auto-demote on breach"]

    EFFECT --> AUDIT_PIPE
    EFFECT --> TEL_OUT
    EFFECT --> TRACKER
    AUDIT_PIPE --> S3
    TEL_OUT --> CW
    TEL_OUT --> DD
    CW -.->|forwarder Lambda| DD
    TRACKER --> SLO
    SLO --> DEMOTE
```

| Component | Audience | Retention | Why separate |
|---|---|---|---|
| **S3 audit log** | Compliance auditors | Years | KMS-encrypted, immutable, queryable from Athena |
| **CloudWatch** | AWS substrate | 30d hot, then expire | Always-on, free with AWS |
| **Datadog dashboards** | On-call + ops + compliance | 15d hot | Where humans look during incidents |
| **OutcomeTracker** | Lifecycle pipeline | Used per-window | Feeds SLO eval; not a human surface |
| **DemotionWatcher** | Lifecycle automation | — | Auto-demotes agents when SLO breached |

---

## End-to-end flow — email to Pega

The 8 effects that fire when one email is triaged. Every box numbered is a `run_effect()` call that goes through ControlTower → policy + audit + telemetry.

```mermaid
flowchart TB
    INBOUND["📧 Email arrives<br/>Outlook connector"]

    N1["1️⃣ classify_node<br/>EMAIL_CLASSIFY + PRIORITY_INFER + SENTIMENT_SCORE"]
    N2["2️⃣ extract_entities_node<br/>ENTITY_EXTRACT"]
    N3["3️⃣ lookup_user_node<br/>USER_DIRECTORY_READ"]
    N4["4️⃣ query_knowledge_node<br/>KNOWLEDGE_BUDDY_QUERY"]
    N5["5️⃣ check_duplicate_node<br/>DUPLICATE_DETECT"]
    N6["6️⃣ draft_ticket_node<br/>TICKET_DRAFT"]
    N7["7️⃣ create_ticket_node<br/>TICKET_CREATE — ASK gate for P1/P2"]
    N8["8️⃣ log_triage_node<br/>TRIAGE_LOG_WRITE"]

    PEGA_OUT["🗄️ Pega case created"]
    AUDIT_OUT["📦 8 audit rows in S3<br/>(redacted)"]
    METRICS_OUT["📊 Telemetry to Datadog<br/>arc.effect.outcome · arc.llm.tokens_in · arc.redaction.match"]

    INBOUND --> N1
    N1 --> N2
    N2 --> N3
    N3 --> N4
    N4 --> N5
    N5 --> N6
    N6 --> N7
    N7 --> N8

    N7 -.-> PEGA_OUT
    N1 -.-> AUDIT_OUT
    N8 -.-> AUDIT_OUT
    N1 -.-> METRICS_OUT
    N8 -.-> METRICS_OUT
```

**8 effects per email**, each gated by policy, recorded in audit, emitted as telemetry. The ASK gate at `create_ticket` is the explicit human-in-the-loop point for P1/P2 priority emails.

---

## How the harness layer composes

`HarnessBuilder` is the assembler — it takes the agent's manifest + policy + fixtures and wires everything below `BaseAgent`:

```python
HarnessBuilder(manifest=MANIFEST, policy=POLICY)
    .with_fixtures(FIXTURES)
    .with_tracker("outcomes.jsonl")
    .build(EmailTriageAgent)
```

What that single call wires:

1. Loads `manifest.yaml` + `policy.yaml`
2. Constructs `ControlTower` with `YamlPolicyEvaluator` + audit sink + approver
3. Wires a `MockGatewayConnector` from the fixtures
4. Constructs `OutcomeTracker` writing to the given path
5. Optionally wires `Telemetry` from environment
6. Instantiates the agent class with all of the above injected

**Same builder works for production** — swap in `S3AuditSink`, `AsyncQueueApprover` (SQS + DynamoDB), `AgentCoreOrchestrator` — manifest + policy + business code stay unchanged. **That's the portability claim, made concrete.**

---

## Where to read next

- [`docs/architecture-diagrams.md`](architecture-diagrams.md) — eight detailed diagrams (run-effect sequence, layered governance, lifecycle pipeline, etc.) for engineers building on arc
- [`docs/architecture-overview.md`](architecture-overview.md) — alternative high-level view focused on the email-triage flow specifically
- [`docs/concepts/`](concepts/) — per-topic deep dives: governance, effects, lifecycle, telemetry, data redaction, LLM clients, feedback
