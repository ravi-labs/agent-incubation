# Gateway & Integrations

Reference documentation for `foundry.gateway`, `foundry.integrations.langchain`, `foundry.integrations.langgraph`, and `foundry.integrations.bedrock_agent_client`.

---

## Gateway

`from foundry.gateway.base import GatewayConnector, DataRequest, DataResponse`

All agent data access goes through a `GatewayConnector`. Agents never connect directly to databases or APIs — they declare permitted sources in their manifest and fetch exclusively through the gateway. This guarantees consistent access logging, a centralised permission model, and easy substitution of real connectors with mocked data in sandbox environments.

### DataRequest / DataResponse

```python
@dataclass(frozen=True)
class DataRequest:
    source:   str             # named data source (declared in manifest data_access)
    params:   dict[str, Any]  # query parameters
    metadata: dict[str, Any]  # optional context for access logging

@dataclass(frozen=True)
class DataResponse:
    source: str
    data:   Any
    cached: bool
    meta:   dict[str, Any]
```

### GatewayConnector Protocol

Any class that implements `async def fetch(self, request: DataRequest) -> DataResponse` satisfies the protocol. Foundry ships three built-in connectors:

---

### MockGatewayConnector

`from foundry.gateway.base import MockGatewayConnector`

In-memory connector for sandbox testing. Preload with static data keyed by source name.

```python
gateway = MockGatewayConnector({
    'participant.data': {
        'p-001': {'balance': 84200, 'contrib_rate': 0.03, 'age': 55},
        'p-002': {'balance': 12000, 'contrib_rate': 0.01, 'age': 34},
    },
    'plan.data': {
        'plan-001': {'auto_enroll_rate': 0.03, 'match_pct': 0.50}
    },
})

# Register additional sources at runtime
gateway.register('benchmark.returns', {'S&P500': [0.12, 0.08, -0.04]})

# Inside execute():
resp = await self.gateway.fetch(DataRequest('participant.data', {'id': 'p-001'}))
participant = resp.data['p-001']
```

---

### HttpGateway

`from foundry.gateway.base import HttpGateway`

Async REST connector using `httpx`. Requires `pip install 'agent-foundry[http]'`.

```python
gateway = HttpGateway(
    base_url   = 'https://participant-api.internal.company.com/v1',
    headers    = {'Authorization': 'Bearer my-service-token', 'X-App': 'agent-foundry'},
    timeout    = 10.0,       # seconds
    method     = 'GET',      # or 'POST'
    source_key = 'path',     # 'path' → appends source as URL path, 'param' → query param
    retries    = 2,          # retry count on transient errors
    verify_ssl = True,
)

# Shutdown (call on agent teardown)
await gateway.close()
```

**Request mapping:**

```
source_key="path":
  DataRequest("participant.data", {"id": "p-001"})
  → GET https://participant-api.internal.company.com/v1/participant.data?id=p-001

source_key="path" + method="POST":
  → POST https://.../.../participant.data  body: {"id": "p-001"}

source_key="param":
  → GET https://.../?source=participant.data&id=p-001
```

**Retry behaviour:** Exponential back-off: 0.5s after attempt 1, 1.0s after attempt 2. Raises `RuntimeError` after all retries are exhausted.

---

### MultiGateway

`from foundry.gateway.base import MultiGateway`

Routes `DataRequest`s to different connectors based on the longest prefix match of `request.source`.

```python
from foundry.gateway.base import MultiGateway, HttpGateway, MockGatewayConnector

gateway = MultiGateway(
    connectors = {
        'participant': HttpGateway('https://participant-api.internal.com/v1'),
        'plan':        HttpGateway('https://plan-api.internal.com/v1'),
        'benchmark':   MockGatewayConnector({'benchmark.returns': [...]}),
    },
    default = MockGatewayConnector({}),  # catch-all (optional)
)

# Routing examples:
# 'participant.data'    → participant connector (prefix 'participant')
# 'participant.account' → participant connector (prefix 'participant')
# 'plan.lineup'         → plan connector        (prefix 'plan')
# 'benchmark.returns'   → benchmark connector   (prefix 'benchmark')
# 'other.source'        → default connector     (empty prefix catch-all)

# Register a new connector at runtime
gateway.register('market', HttpGateway('https://market-api.internal.com/v1'))
```

