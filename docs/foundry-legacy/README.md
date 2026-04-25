# Agent Foundry — Documentation (historical reference)

> **Status:** These docs were written when `agent-foundry` was the canonical
> implementation. The codebase has since been rewritten as `arc/`, and the
> `agent-foundry/` package no longer exists. These docs still reference
> `from foundry.X import Y` paths — every one of them maps 1:1 to an
> `arc.*` path:
>
> | Old (deleted) | New (canonical) |
> |---|---|
> | `foundry.scaffold.base.BaseAgent` | `arc.core.BaseAgent` |
> | `foundry.scaffold.manifest.AgentManifest` | `arc.core.AgentManifest` |
> | `foundry.policy.effects.FinancialEffect` | `arc.core.FinancialEffect` |
> | `foundry.policy.builder.EffectRequestBuilder` | `arc.core.EffectRequestBuilder` |
> | `foundry.gateway.*` | `arc.core.gateway.*` |
> | `foundry.memory.*` | `arc.core.memory.*` |
> | `foundry.tools.*` | `arc.core.tools.*` |
> | `foundry.observability.*` | `arc.core.observability.*` |
> | `foundry.lifecycle.*` | `arc.core.lifecycle.*` |
> | `foundry.harness.*` | `arc.harness.*` |
> | `foundry.deploy.*` | `arc.runtime.deploy.*` |
> | `foundry.eval.*` | `arc.eval.*` |
> | `foundry.integrations.langchain` | `arc.orchestrators.langchain` |
> | `foundry.integrations.langgraph` | `arc.orchestrators.langgraph_agent` |
> | `foundry.integrations.bedrock_*` | `arc.connectors.bedrock_*` |
> | `foundry.registry.catalog` | `arc.core.registry` |
>
> The concepts (effects, manifests, BaseAgent, ControlTower, lifecycle) all
> carry over identically; only the import path changed.
>
> For the canonical entry point, see the root [README](../../README.md) and
> [docs/migration-plan.md](../migration-plan.md).

---

> Enterprise agent incubation platform: from idea to governed production agent on AWS.

---

## Guides

| Document | Description |
|----------|-------------|
| [Quick Start Guide](./quickstart.md) | End-to-end walkthrough covering all six core capabilities |
| [Engineering Overview](./engineering-overview.md) | Architecture, module inventory, AWS alignment — for team onboarding |

## Reference

| Document | Description |
|----------|-------------|
| [FinancialEffect Reference](./effects-reference.md) | All 28+ declared effects, tiers, default decisions, and usage examples |
| [Memory & Tool Registry](./memory-and-tools.md) | `ConversationBuffer`, `FoundryMemoryStore`, `@governed_tool`, `ToolRegistry` |
| [Evals & Guardrails](./evals-and-guardrails.md) | `FoundryEvaluator`, `EvalScenario`, `BedrockGuardrailsAdapter`, `GuardrailsMixin` |
| [Gateway & Integrations](./gateway-and-integrations.md) | `HttpGateway`, `MultiGateway`, LangChain, LangGraph, Bedrock Agent client, Lambda |

---

## At a Glance

```
pip install 'agent-foundry[enterprise]'

from foundry.scaffold.base     import BaseAgent
from foundry.scaffold.manifest import AgentManifest, LifecycleStage
from foundry.policy.effects    import FinancialEffect

class MyAgent(BaseAgent):
    async def execute(self, participant_id: str, **kwargs) -> dict:
        score = await self.run_effect(
            effect        = FinancialEffect.RISK_SCORE_COMPUTE,
            tool          = 'scorer',
            action        = 'compute',
            params        = {'participant_id': participant_id},
            intent_action = 'compute_score',
            intent_reason = 'Assess retirement readiness',
        )
        return {'risk_score': score}
```

Every agent built on Foundry inherits:

- **Policy enforcement** — every action is ALLOW / ASK / DENY before it executes
- **Audit trail** — every effect invocation is logged with agent ID, intent, and decision
- **Human review queue** — ASK decisions are routed to a review workflow automatically
- **Lifecycle management** — SANDBOX → STAGING → PRODUCTION with eval gates at each stage
- **LangChain / LangGraph compatibility** — `invoke`, `ainvoke`, `stream`, `astream`, `|` pipe operator out of the box
- **AWS-native deployment** — Lambda handler, DynamoDB memory, Bedrock Guardrails, Bedrock Agent invocation

---

*Agent Foundry · v0.1.0 · March 2026*
