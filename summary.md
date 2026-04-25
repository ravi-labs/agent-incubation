# agent-incubation — session handoff summary

**Audience:** A Claude session opening this repo for the first time. Read this, then [docs/migration-plan.md](docs/migration-plan.md) for module-by-module detail.

**Date of last work:** 2026-04-26.

---

## 1. What this repo is

An **enterprise AI agent incubation platform** for regulated domains (financial services, healthcare, legal, ITSM, compliance). Every agent action is **declared in a manifest, evaluated by a policy engine, and audited** before it runs. Agents move through a 6-stage incubation pipeline: **DISCOVER → SHAPE → BUILD → VALIDATE → GOVERN → SCALE**.

Two coexisting code surfaces:

- **`arc/`** — the canonical platform. Multi-package monorepo. Native, self-contained, fully tested. **All new code goes here.**
- **`agent-foundry/`** — the legacy package. Source files are now all thin shims that re-export from `arc.*`. Kept alive so existing `from foundry.X import Y` imports keep working. Slated for slimming or deletion (decision pending — see §6).

Plus:
- **`tollgate/`** — vendored policy engine (canonical). Used by both arc and the foundry shims.
- **`agent-team-template/`** — starter template for new agent teams. Ports cleanly to `arc.*`.
- **`agent-registry/`** — central governance catalog (manifests only, no code).
- **`docs/`** — vision/research/marketing planning artifacts + migration plan.

---

## 2. Top-level layout

```
agent-incubation/
├── arc/                            ← THE PLATFORM (canonical, native)
│   ├── packages/
│   │   ├── arc-core/               governance engine: effects, manifest, BaseAgent, gateway,
│   │   │                           memory, tools, observability, lifecycle (incl. promotion pipeline)
│   │   ├── arc-harness/            sandbox testing layer
│   │   ├── arc-runtime/            production wiring + deploy adapters (Lambda, Bedrock, secrets)
│   │   ├── arc-cli/                `arc agent new/list/validate/promote/suspend` (legacy `foundry` script also works)
│   │   ├── arc-eval/               scenario-based evaluation framework
│   │   ├── arc-orchestrators/      LangGraph, AgentCore, Strands adapters + LangChain bridge
│   │   ├── arc-connectors/         Outlook, Pega, ServiceNow, Bedrock (KB, LLM, Guardrails, Agent client)
│   │   └── arc-platform/           reserved for Phase 3 web portals (empty)
│   ├── agents/                     7 reference agents — all native arc, no foundry imports:
│   │                               care-coordinator, contract-review, email-triage,
│   │                               fiduciary-watchdog, life-event-anticipation,
│   │                               plan-design-optimizer, retirement-trajectory
│   └── domains/                    placeholder dirs reserved for native domain extension points
├── agent-foundry/                  LEGACY (back-compat shim). 100% shims, no real source.
├── agent-registry/                 governance catalog
├── agent-team-template/            new-team starter (now points at arc-core / arc-harness)
├── tollgate/                       vendored canonical policy engine
└── docs/
    ├── migration-plan.md           ← READ THIS for module-by-module detail
    ├── vision/                     platform vision deck, project plan, eng overview
    ├── research/                   typed-effect-scopes paper
    └── marketing/                  competitive analysis, quickstart, interactive deck
```

---

## 3. Current state — test baselines

```
agent-foundry           284 / 284 pass     (tests run through the shim layer)
arc-core                290 + 1 skip pass  (effects, builder, manifest, base agent, gateway,
                                            memory, tools, observability, pipeline)
arc-harness              12 pass
arc-runtime              19 pass
arc-eval                 22 pass
─────────────────────────────────────
total arc native        331 + 1 skip pass

arc-orchestrators        43 pass + 3 fail  (3 failures are pre-existing boto3 missing,
                                            not migration-introduced — flagged separately)
```

**Zero `from foundry.X` imports outside `agent-foundry/` itself.** Every consumer (arc packages, arc/agents, agent-team-template) is on native `arc.*` / canonical `tollgate` imports.

**Zero arc packages declare `agent-foundry` as a runtime dep.** The arc tree stands alone.

