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

## 🟡 Streaming LLM responses through governance

**The ask.** Today both `BedrockLLMClient.generate` and
`LiteLLMClient.generate` return the full response string after the
provider call completes. `GovernedChatModel._agenerate` does the same.
Some agent shapes — chat-style UIs, long-form summarisers, anything
where time-to-first-token is part of the UX — want streaming. Add
`astream` (LangChain) / a streaming variant on `LLMClient` that yields
tokens as they arrive while still routing the call through
`run_effect`.

**The lever.** Decide how the audit row fits a multi-chunk response.
Two viable shapes:

  - **Audit-on-completion** — emit one row at end-of-stream with the
    full token count and latency. Policy still fires before any
    token is yielded; DENY raises before streaming starts. Simplest;
    matches the current "decide once, audit once" model. Cost is
    that the audit row only lands after all tokens are consumed.
  - **Pre + post audit** — emit a "stream-start" row at decision time
    (with provider/model/prompt size) and a "stream-end" row with
    token count + latency. Two rows per call; downstream queryers
    correlate by request id. More accurate for long streams; more
    plumbing.

Audit-on-completion is the right v1 — keeps the data shape consistent
with non-streaming calls.

**Scope.**
1. Add `astream` on `LLMClient` Protocol (optional method) plus the
   two shipped clients. Bedrock has `invoke_model_with_response_stream`;
   LiteLLM has `acompletion(stream=True)`.
2. Add `_astream` on `GovernedChatModel`. Wrap the wrapped model's
   `_astream`; emit the audit row when the stream closes (success
   or failure).
3. Capture token count + latency in the audit metadata at stream-end
   alongside the existing `llm_provider` / `llm_model` / `prompt_chars`.
4. Tests: a fake streaming chat model + fake agent verifying (a) DENY
   never starts the stream, (b) ALLOW yields chunks correctly, (c) the
   audit row lands once at completion with the right metadata.

**Effort estimate:** small-medium — ~1 day. The streaming path inside
each provider is well-trodden; the work is fitting the audit row to
match.

**What this would unlock:** chat-style UIs (Pega chatbot, internal
copilot tools), long-form generation (legal summaries, advisor
narrative drafts), anything where time-to-first-token matters.

**What's NOT in scope:** mid-stream interruption (cancel after token
N — the wrapped model's responsibility, surfaced through normal
asyncio cancellation); structured-output streaming (`with_structured_output`
fundamentally needs the complete response to parse the tool call —
streaming doesn't apply).

**Why not now:** none of the seven reference agents need it. The two
that use real LLMs today (retirement-trajectory, email-triage) both
want the *complete* response — retirement-trajectory drafts a single
message, email-triage uses `with_structured_output` which can't
stream by design. Building streaming without a forcing function risks
shipping something that drifts before its first real user. Capture
the design now, build when an agent is in flight that actually
benefits.

---

## 🟡 In-flight run cancellation (force-stop a currently-executing run)

**The ask.** arc has two interrupt mechanisms today: ASK gates (the
agent pauses on consequential effects, human approves/rejects) and
agent-level Suspend (halts new runs, leaves in-flight runs alone). What
*isn't* there is a third rung — **force-stop a single run that's
currently executing**, mid-step, before its next gate fires. Today the
only way to cancel an in-flight run is wait for the next ASK gate and
reject it.

**The lever.** A per-run cancellation flag that the agent
*cooperatively* checks at each `run_effect` call. Combined with a
``RunRegistry`` mapping `run_id → state` and a `POST /api/runs/{id}/cancel`
endpoint that flips the flag, this gives ops on-call a "Cancel run"
button on the live page that surfaces in real time. The agent raises
``RunCancelled`` at the next checkpoint; its calling code logs and
moves on.

**Scope.**
1. Add ``arc.core.RunRegistry`` — in-memory + JSONL-backed implementations
   tracking ``{run_id, agent_id, started_at, status}``. Status is
   ``running`` / ``cancelled`` / ``completed``.
2. Each run gets a ``run_id`` (UUID) on entry; ``BaseAgent.execute``
   registers it. The registry instance is injected like the audit sink.
3. ``BaseAgent.run_effect`` peeks at the registry before each call;
   if the flag is ``cancelled``, raises ``RunCancelled``. **Cooperative,
   not preemptive** — no asyncio task cancellation, no thread kill.
4. Add ``POST /api/runs/{run_id}/cancel`` endpoint with body
   ``{ reviewer, reason }``. Writes to registry; audit row records
   the cancellation.
5. Add ``GET /api/agents/{id}/runs/in-flight`` to list active runs
   for the live page.
6. Add an "In-flight runs" pane to ``AgentLive.tsx`` listing each
   run with its current step + a Cancel button.
7. Tests: cancellation fires before next ``run_effect``, audit row
   lands, calling agent code can catch ``RunCancelled``.

**Effort estimate:** ~3 days. None of the pieces are research-grade;
it's just plumbing across arc-core (registry), arc-platform (endpoint
+ pane), and BaseAgent (the check).

**What this would unlock:**
- A "kill this specific email's processing" button — narrower than
  Suspend, faster than waiting for the next ASK to reject.
- Mid-incident triage — "agent is misclassifying everything; cancel
  all in-flight runs immediately" as a kill switch finer than the
  agent-level Suspend.
- Cleaner deploy semantics — graceful cancel of in-flight runs before
  worker shutdown.

**What's NOT in scope:**
- Preemptive cancellation (asyncio task kill, thread interrupt). The
  agent must stop *cooperatively* at a gate; if the agent is mid-LLM-
  call to a 30-second model, the cancel takes effect when that call
  returns. The right tradeoff for arc — preemption breaks audit
  invariants.
- Mid-graph state mutation ("pause at classify_node, change the
  case_type to X, resume"). LangGraph already supports this via
  ``interrupt_before`` + ``update_state``; surfacing it is a separate
  feature, scoped on its own.

**Why not now:** ASK rejection covers ~80% of the "stop this run"
need today, and the email-triage pilot hasn't yet hit a scenario where
ASK-reject is too slow. Build it when ops on-call asks for the button
during a real incident — that's the forcing function.

---

## How to add to this backlog

Append a new section above with: title, **the ask** (1–2 sentences), the
**lever** (1 sentence — the smallest architectural move that unlocks the
rest), **scope** (numbered list, not exhaustive), **effort estimate**,
**what this would unlock**, **what's NOT in scope**, and **why not now**.

Items don't need a ticket-number — this file is the log. Move resolved
items to a CHANGELOG when they land.
