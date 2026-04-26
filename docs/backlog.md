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

**Why not now:** the platform's other Phase 3 features (anomaly
auto-demotion, harness improvements, more dashboard views) deliver value
to existing Python-only users; the multi-language story unblocks new
audiences only after we have a real demand signal. Capture it; revisit
when a non-Python team asks.

---

## How to add to this backlog

Append a new section above with: title, **the ask** (1–2 sentences), the
**lever** (1 sentence — the smallest architectural move that unlocks the
rest), **scope** (numbered list, not exhaustive), **effort estimate**,
**what this would unlock**, **what's NOT in scope**, and **why not now**.

Items don't need a ticket-number — this file is the log. Move resolved
items to a CHANGELOG when they land.