---

## LangChain Integration

`from foundry.integrations.langchain import FoundryTool, FoundryToolkit, FoundryRunnable`

Requires `pip install 'agent-foundry[langchain]'`.

### FoundryTool — Single Effect as LangChain Tool

```python
from foundry.integrations.langchain import FoundryTool
from foundry.policy.effects import FinancialEffect

# Wrap a single effect as a LangChain StructuredTool
tool = FoundryTool.from_effect(
    agent         = my_agent,
    effect        = FinancialEffect.RISK_SCORE_COMPUTE,
    name          = 'compute_risk_score',
    description   = 'Compute the retirement readiness risk score for a participant.',
    params_schema = {'participant_id': 'str', 'age': 'int'},
)

langchain_tool = tool.as_langchain_tool()
```

### FoundryToolkit — All Declared Effects as Tools

```python
from foundry.integrations.langchain import FoundryToolkit

toolkit = FoundryToolkit.from_agent(
    agent          = my_agent,
    include_tiers  = [1, 2, 3],      # only expose tiers 1-3 by default
    exclude_effects = [               # optionally suppress specific effects
        FinancialEffect.DATA_COMPLIANCE_READ
    ],
)

tools = toolkit.get_tools()   # list[StructuredTool] — ready for LangChain agents
```

### FoundryRunnable — Full LCEL Runnable

```python
from foundry.integrations.langchain import FoundryRunnable

runnable = FoundryRunnable(agent=my_agent)

# All Runnable methods are available
result       = runnable.invoke({'participant_id': 'p-001'})
result       = await runnable.ainvoke({'participant_id': 'p-001'})
chunks       = list(runnable.stream({'participant_id': 'p-001'}))
async for c in runnable.astream({'participant_id': 'p-001'}): print(c)

# LCEL composition
chain = retriever | runnable | output_parser
```

### BaseAgent Native Runnable Protocol

Every `BaseAgent` is already a Runnable without any adapter. Use `FoundryRunnable` only when you need LangChain's registration system or virtual Runnable subclass behaviour.

```python
# These all work directly on any BaseAgent:
result = agent.invoke({'participant_id': 'p-001'})
result = await agent.ainvoke({'participant_id': 'p-001'})
chain  = retriever | agent | parser    # LCEL |
```

---

## LangGraph Integration

`from foundry.integrations.langgraph import GraphAgent, FoundryState`

Requires `pip install 'agent-foundry[langgraph]'`.

### FoundryState

Base `TypedDict` for all graph state classes. Extend it with your agent-specific fields.

```python
from foundry.integrations.langgraph import FoundryState

class WatchdogState(FoundryState):
    fund_id:          str   = ''
    expense_ratio:    float = 0.0
    risk_score:       float = 0.0
    finding_draft:    str   = ''
    finding_severity: str   = 'none'
    emitted:          bool  = False
```

### GraphAgent

```python
from foundry.integrations.langgraph import GraphAgent
from langgraph.graph import StateGraph, END

class WatchdogGraphAgent(GraphAgent[WatchdogState]):

    def new_graph(self) -> StateGraph:
        g = StateGraph(WatchdogState)
        g.add_node('fetch',   self.fetch_fund_data)
        g.add_node('score',   self.score_risk)
        g.add_node('report',  self.emit_finding)
        g.set_entry_point('fetch')
        g.add_edge('fetch', 'score')
        g.add_conditional_edges('score', self._route_by_severity, {
            'low':  'report',
            'high': 'escalate',
        })
        g.add_edge('report', END)
        return g

    async def execute(self, fund_id: str, **kwargs) -> dict:
        return await self._run(WatchdogState(fund_id=fund_id))
```

