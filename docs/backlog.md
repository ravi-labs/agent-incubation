# Arc — backlog

Items captured but deliberately not started. Each entry should be small enough
to brief a fresh contributor in a paragraph and decide "do we want this now?"

Status legend:
- **🟡 idea** — captured; needs design / ADR before any code
- **🟢 ready** — design done; ready to schedule
- **🔵 in flight** — being worked on
- **⚪️ done** — landed (move to a CHANGELOG and remove from here)

---

## 🟡 ADR + design for language-agnostic agent runtime

**The ask.** Today an arc agent is a Python class that subclasses `BaseAgent`
and calls `self.run_effect(...)` in-process. Teams writing in Java, Go, or
Node can't plug into the platform without rewriting in Python or embedding
a Python interpreter. Make arc language-agnostic so any service can speak
the same manifest + tollgate + lifecycle protocol.

**The lever.** Extract Tollgate (`ControlTower.evaluate`) into a wire
protocol; everything else follows.

**Scope of the design pass:**

1. **Wire protocol for ControlTower.** gRPC vs REST+JSON. Stable schema
   for `Intent`, `ToolRequest`, `Decision`, audit events. Backwards
   compatibility plan.
2. **Tollgate as a service.** Reference Python implementation runs as a
   sidecar / standalone server. Auth + transport (mTLS? signed tokens?)
   Performance budget — a tier-1 effect call should cost <5 ms over the
   wire on the same host.
3. **Manifest as JSON Schema.** Today the schema lives implicitly in
   `arc.core.manifest.load_manifest`. Publish it so non-Python tools can
   validate manifests without a Python dependency.
4. **Per-language thin client SDKs.** Java + Go + Node initially. Each
   serializes intents to the wire protocol; none re-implement policy
   logic. Reference: 200–500 LOC per SDK.
5. **Lifecycle pipeline stays Python.** `PromotionService`, `ManifestStore`,
   approval queue — all ops tooling. Not on the runtime hot path. No need
   to port.
6. **Reference non-Python agent.** Ship one Go or Node sample under
   `arc/agents/` to prove the boundary works end-to-end.

**Effort estimate:** medium — ADR + protocol design is the hard part
(maybe 1 week). Per-language SDKs are mechanical after that.

**What this would unlock:** any team / service in the org can publish an
agent into the same registry and submit it through the same compliance
pipeline, regardless of language. Removes Python as a hidden requirement
of being on the platform.

**What's NOT in scope:** auto-generating SDK from the protocol (nice to
have later); production-grade auth (separate ADR); migration tooling for
existing Python agents (none needed — they keep using the in-process path).

**Why not now:** the platform's other Phase 3 features (harness
improvements, more dashboard views) deliver value to existing Python-only
users; the multi-language story unblocks new audiences only after we have
a real demand signal. Capture it; revisit when a non-Python team asks.

---

## 🟡 LangGraph governance gap — `governed_chat_model` adapter

**The ask.** When an agent uses LangChain's `ChatBedrockConverse` (or any
`BaseChatModel`) inside a LangGraph node, the LLM call **bypasses
`run_effect`**. ControlTower never sees the prompt, the audit log
records nothing about the model invocation (only any subsequent
business effect the node logs after the call), and policy can't gate
it. `LLMClient` solves this for agents that call a model directly, but
LangGraph nodes consume the LangChain `Runnable` contract — `LLMClient`
is not a drop-in substitute. Concrete evidence:
[`arc/agents/email-triage/graph.py:274`](../arc/agents/email-triage/graph.py).

**The lever.** A `governed_chat_model(llm: LLMClient, agent: BaseAgent,
default_effect=...)` factory that returns a LangChain `BaseChatModel`
whose `_agenerate` / `ainvoke` routes through `agent.run_effect` first,
then delegates to a real chat model (`ChatLiteLLM`, `ChatBedrockConverse`,
etc.). Drop-in replacement for `ChatBedrockConverse` inside LangGraph
nodes.

**Scope of the design pass:**

1. **Adapter contract.** Subclass `langchain_core.language_models.BaseChatModel`.
   Override `_agenerate`/`_generate` to wrap the upstream call in
   `agent.run_effect(effect=..., tool="...", action="completion",
   exec_fn=<delegate>)`. Preserve `with_structured_output`, `bind_tools`,
   `.astream` by delegating to the wrapped model where possible.
2. **Effect resolution.** Each node call needs a domain effect. Options:
   per-node override (`governed_chat_model(..., effect=ITSMEffect.EMAIL_CLASSIFY)`),
   or a default at adapter construction. Probably both.
3. **Agent reference plumbing.** LangGraph nodes don't have direct agent
   access. Either add `agent` to the `AgentState` TypedDict (in
   `arc.orchestrators.langgraph_agent`) or close over it at node
   construction. ADR should pick one.
4. **Streaming + structured output.** When the wrapped model supports
   them, the adapter forwards. Each chunk on `.astream` doesn't get its
   own audit row — the audit covers the whole call. Document this trade.
5. **Where it lives.** `arc.orchestrators.langchain.governed_chat_model`
   (alongside `ArcTool` / `ArcToolkit` / `ArcRunnable`).
6. **Migration recipe for email-triage.** Replace `ChatBedrockConverse`
   construction with `governed_chat_model(LiteLLMClient(...), agent,
   effect=ITSMEffect.EMAIL_CLASSIFY)` in `graph.py`. Delete the
   post-hoc `agent.run_effect(exec_fn=lambda: result)` since the LLM
   call itself is now governed. Update the eval scenarios so audit-row
   counts match.

**Effort estimate:** medium — 200–400 LOC + tests. The hard part is
preserving `with_structured_output` / `bind_tools` parity through the
wrapper; the actual `run_effect` integration is straightforward.

**What this would unlock:** LangGraph agents get the same governance
guarantees as direct-call agents. The audit log captures
`llm_provider` / `llm_model` / `prompt_chars` / token estimates on
every model invocation across both call patterns — uniform telemetry.

**What's NOT in scope:** generic LLM-marker effect (`LLM_INVOKE`) — out
of scope here; can be discussed separately if policies want to target
all LLM calls regardless of business effect. Tool-calling agents
(LangChain `bind_tools`) — adapter forwards as-is; deeper integration
where tool calls themselves get governed is a follow-up.

**Why not now:** the direct-call path (`LLMClient.generate`) covers the
two reference agents that use real models in production today
(retirement-trajectory, anything new written under the LLMClient
pattern). LangGraph nodes are mostly used for orchestration shape, not
as the primary LLM call site. Capture and revisit when a LangGraph
agent becomes a production-critical compliance concern.

---

## How to add to this backlog

Append a new section above with: title, **the ask** (1–2 sentences), the
**lever** (1 sentence — the smallest architectural move that unlocks the
rest), **scope** (numbered list, not exhaustive), **effort estimate**,
**what this would unlock**, **what's NOT in scope**, and **why not now**.

Items don't need a ticket-number — this file is the log. Move resolved
items to a CHANGELOG when they land.
