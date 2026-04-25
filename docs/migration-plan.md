# Foundry → Arc Migration Plan

**Status:** MIGRATION COMPLETE. `agent-foundry/` has been deleted outright. All 16 modules live natively in `arc-*` packages (15 from Phase 2 + the registry catalog from the foundry-endgame pass). Every install-instruction string and docstring across the codebase has been retargeted from `pip install 'agent-foundry[X]'` and `from foundry.X` to the equivalent `arc-*` extras and `arc.*` paths. The Phase 3 first feature (promotion pipeline) is in `arc.core.lifecycle.pipeline`. Durable artifacts that used to live under foundry have moved to top-level homes: `agent-foundry/policies/` → `policies/`, `agent-foundry/deploy/` → `deploy/`, `agent-foundry/docs/` → `docs/foundry-legacy/`.

**Goal:** Move all production functionality from `agent-foundry/src/foundry/` into the appropriate `arc/packages/arc-*/src/arc/...` package, then delete `agent-foundry/`.

---

## Why migrate

Today `arc/` is mostly an import shell — `arc.core`, `arc.harness`, and parts of `arc.runtime` re-export from `foundry.*`. That gives us a clean *target* API surface but leaves all real code in foundry. Two parallel codebases drift, double the maintenance, and confuse contributors about what's canonical.

Arc is the future packaging surface for the platform. Foundry is the current implementation. The migration moves us from "two codebases pointing at each other" to "one codebase, organized as a multi-package monorepo, with a clear story for each domain."

---

## Shim strategy (the key architectural decision)

**During migration, every committed state must keep both foundry's tests AND arc's tests green.** That means we can't just move code in flag-day fashion. We use a *reverse-import shim*: once a module migrates from foundry to arc, foundry's old module becomes a thin re-export of arc's new module.

Example — after migrating `policy/effects.py`:

- New canonical home: `arc/packages/arc-core/src/arc/core/effects.py`
- Foundry shim: `agent-foundry/src/foundry/policy/effects.py` becomes:
  ```python
  # Migrated to arc-core. Kept as a shim so existing foundry imports keep working.
  from arc.core.effects import *  # noqa: F401, F403
  from arc.core.effects import FinancialEffect, EffectTier, ...  # explicit for IDEs
  ```

This way:
- `from foundry.policy.effects import FinancialEffect` keeps working everywhere it's currently used (foundry tests, examples, agent-team-template, agent-registry)
- `from arc.core.effects import FinancialEffect` is the new canonical path
- Both reference the same code (the arc one); no drift possible
- Foundry shrinks one module at a time; we can delete it the day every shim file is empty

**Until the migration is complete, `arc-core` and `arc-harness` declare `agent-foundry` as a dependency** (so installs work). Each migrated module is one less reason to depend on foundry; the dep gets dropped when the last shim disappears.

### Class-identity caveat

Foundry currently has its own vendored copy of tollgate at `agent-foundry/src/foundry/tollgate/`, which defines `Effect`, `Decision`, etc. as separate classes from the canonical `tollgate/` package at the repo root. They have identical shapes but different `id()` — so `isinstance(x, foundry.tollgate.types.Effect)` returns False if `x` was constructed from `tollgate.types.Effect`.

To preserve identity for foundry's existing `isinstance()` checks during migration, **arc.core.effects imports `Effect` from `foundry.tollgate.types`**, not from canonical `tollgate.types`. This is tracked as a separate cleanup: convert `foundry/tollgate/` itself into a shim that re-exports from canonical `tollgate`. After that, arc.core.effects switches to canonical `tollgate.types`.

### arc.core init must be circular-safe

When the foundry shim does `from arc.core.effects import …`, Python loads `arc.core.__init__.py` first. If that init eagerly imports from `foundry.policy.builder` (which is mid-init through the same chain), you get an `ImportError` for partially-initialized modules.

The fix is in `arc.core.__init__.py`: foundry-backed re-exports are loaded lazily via PEP 562 `__getattr__`, not at import time. Native arc imports (currently just `arc.core.effects`) stay eager. This preserves the public API and avoids the circular.

