# Agent Foundry — Developer Quick Start Guide

> Build governed, production-ready AI agents on AWS with policy enforcement, audit trails, and ERISA-compliant safeguards.

---

## Overview

Agent Foundry is an enterprise agent incubation platform. Every agent built on Foundry inherits a full governance stack — policy enforcement, audit logging, human review workflows, and lifecycle management — with zero boilerplate.

This guide covers the six core capabilities you need to build a production-grade Foundry agent:

1. **Agent Identity** — declare who your agent is and what it can do
2. **Policy & Effects** — what the agent is allowed, requires approval for, or is denied
3. **Gateway & Tool Calls** — governed data access and tool invocations
4. **Memory** — short-term conversation buffers and long-term persistent storage
5. **Evals** — automated scenario-based policy compliance testing
6. **Guardrails** — Bedrock content safety layer for inputs and outputs

### Installation

```bash
# Core (no optional deps)
pip install agent-foundry

# With AWS backends (DynamoDB memory, Bedrock guardrails, Lambda)
pip install 'agent-foundry[aws]'

# With LangGraph orchestration
pip install 'agent-foundry[langgraph]'

# With HTTP gateway connector
pip install 'agent-foundry[http]'

# Everything (enterprise bundle)
pip install 'agent-foundry[enterprise]'
```

---

## 1  Agent Identity — AgentManifest

The `AgentManifest` is the source of truth for your agent's identity, declared permissions, and lifecycle stage. Every agent must have one. It enforces that your agent can only use the effects it explicitly declares.

### 1.1  Defining a Manifest

```python
from foundry.scaffold.manifest import AgentManifest, LifecycleStage
from foundry.policy.effects import FinancialEffect

manifest = AgentManifest(
    agent_id          = 'fiduciary-watchdog-v1',
    version           = '1.0.0',
    owner             = 'investment-ops@company.com',
    lifecycle_stage   = LifecycleStage.SANDBOX,   # SANDBOX | STAGING | PRODUCTION
    environment       = 'sandbox',
    allowed_effects   = [
        FinancialEffect.RISK_SCORE_COMPUTE,
        FinancialEffect.INTERVENTION_DRAFT,
        FinancialEffect.PARTICIPANT_COMMUNICATION_SEND,  # requires ASK
    ],
    data_access       = ['participant.data', 'plan.benchmarks'],
    description       = 'Monitors fiduciary risk and flags action items',
    policy_path       = 'policies/fiduciary_watchdog.yaml',
    tags              = ['fiduciary', 'risk', 'erisa'],
)
```

### 1.2  Lifecycle Stages

| Stage | Environment | Restrictions |
|-------|-------------|--------------|
| `SANDBOX` | sandbox | No production data, no external calls, mocked effects only |
| `STAGING` | staging | Can access staging data, limited to declared effects |
| `PRODUCTION` | production | Full access, stricter policy, requires approval for ASK effects |

> **Tip:** Start every agent in `SANDBOX`. Promote to `STAGING` and then `PRODUCTION` through the `foundry` lifecycle CLI once eval suites pass.

---

## 2  Policy & Effects

All agent actions are governed by `FinancialEffect` declarations. The ControlTower (Tollgate) enforces the policy for every effect invocation: `ALLOW`, `ASK` (human review), or `DENY`.

### 2.1  Effect Taxonomy (6 Tiers)

| Tier | Category | Default | Example Effects |
|------|----------|---------|-----------------|
| 1 | Data Access (Read) | `ALLOW` | `participant.data.read`, `knowledge.base.retrieve` |
| 2 | Compute & Analysis | `ALLOW` | `risk.score.compute`, `compliance.evaluate` |
| 3 | Internal Draft | `ALLOW` | `intervention.draft`, `finding.draft` |
| 4 | Output / External | `ASK` | `participant.communication.send`, `bedrock.agent.invoke` |
| 5 | Persistence | `ALLOW` | `audit.log.write`, `intervention.log.write` |
| 6 | System Control | `DENY` | `agent.promote`, `policy.rule.modify` |

### 2.2  Running an Effect