---

## 4. What was accomplished (this and prior sessions)

A multi-session migration brought the codebase from "two parallel implementations pointing at each other" to "arc is canonical, foundry is shim." Highlights:

### Repo hygiene + foundation
- Reorg root: planning artifacts moved into `docs/`; runtime artifacts gitignored.
- Added arc/ to git (it had been untracked).
- Refreshed root [README.md](README.md) to describe both packages honestly.
- Wrote [docs/migration-plan.md](docs/migration-plan.md) with module-by-module recipe.

### Phase 2 — module migrations (15 of 15 complete)
Each module followed: copy from foundry → rewrite imports to native arc → make foundry path a re-export shim → copy tests with rewritten imports → verify both suites green.

| # | Module | Destination |
|---|---|---|
| 1 | effects (5 domain taxonomies + base) | `arc.core.effects` |
| 2 | EffectRequestBuilder | `arc.core.policy` |
| 3 | AgentManifest | `arc.core.manifest` |
| 4 | BaseAgent | `arc.core.agent` |
| 5 | harness sandbox layer | `arc.harness` |
| 6 | gateway (data access) | `arc.core.gateway` |
| 7 | memory (buffer + store) | `arc.core.memory` |
| 8 | tools (registry + governed_tool) | `arc.core.tools` |
| 9 | observability (tracker + audit report) | `arc.core.observability` |
| 10 | lifecycle stages | `arc.core.lifecycle` |
| 11 | CLI | new `arc-cli` package |
| 12 | deploy (Lambda, Bedrock, secrets) | `arc.runtime.deploy` |
| 13 | eval framework | new `arc-eval` package |
| 14 | LangChain + LangGraph integrations | `arc.orchestrators.{langchain, langgraph_agent}` |
| 15 | Bedrock integrations (KB, LLM, Guardrails, Agent client) | `arc.connectors.bedrock_*` |

### Phase 3 first feature — promotion pipeline
Built `arc.core.lifecycle.pipeline` (~370 LOC + 30 tests):
- `PromotionService` with `promote()` and `demote()`
- `GateChecker` with built-in primitives (`stage_order_check`, `evidence_field_check`, `artifact_exists_check`, `reviewer_present_check`, `predicate_check`)
- `PromotionDecision` (APPROVED / REJECTED / DEFERRED)
- `JsonlPromotionAuditLog` for persisted audit trail
- `require_human={LifecycleStage.SCALE}` to force human approval on production promotions even when automated gates pass

### Vendored-tollgate cleanup
Foundry shipped a complete copy of tollgate (~14K LOC, ~30 files) that was 99% byte-identical to canonical `tollgate/`. Replaced every leaf `.py` with a wildcard re-import shim of canonical tollgate. Net: -13K LOC. Class-identity unified — `foundry.tollgate.types.Effect IS tollgate.types.Effect`.

### Stray-import cleanup
After tollgate cleanup, bulk-rewrote every `from foundry.X` import inside `arc/` to native `arc.X` / canonical `tollgate.X`. Dropped `agent-foundry` from arc-core/arc-harness/arc-cli pyproject deps.

### Reference agents ported
All 7 example agents copied from `agent-foundry/examples/` to `arc/agents/` with:
- Native `arc.*` imports
- snake_case → kebab-case rename (matches arc convention)
- Smoke-tested: each imports cleanly without foundry installed

### agent-team-template updated
Two .py files rewritten to `arc.*`. `pyproject.toml` swapped `agent-foundry>=0.1.0` for `arc-core>=0.1.0` and `arc-harness>=0.1.0`. Description / Python version updated.

---

## 5. The dev environment

A venv at `/Users/ravichandrankanagasikamani/code/agent-incubation/.venv/` has all packages installed editable:

```
pip install -e tollgate/
pip install -e agent-foundry/[dev]
pip install -e arc/packages/arc-core/
pip install -e arc/packages/arc-harness/
pip install -e arc/packages/arc-runtime/
pip install -e arc/packages/arc-orchestrators/
pip install -e arc/packages/arc-connectors/
pip install -e arc/packages/arc-cli/
pip install -e arc/packages/arc-eval/
```

