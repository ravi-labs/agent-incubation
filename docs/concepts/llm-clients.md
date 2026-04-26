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

Caller side:

```python
# Bedrock
from arc.connectors import BedrockLLMClient
agent = RetirementAgent(manifest, tower, gateway, llm=BedrockLLMClient())

# LiteLLM
from arc.connectors import LiteLLMClient
agent = RetirementAgent(manifest, tower, gateway,
                        llm=LiteLLMClient(model="openai/gpt-4o"))

# No LLM — algorithmic path
agent = RetirementAgent(manifest, tower, gateway)
```

**The agent code is identical regardless of provider.** Only the wiring
differs. Tests run with `llm=None` (algorithmic path) or with a fake
`LLMClient` that returns canned text.

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

- [Effects](effects.md) — what `effect=` actually means.
- [Governance](governance.md) — what `run_effect()` does after an
  `LLMClient` call hands control to ControlTower.
- [`arc/packages/arc-connectors/src/arc/connectors/bedrock_llm.py`](../../arc/packages/arc-connectors/src/arc/connectors/bedrock_llm.py) and [`litellm_client.py`](../../arc/packages/arc-connectors/src/arc/connectors/litellm_client.py) — the two reference implementations.