```python
# Inside execute() — every tool call MUST go through run_effect()
result = await self.run_effect(
    effect        = FinancialEffect.RISK_SCORE_COMPUTE,
    tool          = 'risk_scorer',
    action        = 'compute',
    params        = {'participant_id': pid, 'age': 55},
    intent_action = 'compute_risk_score',
    intent_reason = 'Identify at-risk participants for intervention',
    exec_fn       = lambda: risk_scorer.run(pid),
)
```

### 2.3  Policy YAML

```yaml
# policies/fiduciary_watchdog.yaml
version: '1.0'
agent: fiduciary-watchdog-v1
rules:
  - effect: participant.communication.send
    action: ASK
    when:
      - field: params.participant_count
        op: gt
        value: 100
  - effect: account.transaction.execute
    action: DENY
```

---

## 3  Gateway & Tool Calls

All data access goes through a `GatewayConnector`. Agents never connect directly to databases or APIs — they declare sources in their manifest and fetch through the gateway. Tool calls go through `ToolRegistry`, which routes every invocation through `run_effect()` for policy enforcement.

### 3.1  Gateway Connectors

| Connector | When to Use |
|-----------|-------------|
| `MockGatewayConnector` | Unit tests and sandbox; preloads static dict data |
| `HttpGateway` | REST API backends; async with retry & back-off (requires `httpx`) |
| `MultiGateway` | Route different source prefixes to different backends |

```python
from foundry.gateway.base import HttpGateway, MultiGateway, MockGatewayConnector, DataRequest

# Development — mock data
gateway = MockGatewayConnector({
    'participant.data': {'p-001': {'balance': 84200, 'contrib_rate': 0.03}},
})

# Production — route by source prefix
gateway = MultiGateway({
    'participant': HttpGateway('https://participant-api.internal.com/v1',
                               headers={'Authorization': 'Bearer {token}'}),
    'plan':        HttpGateway('https://plan-api.internal.com/v1'),
})

# Fetching inside execute()
resp = await self.gateway.fetch(DataRequest('participant.data', {'id': pid}))
data = resp.data
```

### 3.2  Governed Tool Registry

Use `@governed_tool` to declare tool methods and `ToolRegistry` to invoke them. Every invocation is policy-checked and audit-logged automatically.

```python
from foundry.tools.registry import governed_tool, ToolRegistry
from foundry.policy.effects import FinancialEffect

class FiduciaryAgent(BaseAgent):

    def __init__(self, manifest, tower, gateway, tracker=None):
        super().__init__(manifest, tower, gateway, tracker)
        self.tools = ToolRegistry(self)
        self.tools.register_all(self)   # auto-discovers @governed_tool methods

    @governed_tool(
        effect        = FinancialEffect.RISK_SCORE_COMPUTE,
        description   = 'Compute retirement readiness risk score.',
        intent_reason = 'Assess participant trajectory',
        params_schema = {'participant_id': 'str', 'age': 'int'},
    )
    async def compute_risk_score(self, participant_id: str, age: int) -> float:
        return 0.72   # your real logic here

    async def execute(self, participant_id: str, **kwargs) -> dict:
        score = await self.tools.invoke('compute_risk_score',
                                        participant_id=participant_id, age=55)
        return {'risk_score': score}
```

---

## 4  Memory

Foundry provides two memory layers. `ConversationBuffer` is an in-memory ring buffer for multi-turn conversation context. `FoundryMemoryStore` is long-term persisted key-value storage (JSON file for dev, DynamoDB for production).

### 4.1  Short-Term: ConversationBuffer

```python
from foundry.memory.buffer import ConversationBuffer

class ChatAgent(BaseAgent):

    def __init__(self, manifest, tower, gateway, tracker=None):
        super().__init__(manifest, tower, gateway, tracker,
                         memory=ConversationBuffer(max_turns=20))

    async def execute(self, user_input: str, session_id: str = 'default', **kwargs):
        self.memory.add_user(session_id, user_input)
        context = self.memory.format_context(session_id, last_n=5)

        # Pass context to your LLM
        response = await self._generate(context + '\nUser: ' + user_input)

        self.memory.add_assistant(session_id, response)
        return {'response': response}

# OpenAI / Anthropic API messages format:
# messages = self.memory.to_openai_messages(session_id)
```

### 4.2  Long-Term: FoundryMemoryStore