Scripts available: `arc` (new) and `foundry` (legacy, points at the same code).

To run tests:
```
cd agent-foundry && ../.venv/bin/python -m pytest tests/ -q     # foundry shim layer
.venv/bin/python -m pytest arc/packages/ -q                     # arc native (skip arc-orchestrators
                                                                # if you don't have boto3 — 3 known fails)
```

---

## 6. PENDING DECISION — what to do with `agent-foundry/`

This is the one open item. The user picked the broad direction ("focus on removing agent-foundry") but hasn't committed to one of three endgames:

**Option 1 — Full delete: `git rm -r agent-foundry/`.** Aggressive. Anyone with `from foundry.X import Y` in their own code breaks at next install.

**Option 2 — Slim back-compat shim package (recommended in handoff).** Delete `agent-foundry/{tests, docs, deploy, examples, policies}/` and `agent-foundry/src/foundry/tollgate/`. Keep `pyproject.toml` and the ~50 thin re-export shim files. Add explicit deps on the arc-* packages so installing `agent-foundry` pulls them in. External `from foundry.X` keeps working forever; disk shrinks ~5×.

**Option 3 — `git mv agent-foundry legacy/agent-foundry`.** Defer the decision. Hides from default discovery, near-zero cost.

**Recommendation made to user: Option 2.** Awaiting their confirmation.

---

## 7. Future roadmap (after the agent-foundry decision)

The user's original three goals from the start of the migration were:
1. **Move foundry to arc** ✅ DONE (Phase 2)
2. **Improve arc as a true harness layer** — migration done (module 5); the *improvements* (deterministic LLM replay, time-travel debugging, snapshot diffing, golden-output regression tests) are still ahead
3. **Build incubation + promotion pipeline** — first feature done (`PromotionService`); the rest is still ahead

Concrete next features once agent-foundry is resolved:

- **Anomaly auto-demotion.** Watcher loop on `OutcomeTracker` JSONL streams. When metrics drift past thresholds, automatically calls `service.demote(...)`. Likely module: `arc.core.lifecycle.anomaly`.
- **Approval queue handoff for `DEFERRED` decisions.** Today the pipeline produces a DEFERRED decision and stops. Wire it to Tollgate's `AsyncQueueApprover` so a human approval resumes the promotion atomically.
- **Manifest write-back.** Today `service.promote()` returns a decision; the caller has to apply `manifest.lifecycle_stage = decision.request.target_stage`. Build a `ManifestStore` that persists stage transitions and synchronizes with the `agent-registry/` catalog.
- **Harness improvements** the user explicitly called out:
  - Deterministic LLM replay (record/replay Bedrock calls)
  - Time-travel debugging (step backward through ControlTower decisions)
  - Snapshot diffing across runs
  - Golden-output regression tests
- **`arc-platform`** (currently empty placeholder package) — Phase 3 web portals: agent inventory, lifecycle dashboard, audit viewer, pipeline operator UI.

---

## 8. Useful entry points for a fresh session

- Root [README.md](README.md) — public-facing intro
- [docs/migration-plan.md](docs/migration-plan.md) — module-by-module migration history with status table
- [agent-foundry/docs/platform-architecture.md](agent-foundry/docs/platform-architecture.md) — original architecture writeup (still accurate, references foundry paths but the concepts apply to arc identically)
- [arc/packages/arc-core/src/arc/core/__init__.py](arc/packages/arc-core/src/arc/core/__init__.py) — public arc API surface
- [arc/packages/arc-core/src/arc/core/lifecycle/pipeline.py](arc/packages/arc-core/src/arc/core/lifecycle/pipeline.py) — promotion pipeline (Phase 3 first feature)
- [arc/agents/email-triage/](arc/agents/email-triage/) — most complete reference agent (uses graph.py + LangGraph orchestrator + harness fixtures)
- `git log --oneline | head -30` — recent commit history is detailed and tells the migration story

---

## 9. Last commit on `main`

`bb9aa99 refactor(arc,team-template): purge stray foundry imports + port all 7 reference agents`

Working tree is clean. `main` is in sync with `origin/main`.
