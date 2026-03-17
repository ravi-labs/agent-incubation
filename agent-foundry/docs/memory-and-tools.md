# Memory & Tool Registry

Reference documentation for `foundry.memory` and `foundry.tools`.

---

## Memory

Foundry provides two memory layers that serve different purposes and can be used together.

| Layer | Class | Scope | Backend | Use Case |
|-------|-------|-------|---------|----------|
| Short-term | `ConversationBuffer` | In-memory per process | None (ring buffer) | Multi-turn conversation context |
| Long-term | `FoundryMemoryStore` | Persistent across restarts | File or DynamoDB | Recall past findings, cache computed scores |

---

## ConversationBuffer

`from foundry.memory.buffer import ConversationBuffer, Message`

An in-memory, session-keyed ring buffer for conversation history. Stores the most recent `max_turns` messages per session and discards older ones automatically.

### Constructor

```python
ConversationBuffer(
    max_turns:     int  = 50,    # max messages per session before oldest are dropped
    system_prompt: str | None = None,  # prepended to every to_openai_messages() call
)
```

### Core Methods

```python
buf = ConversationBuffer(max_turns=20, system_prompt="You are a fiduciary assistant.")

# Add messages
buf.add_user(session_id, "What is my risk score?")
buf.add_assistant(session_id, "Your risk score is 0.72.")
buf.add_system(session_id, "Context updated.")

# Retrieve history
messages: list[Message] = buf.get_history(session_id, last_n=5)

# Format as plain text context string
context: str = buf.format_context(session_id, last_n=10)

# Format for OpenAI / Anthropic APIs
openai_msgs: list[dict] = buf.to_openai_messages(session_id)
# → [{"role": "system", "content": "..."}, {"role": "user", "content": "..."}, ...]

# Introspection
buf.session_count()  # number of active sessions
buf.turn_count(session_id)  # messages in a session

# Reset
buf.clear(session_id)  # clear one session
buf.clear()            # clear all sessions
```

### Message Dataclass

```python
@dataclass
class Message:
    role:      str            # "user" | "assistant" | "system"
    content:   str
    timestamp: float          # time.time()
    metadata:  dict[str, Any] # custom context tags
```

---

## FoundryMemoryStore

`from foundry.memory.store import FoundryMemoryStore, MemoryEntry`

Long-term key-value memory scoped to an `agent_id` and a `namespace`. Supports TTL-based expiry.

### Constructor

```python
FoundryMemoryStore(
    agent_id: str,
    backend:  MemoryBackend,  # LocalJsonStore | DynamoDBMemoryBackend
)
```

### Core Methods

```python
store = FoundryMemoryStore(agent_id='my-agent', backend=LocalJsonStore('/tmp/mem.json'))

# Read / Write
value = await store.get('findings', 'fund-001')          # returns None if missing/expired
await store.set('findings', 'fund-001', {'score': 0.42}, ttl_days=90)
await store.delete('findings', 'fund-001')

# List keys
keys: list[str] = await store.keys('findings')           # all keys in namespace
all_entries = await store.all('findings')                 # dict[key, value]

# Cache-aside pattern
score = await store.get_or_set(
    namespace  = 'risk_scores',
    key        = 'fund-001',
    default_fn = lambda: compute_score('fund-001'),  # called only on cache miss
    ttl_days   = 7,
)
```

### Backends

#### LocalJsonStore (development)

```python
from foundry.memory.store import LocalJsonStore

backend = LocalJsonStore(path='/tmp/agent-memory.json')
```

Stores all entries in a single JSON file. Async operations use `asyncio.to_thread`. Not suitable for concurrent multi-process access.

#### DynamoDBMemoryBackend (production)

```python
from foundry.memory.store import DynamoDBMemoryBackend

backend = DynamoDBMemoryBackend(
    table_name = 'agent-foundry-memory',
    region     = 'us-east-1',
)
```

**DynamoDB schema:**

| Attribute | Type | Notes |
|-----------|------|-------|
| `pk` | String (Hash Key) | `{agent_id}#{namespace}` |
| `sk` | String (Sort Key) | `{key}` |
| `value` | String (JSON) | Serialised value |
| `expires_at` | Number | Unix timestamp — enable DynamoDB TTL on this attribute |
| `created_at` | String | ISO8601 |
| `updated_at` | String | ISO8601 |

Enable TTL on `expires_at` in the DynamoDB console or CDK to get automatic expiry.

---

## Tool Registry

`from foundry.tools.registry import governed_tool, ToolRegistry, GovernedToolDef`

The `ToolRegistry` makes every tool call policy-governed. Instead of calling tools directly inside `execute()`, you register them and invoke through the registry — which routes every call through `run_effect()`.

