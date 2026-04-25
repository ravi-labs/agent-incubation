# Foundry → Arc Migration Plan

**Status:** Phase 1 (planning + scaffolding) complete. Phase 2 (per-module migration) not yet started.

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

## Exit criteria — when can foundry be deleted?

All of:

- [ ] Every file under `agent-foundry/src/foundry/` is either deleted or contains only re-exports from `arc.*`
- [ ] Every test in `agent-foundry/tests/` has an equivalent or replacement in `arc/packages/arc-*/tests/`
- [ ] Every reference example in `agent-foundry/examples/` has been ported to `arc/agents/` (or deleted as redundant)
- [ ] `arc-core` and `arc-harness` no longer declare `agent-foundry` as a dependency
- [ ] `agent-team-template/` has been updated to import from `arc.*` instead of `foundry.*`
- [ ] CI passes with `agent-foundry/` removed from the test command
- [ ] `git grep "from foundry"` and `git grep "import foundry"` both return zero results outside `agent-foundry/` itself

When that checklist is complete: `git rm -r agent-foundry/` becomes a single, safe commit.

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
| 1 | effects | not started | — | yes |
| 2 | builder | not started | — | yes |
| 3 | manifest | not started | — | yes |
| 4 | base agent | not started | — | yes |
| 5 | harness | not started | — | yes |
| 6 | gateway | not started | — | yes |
| 7 | memory | not started | — | yes |
| 8 | tools | not started | — | yes |
| 9 | observability | not started | — | yes |
| 10 | lifecycle | not started | — | yes |
| 11 | cli | not started | — | yes |
| 12 | deploy | not started | — | yes |
| 13 | eval | not started | — | yes |
| 14 | langchain integration | not started | — | yes |
| 15 | bedrock integrations | not started | — | yes |