### Checkpointing (Thread-Safe Persistence)

```python
from langgraph.checkpoint.memory import MemorySaver

agent = WatchdogGraphAgent(
    manifest    = manifest,
    tower       = tower,
    gateway     = gateway,
    checkpointer = MemorySaver(),   # or SqliteSaver for file-backed persistence
)

# Scoped to a thread_id — state is saved between calls
result = await agent.execute(fund_id='FUND001', thread_id='run-abc-123')

# Resume or inspect state
state = await agent.aget_state(thread_id='run-abc-123')

# Update state for human-in-the-loop
await agent.aupdate_state(thread_id='run-abc-123', values={'risk_score': 0.85})
```

### Streaming Graph Execution

```python
# stream_mode options: "updates" | "values" | "debug"
async for snapshot in agent.astream(
    {'fund_id': 'FUND001'},
    thread_id   = 'run-abc-123',
    stream_mode = 'updates',
):
    print(snapshot)   # intermediate state updates as nodes complete
```

---

## Bedrock Agent Client

`from foundry.integrations.bedrock_agent_client import BedrockAgentStreamingClient, AgentChunk`

Invoke other Bedrock Agents from within a Foundry agent. The invocation is governed by the `BEDROCK_AGENT_INVOKE` effect (Tier 4, ASK by default).

### AgentChunk Events

```python
@dataclass
class AgentChunk:
    text:       str | None
    event_type: str   # "text" | "trace" | "returnControl" | "files" | "done"
    raw:        dict
    metadata:   dict

    # Properties
    is_text:        bool
    is_trace:       bool
    is_done:        bool
    is_return_ctrl: bool
```

### Streaming Invocation

```python
from foundry.integrations.bedrock_agent_client import BedrockAgentStreamingClient

client = BedrockAgentStreamingClient(
    agent        = my_foundry_agent,   # provides run_effect() for governance
    agent_id     = 'BEDROCK_AGENT_ID',
    agent_alias  = 'TSTALIASID',
    region       = 'us-east-1',
)

# Stream chunks from Bedrock Agent
async for chunk in client.stream_invoke(
    session_id = 'session-001',
    input_text = 'Analyse fund FUND001 for fiduciary risk',
):
    if chunk.is_text:
        print(chunk.text, end='', flush=True)
    elif chunk.is_done:
        break

# Non-streaming (collects all text into a single string)
full_response = await client.invoke(
    session_id = 'session-001',
    input_text = 'Analyse fund FUND001',
)
```

### Manifest Declaration

```python
manifest = AgentManifest(
    agent_id        = 'orchestrator-agent',
    allowed_effects = [
        FinancialEffect.BEDROCK_AGENT_INVOKE,   # Tier 4 — will ASK by default
        # Add policy override to ALLOW for specific sub-agents if appropriate:
        # policy_path = 'policies/orchestrator.yaml'
    ],
    # ...
)
```

---

## Lambda Deployment

`from foundry.deploy.lambda_handler import make_handler, make_streaming_handler`

### Standard Lambda Handler

```python
from foundry.deploy.lambda_handler import make_handler
from my_agent import FiduciaryAgent

# Creates a Lambda handler function bound to your agent class
handler = make_handler(FiduciaryAgent, manifest=manifest, tower=tower, gateway=gateway)

# Lambda invokes: handler(event, context) → {"statusCode": 200, "body": {...}}
```

### Streaming Lambda Handler (NDJSON)

```python
from foundry.deploy.lambda_handler import make_streaming_handler

streaming_handler = make_streaming_handler(
    FiduciaryAgent,
    manifest = manifest,
    tower    = tower,
    gateway  = gateway,
)

# Lambda Response Streaming entry point:
# streaming_handler(event, context, response_stream)
# → writes NDJSON chunks: {"type": "chunk", "data": ...}\n
# → terminates with:      {"type": "complete"}\n
```

---

*Agent Foundry · Gateway & Integrations · v0.1.0 · March 2026*