```python
from foundry.memory.store import FoundryMemoryStore, LocalJsonStore
# Production: from foundry.memory.store import DynamoDBMemoryBackend

class WatchdogAgent(BaseAgent):

    def __init__(self, manifest, tower, gateway, tracker=None):
        backend  = LocalJsonStore('/tmp/watchdog-memory.json')  # or DynamoDB
        long_mem = FoundryMemoryStore(agent_id=manifest.agent_id, backend=backend)
        super().__init__(manifest, tower, gateway, tracker, memory=long_mem)

    async def execute(self, fund_id: str, **kwargs) -> dict:
        # Recall previous finding
        last = await self.memory.get('findings', fund_id)

        # Cache-aside pattern: compute only if not cached
        score = await self.memory.get_or_set(
            'risk_scores', fund_id,
            default_fn=lambda: self._compute_score(fund_id),
            ttl_days=7,
        )

        finding = {'severity': 'low', 'score': score}
        await self.memory.set('findings', fund_id, finding, ttl_days=90)
        return finding
```

> **Production:** Swap `LocalJsonStore` for `DynamoDBMemoryBackend(table_name='agent-foundry-memory', region='us-east-1')`. Enable DynamoDB TTL on the `expires_at` attribute for automatic expiry.

---

## 5  Agent Evals — FoundryEvaluator

`FoundryEvaluator` runs structured `EvalScenario`s against a live agent and verifies that policy decisions (`ALLOW`/`ASK`/`DENY`), output content, and latency budgets all match expectations. It instruments `run_effect()` directly — no mocking required.

### 5.1  Writing Scenarios

```python
from foundry.eval import FoundryEvaluator, EvalScenario

scenarios = [
    EvalScenario(
        name                   = 'risk_score_allowed',
        inputs                 = {'participant_id': 'p-001'},
        expect_effects_allowed = ['risk.score.compute'],
        expect_output_contains = {'risk_score'},
        max_latency_ms         = 2000,
    ),
    EvalScenario(
        name                 = 'send_requires_approval',
        inputs               = {'participant_id': 'p-001', 'message': 'Action needed'},
        expect_effects_asked = ['participant.communication.send'],
        expect_no_exception  = False,   # TollgateDeferred is expected
    ),
    EvalScenario(
        name                  = 'transaction_denied',
        inputs                = {'participant_id': 'p-001', 'amount': 1000},
        expect_effects_denied = ['account.transaction.execute'],
        expect_exception_type = 'PermissionError',
    ),
]
```

### 5.2  Running Evals

```python
agent     = FiduciaryAgent(manifest, tower, gateway)
evaluator = FoundryEvaluator(agent, verbose=True)

results = await evaluator.run(scenarios)
evaluator.print_report(results)

# CI assertion
assert all(r.passed for r in results), 'Eval suite failed'
```

```python
# Pytest integration
import pytest

@pytest.mark.asyncio
async def test_policy_compliance(make_agent):
    evaluator = FoundryEvaluator(make_agent())
    failures  = [r for r in await evaluator.run(scenarios) if not r.passed]
    assert not failures, f"Failed scenarios: {[r.name for r in failures]}"
```

---

## 6  Bedrock Guardrails

Bedrock Guardrails add a content safety layer on top of Tollgate policy. Tollgate enforces _business policy_ (is this agent allowed to do this?). Guardrails enforce _content safety_ (is the input/output safe to process/return?). They are complementary, not alternatives.

| Layer | Enforces | Raises on block |
|-------|----------|-----------------|
| **Tollgate** | Business policy — ALLOW/ASK/DENY on declared `FinancialEffect`s | `TollgateDeferred`, `PermissionError` |
| **Guardrails** | Content safety — PII redaction, topic blocking, profanity, grounding | `GuardrailIntervention` |

### 6.1  Standalone Adapter

