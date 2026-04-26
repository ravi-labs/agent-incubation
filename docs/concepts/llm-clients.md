# LLM clients

Arc treats LLM calls as just another effect. The agent calls a model via
an `LLMClient`; the client routes the call through `agent.run_effect()`,
which means **every model invocation is policy-evaluated and audit-logged
exactly like a tool call**.

`LLMClient` is a Protocol — agents accept it; the platform ships two
concrete implementations and you can write your own.

> **Code:** [`arc/packages/arc-core/src/arc/core/llm.py`](../../arc/packages/arc-core/src/arc/core/llm.py)
> **Public API:** `from arc.core import LLMClient`

---

## The contract

```python
from typing import Protocol, runtime_checkable

@runtime_checkable
class LLMClient(Protocol):
    async def generate(
        self, *,
        agent,                          # the BaseAgent making the call
        effect,                         # domain effect (FinancialEffect.X, …)
        intent_action: str,
        intent_reason: str,
        prompt: str,
        system: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.3,
        metadata: dict | None = None,
    ) -> str: ...

    async def generate_json(...) -> dict: ...
```

Two design rules every implementation follows:

1. **Stateless w.r.t. the agent.** The `agent` is a per-call argument,
   not a constructor argument. One client instance can be shared across
   agents, threads, requests.

2. **Routes through `agent.run_effect()` internally.** The implementation
   wraps its provider call in `await agent.run_effect(effect=..., tool=...,
   action=..., exec_fn=<provider call>)`. ControlTower can therefore deny
   the call (policy), defer it for human review (ASK), or audit it
   (always). The prompt content is never stored in the policy engine —
   only the effect, intent, model id, token estimate, and prompt size.

---

## The two shipped implementations

### `BedrockLLMClient` — AWS-native, boto3

```python
from arc.connectors import BedrockLLMClient

llm = BedrockLLMClient(
    model_id="anthropic.claude-3-5-sonnet-20241022-v2:0",
    region="us-east-1",     # optional; falls back to boto3 default
    max_retries=3,
)
```

- Calls `bedrock-runtime.invoke_model` directly.
- Hard-coded to Anthropic's message shape (the only thing on Bedrock that
  arc agents currently use). Adding Cohere/Llama support means a sibling
  client.
- Throttle handling: exponential back-off internally.
- Optional dep: `pip install 'arc-connectors[aws]'`

**Pick this when:** the deploy target is AWS, you already pay for Bedrock,
and minimum dependency surface matters (regulated environments often
prefer fewer transitive deps).

### `LiteLLMClient` — multi-provider via LiteLLM

```python
from arc.connectors import LiteLLMClient

llm = LiteLLMClient(
    model="anthropic/claude-3-5-sonnet-20241022",
    fallback_models=["openai/gpt-4o-mini"],   # tried on rate-limit / error
    max_retries=3,
    # api_base / api_key for self-hosted Ollama, LiteLLM proxy, etc.
)
```

- One interface, 100+ providers. The model string picks the backend:
  `anthropic/...`, `openai/...`, `bedrock/...`, `vertex_ai/...`,
  `ollama/llama3.1` (local), `azure/...`, `cohere/...`.
- Fallbacks built in — try a cheaper model when the primary is throttled.
- Optional dep: `pip install 'arc-connectors[litellm]'`. Provider creds
  in standard env vars (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, AWS for
  Bedrock, etc.) — LiteLLM handles auth.

**Pick this when:** you want provider portability, local models for
sandbox runs (Ollama), or fallback chains across providers.

Both clients have **identical surfaces** — switching between them in an
agent is just a one-line wiring change.

---

## How an agent uses it

```python
from arc.core import BaseAgent, FinancialEffect, LLMClient

class RetirementAgent(BaseAgent):
    def __init__(self, *args, llm: LLMClient | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.llm = llm                          # injected, optional

    async def execute(self, participant_id: str) -> dict:
        # … gather data, score risk …

        if self.llm is not None:
            text = await self.llm.generate(
                agent=self,                                       # ← required
                effect=FinancialEffect.INTERVENTION_DRAFT,        # ← in manifest
                intent_action="draft_intervention",
                intent_reason=f"Personalise message for {participant_id}",
                system="You are a retirement planning assistant…",
                prompt=f"Write a 2-sentence nudge for participant {participant_id}.",
                max_tokens=256,
            )
        else:
            text = self._template_fallback(participant_id)

        return {"body": text, "generated_by": type(self.llm).__name__ if self.llm else "template"}
```

**The agent code is identical regardless of provider.** Tests run with
`llm=None` (algorithmic path) or with a fake `LLMClient` that returns
canned text. The wiring of which provider gets injected is owned by
`LLMConfig` + the builder, not by the agent.

---

## Configuring which provider gets wired — `LLMConfig`

Hard-coding `BedrockLLMClient(...)` at the call site works for examples,
but in production you want one platform-wide default that every agent
inherits, with the option for a specific agent to override. That's what
`LLMConfig` is for.

```python
from arc.core import LLMConfig

# Declarative spec — same dataclass works for the platform default,
# the per-agent manifest override, and ad-hoc test wiring.
cfg = LLMConfig(
    provider="bedrock",
    model="anthropic.claude-3-5-sonnet-20241022-v2:0",
    region="us-east-1",
)
client = cfg.build_client()      # → BedrockLLMClient instance
```

`LLMConfig` knows how to build either shipped client. The builder
chooses based on `provider`:

| `provider`  | builds                                       |
|-------------|----------------------------------------------|
| `"bedrock"` | `BedrockLLMClient(model_id, region, …)`      |
| `"litellm"` | `LiteLLMClient(model, fallback_models, …)`   |
| `""`        | `None` — agent runs without an LLM           |

### The precedence stack

