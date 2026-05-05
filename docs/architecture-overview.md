# Arc — architecture overview

Two diagrams for sharing with stakeholders. The first shows the
platform shape; the second shows it in action with a real agent.

> Both diagrams are Mermaid — they render natively in GitHub,
> Notion, Confluence, and any Markdown tool. For PowerPoint, paste
> into [mermaid.live](https://mermaid.live) and export PNG/SVG.

---

## 1. Platform architecture (high-level)

What every arc agent inherits and where each component lives. Built
left-to-right: agent code on the left, the governance core in the
middle, observability + storage on the right.

```mermaid
flowchart LR
    %% ── Agent code (what teams write) ─────────────────────────────
    subgraph AGENT["📝 Agent code (what teams write)"]
        direction TB
        MAN["manifest.yaml<br/><i>scope, effects, SLOs</i>"]
        POL["policy.yaml<br/><i>ALLOW / ASK / DENY rules</i>"]
        CODE["agent.py + graph.py<br/><i>business logic</i>"]
    end

    %% ── Arc core (governance contract) ────────────────────────────
    subgraph CORE["⚙️ arc.core (governance contract)"]
        direction TB
        BA["BaseAgent.run_effect()<br/><b>single entry point for every action</b>"]
        TOWER["ControlTower (tollgate)"]
        SCOPE["Manifest scope check"]
        POLICY["YamlPolicyEvaluator"]
        AUDIT["Audit pipeline<br/>+ Redactor (PII)"]
        APPROVER["Approver<br/>Auto · Cli · AsyncQueue"]
        TEL["Telemetry<br/>NoOp · CloudWatch · Datadog"]
        BA --> TOWER
        TOWER --> SCOPE
        TOWER --> POLICY
        TOWER --> AUDIT
        TOWER --> APPROVER
        TOWER --> TEL
    end

    %% ── Orchestrators (interchangeable) ───────────────────────────
    subgraph ORCH["🔀 Orchestrators (interchangeable)"]
        direction LR
        LG["LangGraph"]
        AC["AgentCore"]
        DIRECT["Direct"]
    end

    %% ── Connectors (data + LLM) ───────────────────────────────────
    subgraph CONN["🔌 Connectors"]
        direction LR
        BEDROCK["Bedrock LLM"]
        LITE["LiteLLM"]
        PEGA["Pega / ServiceNow"]
        GW["Gateway (data sources)"]
    end

    %% ── Observability + storage (compliance + ops) ────────────────
    subgraph OBS["📊 Observability + storage"]
        direction TB
        S3["S3 audit log<br/><i>compliance · years</i>"]
        CW["CloudWatch logs + metrics<br/><i>AWS substrate · 30d</i>"]
        DD["Datadog dashboards<br/><i>3× audiences · live, quality, PII heatmap</i>"]
        SQS["SQS + DynamoDB<br/><i>human approval queue</i>"]
        OUT["Outcome tracker → SLO eval<br/>→ DemotionWatcher"]
    end

    %% ── Lifecycle (governance over time) ──────────────────────────
    subgraph LIFE["🔄 Lifecycle"]
        direction LR
        L1["DISCOVER"] --> L2["SHAPE"]
        L2 --> L3["BUILD"]
        L3 --> L4["VALIDATE"]
        L4 --> L5["GOVERN"]
        L5 --> L6["SCALE"]
    end

    %% ── Frontend ──────────────────────────────────────────────────
    UI["🖥️ Live ops console<br/><i>React · polled · suspend / resume</i>"]
    API["FastAPI backend"]

    %% ── Wiring ────────────────────────────────────────────────────
    AGENT  --> CORE
    CORE   --> ORCH
    ORCH   --> CONN

    AUDIT     -.->|redacted| S3
    AUDIT     -.->|redacted| CW
    TEL       -.->|EMF| CW
    TEL       -.->|DogStatsD| DD
    CW        -.->|forwarder Lambda| DD
    APPROVER  -.-> SQS
    BA        -.-> OUT
    OUT       -.-> LIFE

    UI    --> API
    API   -.-> S3
    API   -.-> OUT

    classDef agent fill:#dbeafe,stroke:#1e40af,color:#1e3a8a
    classDef core  fill:#fef3c7,stroke:#a16207,color:#713f12
    classDef obs   fill:#dcfce7,stroke:#15803d,color:#14532d
    classDef life  fill:#fae8ff,stroke:#a21caf,color:#86198f

    class AGENT,MAN,POL,CODE agent
    class CORE,BA,TOWER,SCOPE,POLICY,AUDIT,APPROVER,TEL core
    class OBS,S3,CW,DD,SQS,OUT obs
    class LIFE,L1,L2,L3,L4,L5,L6 life
```

### Reading the diagram

- **Blue (left):** what an agent team writes. A YAML manifest, a YAML
  policy, the business code. Three files, no platform code.
- **Yellow (centre):** the governance contract. Every action goes
  through `BaseAgent.run_effect()` → `ControlTower`. Manifest scope,
  policy, audit, approval, telemetry — all wired here, all
  non-bypassable.
- **Grey (orchestrators + connectors):** interchangeable substrates.
  Swap LangGraph → AgentCore without touching governance. Swap
  Bedrock → LiteLLM without touching audit shape.
- **Green (right):** where humans look. S3 for compliance auditors
  (years), CloudWatch as the AWS substrate (days), Datadog dashboards
  for ops + compliance leads. Every signal here is derived from the
  governance core; nothing is bolted on.
- **Purple (bottom):** lifecycle. Six stages with promotion gates,
  SLO-driven auto-demotion, manifest store. Governance over time, not
  just per-call.

### What's NOT in the picture (honest gaps)

- **Multi-tenancy** — single-tenant today
- **Identity for agents** — manifest-declared, not cryptographically
  signed
- **In-flight cancel** — backlog item; today's interrupts are ASK
  gates + suspend kill switch
- **Streaming under AgentCore** — adapter scaffold present, streaming
  TODO

---

## 2. Email triage flow (real agent in motion)

The `email-triage` agent processes one inbound email through nine
governed nodes. Every node fires through `run_effect`, so every action
appears in the audit log + telemetry stream.

```mermaid
flowchart TD
    EMAIL["📧 Inbound email"]

    subgraph CLF["1️⃣ Classify"]
        N1["classify_node"]
        E1{"effect:<br/>ITSMEffect.EMAIL_CLASSIFY<br/>+ PRIORITY_INFER<br/>+ SENTIMENT_SCORE"}
        N1 --> E1
    end

    subgraph EXT["2️⃣ Extract entities"]
        N2["extract_entities_node"]
        E2{"effect:<br/>ITSMEffect.ENTITY_EXTRACT"}
        N2 --> E2
    end

    subgraph LOOK["3️⃣ Lookup user"]
        N3["lookup_user_node"]
        E3{"effect:<br/>ITSMEffect.USER_DIRECTORY_READ"}
        N3 --> E3
    end

    subgraph KB["4️⃣ Query knowledge base"]
        N4["query_knowledge_node"]
        E4{"effect:<br/>ITSMEffect.KNOWLEDGE_BUDDY_QUERY"}
        N4 --> E4
    end

    subgraph DUP["5️⃣ Check duplicate"]
        N5["check_duplicate_node"]
        E5{"effect:<br/>ITSMEffect.DUPLICATE_DETECT"}
        N5 --> E5
    end

    subgraph DRAFT["6️⃣ Draft ticket"]
        N6["draft_ticket_node"]
        E6{"effect:<br/>ITSMEffect.TICKET_DRAFT<br/>(uses Pega schema registry<br/>per case type)"}
        N6 --> E6
    end

    GATE{{"Priority?"}}

    subgraph CRT["7️⃣ Create ticket"]
        N7["create_ticket_node"]
        E7{"effect:<br/>ITSMEffect.TICKET_CREATE"}
        N7 --> E7
    end

    ASK["⏸️ ASK gate<br/>P1/P2 → human review"]
    ALLOW["✅ ALLOW<br/>P3/P4 + confidence ≥ 0.85"]

    subgraph LOG["8️⃣ Log triage"]
        N8["log_triage_node"]
        E8{"effect:<br/>ITSMEffect.TRIAGE_LOG_WRITE"}
        N8 --> E8
    end

    DONE["✅ Outcome recorded<br/>→ OutcomeTracker"]

    %% ── Side rails: governance + observability ────────────────────
    POLICY["policy.yaml"]
    AUDIT["S3 audit log<br/>(redacted)"]
    DDOG["Datadog dashboards<br/>arc.effect.outcome<br/>arc.redaction.match<br/>arc.llm.tokens_in"]

    EMAIL --> N1
    E1 --> N2
    E2 --> N3
    E3 --> N4
    E4 --> N5
    E5 --> N6
    E6 --> GATE
    GATE -->|P1/P2| ASK
    GATE -->|P3/P4| ALLOW
    ASK --> N7
    ALLOW --> N7
    E7 --> N8
    E8 --> DONE

    %% Each effect routes through ControlTower
    E1 -.->|ControlTower| POLICY
    E2 -.->|ControlTower| POLICY
    E3 -.->|ControlTower| POLICY
    E4 -.->|ControlTower| POLICY
    E5 -.->|ControlTower| POLICY
    E6 -.->|ControlTower| POLICY
    E7 -.->|ControlTower| POLICY
    E8 -.->|ControlTower| POLICY

    POLICY -.->|writes| AUDIT
    POLICY -.->|emits| DDOG

    classDef effect fill:#fef3c7,stroke:#a16207,color:#713f12
    classDef gate   fill:#fecaca,stroke:#b91c1c,color:#7f1d1d
    classDef obs    fill:#dcfce7,stroke:#15803d,color:#14532d

    class E1,E2,E3,E4,E5,E6,E7,E8 effect
    class GATE,ASK gate
    class POLICY,AUDIT,DDOG obs
```

### What this flow proves

- **Eight effects per email** — every meaningful action is governed.
  None of them can run without `policy.yaml` saying it can.
- **PII redacted at the boundary** — the `Redactor` runs before any
  LLM call (Bedrock / LiteLLM) and before audit-row write. The agent
  sees real values; the LLM provider and audit storage see redacted.
- **Human-in-the-loop where it matters** — P1/P2 priority emails hit
  the `TICKET_CREATE` ASK gate. P3/P4 with high confidence go
  straight through. Same code path, policy decides.
- **Cost-tracked end-to-end** — `arc.llm.tokens_in` per effect lets
  you compute "cost per email triaged" and break it down by
  classification model vs. drafting model.
- **One audit row per effect** — eight rows per email in S3.
  Replayable, queryable from Athena, redacted of PII.
- **Three telemetry signals visible to ops** — decision distribution,
  effect latency, redaction match counts. All in Datadog within ~2
  minutes of each run.

### What changes if you run this on AgentCore instead

**Almost nothing.** The orchestrator routes the LangGraph graph to
Bedrock AgentCore's runtime instead of in-process, and that's it.
Same nine nodes, same eight effects, same policy file, same audit
shape, same telemetry vocabulary.

The portability demo (`demos/agentcore-portability/`) proves this
end-to-end with a minimal agent. The architectural claim holds for
email-triage too because every arc agent inherits the same governance
contract.