---

## Module mapping

| Foundry path | Arc destination | Order | Notes |
|---|---|---|---|
| `policy/effects.py` + 4 domain effect files | `arc-core/effects/` | **1** | Pure data, smallest blast radius |
| `policy/builder.py` | `arc-core/policy/builder.py` | **2** | Depends on effects |
| `scaffold/manifest.py` | `arc-core/manifest.py` | **3** | Depends on effects |
| `scaffold/base.py` | `arc-core/agent.py` | **4** | Depends on manifest, tollgate |
| `scaffold/__init__.py` | `arc-core/__init__.py` (already exports) | **4** | Re-exports only |
| `harness/` (entire dir) | `arc-harness/src/arc/harness/` | **5** | Replaces 37-line stub |
| `gateway/` | `arc-core/gateway/` | **6** | Used by harness fixtures + runtime connectors |
| `memory/` | `arc-core/memory/` | **7** | Independent module |
| `tools/` | `arc-core/tools/` | **8** | Independent module |
| `observability/` | `arc-core/observability/` | **9** | Audit sink, tracker, HTML report |
| `lifecycle/` | `arc-core/lifecycle/` | **10** | Stage definitions; pipeline logic added in Phase 3 |
| `cli/` | new `arc-cli` package | **11** | `arc agent new/list/validate/promote/suspend` |
| `deploy/` | `arc-runtime/deploy/` | **12** | Lambda handler, Bedrock secrets |
| `eval/` | new `arc-eval` package | **13** | Eval framework |
| `integrations/langchain*` | `arc-orchestrators/langchain.py` | **14** | Bridge to arc-orchestrators |
| `integrations/bedrock_*` | `arc-connectors/bedrock_*` | **15** | LLM client, KB, Guardrails, Agent client |
| `tollgate/` (vendored at root) | stays vendored at repo root | — | Both foundry and arc import from it |

---

## Migration recipe (per module)

This is the procedure to follow for each row in the table above. Each module = one PR / one commit.

1. **Copy** the source file(s) from `agent-foundry/src/foundry/<old_path>` to `arc/packages/arc-<pkg>/src/arc/<new_path>`
2. **Update imports inside the copied code** so internal references (e.g. `from foundry.policy.effects import X`) become `from arc.core.effects import X`
3. **Add exports** to the relevant `arc/packages/arc-<pkg>/src/arc/<pkg>/__init__.py`
4. **Replace foundry source with a shim** (see "Shim strategy" above) — the file stays at the same foundry path, but its body becomes a re-export from arc
5. **Run foundry's tests** — must stay green (the shim makes this work)
6. **Add tests in arc** — copy or adapt foundry's tests for this module into `arc/packages/arc-<pkg>/tests/`
7. **Run arc's tests** — must pass
8. **Drop the foundry dep** from `arc-<pkg>/pyproject.toml` if no other arc code in that package still imports from foundry
9. **Commit** with message `refactor(<pkg>): migrate <module> from foundry to arc-<pkg>`

---

## Original deletion exit criteria — all satisfied

These were the criteria from when foundry was being slimmed to a shim. They
are kept here for historical record; foundry has now been deleted outright.

- [x] Every file under `agent-foundry/src/foundry/` is gone
- [x] Every test in `agent-foundry/tests/` has an equivalent or replacement in `arc/packages/arc-*/tests/`
- [x] Every reference example in `agent-foundry/examples/` has been ported to `arc/agents/`
- [x] No arc package declares `agent-foundry` as a dependency
- [x] `agent-team-template/` imports from `arc.*` instead of `foundry.*`
- [x] CI passes without `agent-foundry/` in the test command
- [x] `git grep "from foundry"` and `git grep "import foundry"` return zero results
      outside `docs/foundry-legacy/` (intentional historical content)

---

## Out of scope (Phase 3, after migration)

The following are *not* part of the migration. They get built natively in arc *after* the migration completes:

- **True harness improvements** — deterministic LLM replay, time-travel debugging, snapshot diffing across runs, golden-output regression tests. Built on top of the migrated `arc-harness` (cleaner than building on a re-export shell).
- **Real incubation + promotion pipeline** — stage-transition checks as code, automated promotion gate evaluation, auto-demotion on anomaly, approval workflows, audit trail of every transition. Replaces today's 188-line `lifecycle/stages.py`.
- **arc-platform** (Phase 3) — web portals for the pipeline.

---

## Status tracker

Update this section as modules migrate.

| # | Module | Status | Commit | Foundry shim still present? |
|---|---|---|---|---|
| 1 | effects | **migrated** | 4b8f3f4 | yes |
| 2 | builder | **migrated** | ea68237 | yes |
| 3 | manifest | **migrated** | 0997b63 | yes |
| 4 | base agent | **migrated** | 4009b91 | yes |
| 5 | harness | **migrated** | 1016ee9 | yes |
| 6 | gateway | **migrated** | 0ed0414 | yes |
| 7 | memory | **migrated** | 26a0481 | yes |
| 8 | tools | **migrated** | 26a0481 | yes |
| 9 | observability | **migrated** | 26a0481 | yes |
| 10 | lifecycle | **migrated** | 33e6d23 | yes |
| 11 | cli | **migrated** | 00a2d6c | yes |
| 12 | deploy | **migrated** | 00a2d6c | yes |
| 13 | eval | **migrated** | 00a2d6c | yes |
| 14 | langchain integration | **migrated** | b1d3e8a | yes |
| 15 | bedrock integrations | **migrated** | b1d3e8a | yes |
| 16 | registry catalog | **migrated** | (this commit) | yes |

## Endgame status — deletion complete

`agent-foundry/` no longer exists. The deletion pass (commit history below)
cleared the last cosmetic dependencies on the package and removed the
directory wholesale:

- [x] Every `pip install 'agent-foundry[X]'` string in arc and tollgate retargeted
      to the equivalent extra: `arc-core[aws|http]`, `arc-connectors[aws]`,
      `arc-orchestrators[langchain|langgraph]`, `arc-runtime[aws]`, `tollgate[aws]`.
- [x] `arc-core`, `arc-connectors`, `arc-orchestrators`, `arc-runtime` and
      `tollgate` got the missing `[aws]` / `[http]` / `[langchain]` extras
      added to their pyprojects so those install instructions actually resolve.
- [x] `from foundry.X` import examples in `deploy/bedrock-agent-core.md`
      rewritten to `arc.*`.
- [x] `deploy/cdk/foundry_stack.py` renamed to `deploy/cdk/arc_stack.py`;
      `FoundryAgentStack` → `ArcAgentStack`; resource-name prefixes
      `foundry-{agent}` → `arc-{agent}`; tags `foundry:X` → `arc:X`;
      env vars `FOUNDRY_X` → `ARC_X` across CDK + Lambda handler + Dockerfile +
      ECS task def.
- [x] `foundry_event` magic key in the SQS approver envelope renamed to
      `arc_event` (with the matching consumer + test updates).
- [x] Marketing-facing references in `agent-registry/README.md`,
      `agent-team-template/README.md`, `tollgate/README.md`, root `README.md`
      and reference agents updated from `agent-foundry` to `arc`.
- [x] `agent-registry/.github/workflows/ci.yml` installs arc packages from
      source instead of `pip install agent-foundry`.
- [x] `git grep "from foundry"` and `git grep "agent-foundry"` outside
      `docs/foundry-legacy/`, `docs/migration-plan.md`, and the marketing
      HTML/PPTX (intentionally retained as historical artifacts) are zero.
- [x] `agent-foundry/` deleted via `git rm -r`.

What stays:

- `docs/foundry-legacy/` — the original architecture/quickstart docs, kept
  as historical reference. The banner notes that paths reference the now-
  deleted `foundry.*` namespace and that concepts map 1:1 to `arc.*`.
- `docs/marketing/competitive-analysis.html` and
  `docs/vision/platform-vision.pptx` — written under the original brand
  name. Branding decisions are out of scope for this code migration.