Three sources can supply an LLM. Higher wins:

```
explicit (with_llm)   >   manifest.llm   >   platform default   >   None
```

1. **`with_llm(client)`** on a builder — pre-built `LLMClient` passed
   programmatically. For tests and one-off scripts. Wins over everything.
2. **`manifest.llm`** — the agent's `manifest.yaml` declares an `llm:`
   block. Visible in the registry PR so compliance can review the
   provider/model. Lets one agent say *"I need GPT-4o for this use case"*
   even when the platform default is Bedrock.
3. **Platform default** — `RuntimeConfig.llm`, populated from
   `ARC_LLM_*` env vars at startup. The fallback every agent gets unless
   it overrides.
4. **None** — no LLM available. Agents that can run without one (template
   path, algorithmic path) do so; agents that require one should fail
   loudly during build.

`arc.core.resolve_llm` implements the stack — both `HarnessBuilder` and
`RuntimeBuilder` call it. You don't usually invoke it yourself.

### Platform default — env vars

`RuntimeConfig.from_env()` reads:

| Env var                    | Field             | Notes                                  |
|----------------------------|-------------------|----------------------------------------|
| `ARC_LLM_PROVIDER`         | `provider`        | `bedrock` / `litellm` / empty          |
| `ARC_LLM_MODEL`            | `model`           | provider-specific id                   |
| `ARC_LLM_REGION`           | `region`          | Bedrock; falls back to `AWS_REGION`    |
| `ARC_LLM_FALLBACK_MODELS`  | `fallback_models` | comma-separated, LiteLLM only          |
| `ARC_LLM_API_BASE`         | `api_base`        | LiteLLM proxy / self-hosted Ollama     |
| `ARC_LLM_MAX_RETRIES`      | `max_retries`     | integer, default `3`                   |

Example platform config (e.g. ECS task env):

```bash
ARC_LLM_PROVIDER=bedrock
ARC_LLM_MODEL=anthropic.claude-3-5-sonnet-20241022-v2:0
ARC_LLM_REGION=us-east-1
```

### Per-agent manifest override

Add an optional `llm:` block to the agent's `manifest.yaml`:

```yaml
agent_id: claims-triage
version: "0.1.0"
# … allowed_effects, data_access, …

llm:
  provider: litellm
  model:    openai/gpt-4o
  fallback_models:
    - anthropic/claude-3-5-sonnet-20241022
```

The block is optional and forward-compatible — unknown keys are ignored,
so a manifest written today won't break against a newer arc-core.

### Wiring it up — builders own the resolution

Production:

```python
from arc.runtime.builder import RuntimeBuilder
from arc.runtime.config  import RuntimeConfig

config = RuntimeConfig.from_env()        # reads ARC_LLM_* env vars
agent  = (
    RuntimeBuilder(config=config, manifest="manifest.yaml", policy="policy.yaml")
        .build(RetirementAgent)          # llm resolved from manifest > config
)
```

Harness (tests / sandbox):

```python
from arc.harness import HarnessBuilder
from arc.core    import LLMConfig

agent = (
    HarnessBuilder(
        manifest="manifest.yaml",
        policy="policy.yaml",
        llm_config=LLMConfig(provider="bedrock", model="…"),  # platform default
    )
    .build(RetirementAgent)              # uses manifest.llm if set, else llm_config
)
```

Override at call site (highest precedence — for tests):

```python
from arc.connectors import LiteLLMClient

agent = (
    HarnessBuilder(...)
        .with_llm(LiteLLMClient(model="ollama/llama3.1"))   # local model for sandbox
        .build(RetirementAgent)
)
```

---

## What's NOT in this layer

- **The LangGraph escape hatch.** Agents that use `langchain_aws.ChatBedrockConverse`
  inside LangGraph nodes bypass `LLMClient` entirely — those calls don't
  go through `run_effect()`. That's a real governance gap, tracked
  separately as a future "governed chat-model wrapper" feature.

- **Cost / token telemetry.** The audit log records effect / intent /
  prompt size / model. Detailed token counts and cost rollups belong in
  a future observability layer (LiteLLM has built-in callbacks for this;
  Bedrock surfaces token counts in the response — both can feed a shared
  sink).

- **Streaming.** Both clients return the full response string. Streaming
  agent outputs are a separate feature on the harness/runtime side.

---

## Writing your own client

To add a third backend (say a local HTTP server, or a vendor SDK that
LiteLLM doesn't cover):

1. Create a class in `arc.connectors.your_client.YourLLMClient`.
2. Implement `generate(self, *, agent, effect, ..., prompt, system, max_tokens, temperature, metadata)` and `generate_json` with the same shape as the two shipped clients.
3. Inside `generate`, call `await agent.run_effect(effect=..., tool="<provider>", action="<verb>", params=..., metadata={"llm_provider": "<provider>", ...}, exec_fn=...)` where `exec_fn` is your provider call.
4. Add a `runtime_checkable` test: `assert isinstance(YourLLMClient(...), LLMClient)`.

The Protocol is structural — there's no base class to inherit. Same
shape, you're in.

---

## Where to read next

- [Architecture diagrams](../architecture-diagrams.md#7-sequence-llm-call-with-policy)
  — diagram 7 shows the LLM call routing through `run_effect` step by step.
- [Effects](effects.md) — what `effect=` actually means.
- [Governance](governance.md) — what `run_effect()` does after an
  `LLMClient` call hands control to ControlTower.
- [`arc/packages/arc-connectors/src/arc/connectors/bedrock_llm.py`](../../arc/packages/arc-connectors/src/arc/connectors/bedrock_llm.py) and [`litellm_client.py`](../../arc/packages/arc-connectors/src/arc/connectors/litellm_client.py) — the two reference implementations.
