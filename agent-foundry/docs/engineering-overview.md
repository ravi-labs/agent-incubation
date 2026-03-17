# Agent Foundry — Engineering Overview

> Enterprise agent incubation platform: from idea to governed production agent.

---

## What Is Agent Foundry?

Agent Foundry is a platform for incubating, governing, and deploying AI agents in regulated financial services environments. It provides the infrastructure layer every agent team would otherwise build from scratch: policy enforcement, audit logging, human review workflows, lifecycle management, and AWS deployment primitives.

Agents built on Foundry inherit the full governance stack from the first line of code. Governance is not bolted on at the end — it is the foundation.

---

## Repository Layout

```
agent-foundry/
├── src/foundry/
│   ├── scaffold/           # BaseAgent, AgentManifest, lifecycle management
│   ├── policy/             # FinancialEffect taxonomy, effect registry, policy builder
│   ├── tollgate/           # ControlTower policy engine, audit logging, ALLOW/ASK/DENY
│   ├── gateway/            # Data access layer: MockGateway, HttpGateway, MultiGateway
│   ├── memory/             # ConversationBuffer (short-term), FoundryMemoryStore (long-term)
│   ├── tools/              # @governed_tool decorator, ToolRegistry
│   ├── eval/               # FoundryEvaluator, EvalScenario, CI integration
│   ├── observability/      # OutcomeTracker, ROI measurement
│   ├── integrations/       # LangChain, LangGraph, Bedrock Agent, Bedrock Guardrails
│   ├── deploy/             # Lambda handler, streaming handler, CDK constructs
│   └── cli/                # foundry CLI (scaffold, promote, eval)
├── examples/
│   └── fiduciary_watchdog/ # Full reference implementation
├── deploy/
│   └── cdk/                # BedrockAgentStack CDK construct
├── docs/                   # This documentation
└── tests/
```

---

## Architecture: Three Layers

| Layer | Purpose | Key Components |
|-------|---------|----------------|
| **Scaffold** | Agent identity, lifecycle, and the `execute()` contract | `BaseAgent`, `AgentManifest`, `LifecycleStage` |
| **Policy** | What agents are allowed to do and at what cost | `FinancialEffect`, `ControlTower`, policy YAML, `TollgateDeferred` |
| **Integration** | How agents connect to data, LLMs, and AWS services | Gateway connectors, Memory, LangGraph, Bedrock, Lambda |

Every agent action flows through all three layers in order. No shortcuts.

---

## The Effect Taxonomy

All agent actions are modelled as `FinancialEffect` values arranged in a 6-tier risk hierarchy. The tier determines the default policy decision (ALLOW / ASK / DENY).

| Tier | Category | Default | Examples |
|------|----------|---------|---------|
| 1 | Data Access | ALLOW | `data.participant.read`, `knowledge.base.retrieve` |
| 2 | Compute & Analysis | ALLOW | `risk.score.compute`, `data.analysis.run` |
| 3 | Internal Draft | ALLOW | `intervention.draft`, `finding.generate` |
| 4 | Output / External | ASK | `participant.communication.send`, `bedrock.agent.invoke` |
| 5 | State Change | ASK | `account.transaction.execute`, `policy.override` |
| 6 | System Control | DENY | `agent.promote`, `system.config.change` |

Effects are declared in the `AgentManifest`. An agent cannot invoke an undeclared effect — the attempt raises `PermissionError` before it ever reaches the policy engine.

---

## AWS & Bedrock Alignment

| Foundry Concept | AWS / Bedrock Equivalent |
|-----------------|--------------------------|
| `AgentManifest` | IAM Role + Resource Policy (declares permitted actions) |
| `ControlTower` | SCP / Permission Boundary (policy enforcement point) |
| `LifecycleStage` | CodePipeline stage (SANDBOX → STAGING → PRODUCTION) |
| `HttpGateway` | API Gateway + VPC Link (governed data access) |
| `FoundryMemoryStore` | DynamoDB (agent memory with TTL) |
| `BedrockGuardrailsAdapter` | Bedrock Guardrails (PII, topic, profanity filtering) |
| `BedrockAgentStreamingClient` | Bedrock Agent Runtime (cross-agent orchestration) |
| `make_handler()` | Lambda function handler |
| `make_streaming_handler()` | Lambda Response Streaming |
| `BedrockAgentConstruct` | CDK L3 construct for Bedrock Agent + Alias |

---

## LangChain & LangGraph Support

Foundry does not require LangChain or LangGraph — they are optional extras. But when teams use them, Foundry integrates natively:

- **Every `BaseAgent` is a Runnable.** `invoke`, `ainvoke`, `stream`, `astream`, and the `|` LCEL pipe operator work out of the box with no adapter.
- **`FoundryToolkit`** converts all declared effects to `StructuredTool` objects consumable by any LangChain agent.
- **`GraphAgent`** wraps LangGraph `StateGraph` with governed node execution, checkpointing (`MemorySaver` / `SqliteSaver`), and `astream()` / `aget_state()` / `aupdate_state()`.

```
# LCEL example — no adapter needed
chain = retriever | my_foundry_agent | output_parser
```

---

## How to Build an Agent

| Step | What You Do | Foundry Does |
|------|-------------|-------------|
| **1. Declare** | Define `AgentManifest` with `allowed_effects`, `data_access`, `owner`, `lifecycle_stage` | Enforces scope; blocks undeclared effects at runtime |
| **2. Implement** | Subclass `BaseAgent`, implement `execute()`, call `run_effect()` for every action | Routes every call through `ControlTower`; writes audit log; handles `ALLOW`/`ASK`/`DENY` |
| **3. Eval & Promote** | Write `EvalScenario` suite; run `FoundryEvaluator`; promote via `foundry promote` CLI | Validates policy compliance before production; gates promotion on passing evals |

---

## Agent Lifecycle Stages

| Stage | Data Access | External Calls | Promotion Gate |
|-------|-------------|----------------|----------------|
| `SANDBOX` | Mocked only | None | Auto (dev iteration) |
| `STAGING` | Staging systems | Declared only | Eval suite passes |
| `PRODUCTION` | Production | Declared + approved | Eval suite + human sign-off |

---

## What's Built

### Core

| Module | Description |
|--------|-------------|
| `scaffold/base.py` | `BaseAgent` — abstract base with `run_effect()`, `log_outcome()`, full LCEL Runnable protocol |
| `scaffold/manifest.py` | `AgentManifest` — effect declarations, lifecycle, owner, policy path |
| `policy/effects.py` | 28+ `FinancialEffect` values across 6 tiers |
| `tollgate/tower.py` | `ControlTower` — synchronous and async policy execution, audit trail |
| `gateway/base.py` | `MockGatewayConnector`, `HttpGateway` (httpx + retry), `MultiGateway` (prefix routing) |

### Memory

| Module | Description |
|--------|-------------|
| `memory/buffer.py` | `ConversationBuffer` — session-keyed ring buffer, `to_openai_messages()`, `format_context()` |
| `memory/store.py` | `FoundryMemoryStore` — `LocalJsonStore` (dev), `DynamoDBMemoryBackend` (prod), TTL, `get_or_set()` |

### Tools & Evals

| Module | Description |
|--------|-------------|
| `tools/registry.py` | `@governed_tool` decorator + `ToolRegistry` — auto-discovery, every call through `run_effect()` |
| `eval/evaluator.py` | `FoundryEvaluator` + `EvalScenario` — ALLOW/ASK/DENY assertions, latency budgets, CI integration |

### Integrations

| Module | Description |
|--------|-------------|
| `integrations/langchain.py` | `FoundryTool`, `FoundryToolkit`, `FoundryRunnable` — LangChain adapters |
| `integrations/langgraph.py` | `GraphAgent` + `FoundryState` — LangGraph StateGraph with governed nodes, checkpointer, `astream()` |
| `integrations/bedrock_guardrails.py` | `BedrockGuardrailsAdapter` + `GuardrailsMixin` — Bedrock content safety layer |
| `integrations/bedrock_agent_client.py` | `BedrockAgentStreamingClient` — invoke Bedrock Agents with streaming, `AgentChunk` events |

### Deploy

| Module | Description |
|--------|-------------|
| `deploy/lambda_handler.py` | `make_handler()` + `make_streaming_handler()` — Lambda entry points, NDJSON streaming |
| `deploy/cdk/bedrock_agent_stack.py` | `BedrockAgentConstruct` — CDK L3 construct: CfnAgent, Alias, IAM role, outputs |

---

## Install Extras

```bash
pip install agent-foundry               # Core only
pip install 'agent-foundry[aws]'        # + boto3 (DynamoDB, Bedrock, Lambda)
pip install 'agent-foundry[http]'       # + httpx (HttpGateway)
pip install 'agent-foundry[langchain]'  # + langchain-core, langchain
pip install 'agent-foundry[langgraph]'  # + langgraph (includes langchain)
pip install 'agent-foundry[enterprise]' # Everything
```

---

*Agent Foundry · Engineering Overview · v0.1.0 · March 2026*