```python
from foundry.integrations.bedrock_guardrails import BedrockGuardrailsAdapter

class SafeAgent(BaseAgent):

    def __init__(self, manifest, tower, gateway, tracker=None):
        super().__init__(manifest, tower, gateway, tracker)
        self.guardrails = BedrockGuardrailsAdapter(
            guardrail_id      = 'abc123def456',
            guardrail_version = 'DRAFT',
            region            = 'us-east-1',
        )

    async def execute(self, user_input: str, **kwargs) -> dict:
        # Screen input before processing
        clean_input = await self.guardrails.check_input(
            text       = user_input,
            session_id = kwargs.get('session_id', 'default'),
        )

        response = await self._generate(clean_input)

        # Screen output before returning
        safe_output = await self.guardrails.check_output(
            text       = response,
            session_id = kwargs.get('session_id', 'default'),
        )
        return {'response': safe_output}
```

### 6.2  Mixin Pattern (Auto-Wraps execute())

```python
from foundry.integrations.bedrock_guardrails import GuardrailsMixin

# IMPORTANT: GuardrailsMixin must come BEFORE BaseAgent in MRO
class SafeAgent(GuardrailsMixin, BaseAgent):
    guardrail_id      = 'abc123def456'
    guardrail_version = 'DRAFT'
    guardrail_region  = 'us-east-1'   # optional

    async def execute(self, user_input: str, **kwargs) -> dict:
        # user_input has already been screened — no extra code needed
        response = await self._generate(user_input)
        # response will be screened before returning
        return {'response': response}
```

> `GuardrailsMixin` screens input keys: `user_input`, `input`, `message`, `query` — and output keys: `response`, `text`, `output`, `message`, `answer`. Raises `GuardrailIntervention` when content is blocked.

---

## 7  Knowledge Base Integration

Foundry integrates with Amazon Bedrock Knowledge Bases through a governed effect. Retrieval calls go through `run_effect()` — policy-checked and audit-logged like any other tool call.

### 7.1  Retrieve from a Knowledge Base

```python
from foundry.policy.effects import FinancialEffect
import boto3, asyncio

class KBAgent(BaseAgent):

    def __init__(self, manifest, tower, gateway, tracker=None):
        super().__init__(manifest, tower, gateway, tracker)
        self._kb_client = boto3.client('bedrock-agent-runtime', region_name='us-east-1')
        self._kb_id     = 'your-knowledge-base-id'

    async def execute(self, query: str, **kwargs) -> dict:
        results = await self.run_effect(
            effect        = FinancialEffect.KNOWLEDGE_BASE_RETRIEVE,
            tool          = 'bedrock_kb',
            action        = 'retrieve',
            params        = {'query': query, 'kb_id': self._kb_id},
            intent_action = 'kb_retrieval',
            intent_reason = 'Retrieve relevant plan documents for query',
            exec_fn       = lambda: self._retrieve(query),
        )
        return {'passages': results}

    async def _retrieve(self, query: str) -> list:
        def _sync():
            resp = self._kb_client.retrieve(
                knowledgeBaseId        = self._kb_id,
                retrievalQuery         = {'text': query},
                retrievalConfiguration = {
                    'vectorSearchConfiguration': {'numberOfResults': 5}
                },
            )
            return [r['content']['text'] for r in resp['retrievalResults']]
        return await asyncio.to_thread(_sync)
```

### 7.2  Manifest Declaration

```python
manifest = AgentManifest(
    agent_id        = 'kb-query-agent',
    allowed_effects = [
        FinancialEffect.KNOWLEDGE_BASE_RETRIEVE,   # Tier 1 — ALLOW by default
        FinancialEffect.RISK_SCORE_COMPUTE,
    ],
    # ...
)
```

---

## 8  LangChain & LangGraph Integration

Every `BaseAgent` is a native LCEL `Runnable` — no adapter needed. For multi-step stateful workflows, use `GraphAgent` which wraps LangGraph `StateGraph` with governed execution.

### 8.1  LCEL Pipe Composition

```python
# Every BaseAgent exposes invoke / ainvoke / stream / astream / | operator

# Standalone async call
result = await agent.ainvoke({'fund_id': 'FUND001'})

# LCEL pipeline: retriever → agent → output parser
chain  = retriever | agent | output_parser
result = chain.invoke({'fund_id': 'FUND001'})

# Streaming
async for chunk in agent.astream({'user_input': 'What is my risk score?'}):
    print(chunk)
```

### 8.2  LangChain Tool Adapters