### @governed_tool Decorator

```python
from foundry.tools.registry import governed_tool
from foundry.policy.effects import FinancialEffect

@governed_tool(
    effect        = FinancialEffect.RISK_SCORE_COMPUTE,
    description   = 'Compute retirement readiness risk score for a participant.',
    intent_reason = 'Assess participant trajectory to prioritise interventions',
    params_schema = {'participant_id': 'str', 'age': 'int'},  # optional, for docs/LangChain
    tags          = ['risk', 'compute'],                       # optional, for filtering
)
async def compute_risk_score(self, participant_id: str, age: int) -> float:
    # your real implementation
    return 0.72
```

The decorator attaches a `__governed_tool__` attribute to the function that `ToolRegistry.register_all()` reads during auto-discovery.

### ToolRegistry

```python
from foundry.tools.registry import ToolRegistry

class MyAgent(BaseAgent):

    def __init__(self, manifest, tower, gateway, tracker=None):
        super().__init__(manifest, tower, gateway, tracker)
        self.tools = ToolRegistry(self)
        self.tools.register_all(self)   # auto-discovers all @governed_tool methods

    async def execute(self, participant_id: str, **kwargs) -> dict:
        # Invoke — all policy enforcement happens inside invoke()
        score = await self.tools.invoke(
            'compute_risk_score',
            participant_id = participant_id,
            age            = 55,
        )
        return {'risk_score': score}
```

### ToolRegistry Methods

```python
registry = ToolRegistry(agent)

# Registration
registry.register(tool_def)             # register a GovernedToolDef directly
registry.register_all(agent)            # auto-discover all @governed_tool methods on agent

# Invocation
result = await registry.invoke('tool_name', **params)

# Introspection
tools: list[GovernedToolDef] = registry.list_tools()
tools_by_tag = registry.list_tools(tag='risk')
tool: GovernedToolDef | None = registry.get('compute_risk_score')
names: list[str] = registry.names()
```

### GovernedToolDef Fields

```python
@dataclass
class GovernedToolDef:
    name:          str
    fn:            Callable           # the async function
    effect:        FinancialEffect    # governs policy enforcement
    description:   str
    intent_reason: str                # passed to ControlTower audit log
    params_schema: dict[str, str]     # {param_name: type_hint}
    tags:          list[str]
```

### Manual Registration (no decorator)

```python
from foundry.tools.registry import GovernedToolDef

registry.register(GovernedToolDef(
    name          = 'compute_risk_score',
    fn            = self.compute_risk_score,
    effect        = FinancialEffect.RISK_SCORE_COMPUTE,
    description   = 'Compute risk score.',
    intent_reason = 'Assess trajectory',
    params_schema = {'participant_id': 'str'},
    tags          = ['risk'],
))
```

---

## Using Both Layers Together

```python
from foundry.memory.buffer import ConversationBuffer
from foundry.memory.store  import FoundryMemoryStore, LocalJsonStore
from foundry.tools.registry import governed_tool, ToolRegistry

class FullAgent(BaseAgent):

    def __init__(self, manifest, tower, gateway, tracker=None):
        backend  = LocalJsonStore('/tmp/agent.json')
        long_mem = FoundryMemoryStore(agent_id=manifest.agent_id, backend=backend)
        short_mem = ConversationBuffer(max_turns=20)

        # Wire into BaseAgent — pick one for self.memory or manage both manually
        super().__init__(manifest, tower, gateway, tracker, memory=short_mem)

        self.long_memory = long_mem          # second memory layer as a custom attribute
        self.tools = ToolRegistry(self)
        self.tools.register_all(self)

    @governed_tool(
        effect        = FinancialEffect.RISK_SCORE_COMPUTE,
        description   = 'Compute risk score.',
        intent_reason = 'Assess participant trajectory',
    )
    async def score(self, participant_id: str) -> float:
        # Check long-term memory first
        cached = await self.long_memory.get('scores', participant_id)
        if cached:
            return cached
        score = await self._compute(participant_id)
        await self.long_memory.set('scores', participant_id, score, ttl_days=1)
        return score

    async def execute(self, user_input: str, session_id: str = 'default', **kwargs):
        self.memory.add_user(session_id, user_input)
        pid = kwargs.get('participant_id', 'unknown')

        score = await self.tools.invoke('score', participant_id=pid)

        response = f"Risk score for {pid}: {score:.2f}"
        self.memory.add_assistant(session_id, response)
        return {'response': response, 'risk_score': score}
```

---

*Agent Foundry · Memory & Tool Registry · v0.1.0 · March 2026*