```python
from foundry.integrations.langchain import FoundryToolkit

# Expose all declared effects as LangChain StructuredTools
toolkit = FoundryToolkit.from_agent(agent, include_tiers=[1, 2, 3])
tools   = toolkit.get_tools()   # list[StructuredTool]

# Use in any LangChain agent / chain
llm_agent = create_openai_functions_agent(llm, tools, prompt)
```

### 8.3  GraphAgent (LangGraph)

```python
from foundry.integrations.langgraph import GraphAgent, FoundryState
from langgraph.graph import StateGraph, END

class WatchdogState(FoundryState):
    fund_id:    str   = ''
    risk_score: float = 0.0
    finding:    str   = ''

class WatchdogGraphAgent(GraphAgent[WatchdogState]):

    def new_graph(self) -> StateGraph:
        g = StateGraph(WatchdogState)
        g.add_node('fetch',  self.fetch_data)
        g.add_node('score',  self.score_risk)
        g.add_node('report', self.emit_finding)
        g.set_entry_point('fetch')
        g.add_edge('fetch', 'score')
        g.add_edge('score', 'report')
        g.add_edge('report', END)
        return g

    async def fetch_data(self, state: WatchdogState) -> dict:
        return {'fund_data': await self._fetch(state.fund_id)}

    async def score_risk(self, state: WatchdogState) -> dict:
        score = await self.tools.invoke('compute_risk', fund_id=state.fund_id)
        return {'risk_score': score}

    async def emit_finding(self, state: WatchdogState) -> dict:
        return {'finding': f'Risk score: {state.risk_score}'}

    async def execute(self, fund_id: str, **kwargs):
        return await self._run(WatchdogState(fund_id=fund_id))
```

---

## 9  Quick Reference

### Imports

| Capability | Import |
|------------|--------|
| Agent base class | `from foundry.scaffold.base import BaseAgent` |
| Manifest + lifecycle | `from foundry.scaffold.manifest import AgentManifest, LifecycleStage` |
| Effects taxonomy | `from foundry.policy.effects import FinancialEffect` |
| Gateway connectors | `from foundry.gateway.base import HttpGateway, MultiGateway, MockGatewayConnector, DataRequest` |
| Conversation buffer | `from foundry.memory.buffer import ConversationBuffer` |
| Long-term memory | `from foundry.memory.store import FoundryMemoryStore, LocalJsonStore, DynamoDBMemoryBackend` |
| Tool registry | `from foundry.tools.registry import governed_tool, ToolRegistry` |
| Evals framework | `from foundry.eval import FoundryEvaluator, EvalScenario` |
| Bedrock guardrails | `from foundry.integrations.bedrock_guardrails import BedrockGuardrailsAdapter, GuardrailsMixin` |
| LangGraph agent | `from foundry.integrations.langgraph import GraphAgent, FoundryState` |
| Lambda handler | `from foundry.deploy.lambda_handler import make_handler, make_streaming_handler` |
| Bedrock agent client | `from foundry.integrations.bedrock_agent_client import BedrockAgentStreamingClient` |

### Effect Decision Defaults

| Decision | Meaning | Examples |
|----------|---------|---------|
| `ALLOW` | Auto-approved | data reads, compute, draft generation |
| `ASK` | Human review queued (`TollgateDeferred` raised) | send communications, invoke external agents |
| `DENY` | Blocked immediately (`PermissionError` raised) | system config, promote agent, transaction execute |

### Checklist: Building a New Agent

- [ ] Define `AgentManifest` with `allowed_effects` and `data_access` declarations
- [ ] Subclass `BaseAgent` and implement `execute()`
- [ ] Route all tool calls through `run_effect()` or `ToolRegistry.invoke()`
- [ ] Fetch data exclusively through `self.gateway.fetch()`
- [ ] Add `ConversationBuffer` or `FoundryMemoryStore` if multi-turn or stateful
- [ ] Write `EvalScenario`s covering `ALLOW`, `ASK`, and `DENY` paths
- [ ] Run `evaluator.run(scenarios)` in CI — assert `all(r.passed for r in results)`
- [ ] Add `BedrockGuardrailsAdapter` or `GuardrailsMixin` for user-facing agents
- [ ] Start in `SANDBOX` lifecycle stage; promote through `foundry` CLI after evals pass

---

*Agent Foundry · Developer Quick Start Guide · v0.1.0 · March 2026*
