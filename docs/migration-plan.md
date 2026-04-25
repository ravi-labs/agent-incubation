# Foundry → Arc Migration Plan

**Status:** Phase 2 COMPLETE. Phase 3 first feature (promotion pipeline) COMPLETE. Foundry-endgame slim COMPLETE — `agent-foundry/` is now a back-compat shim package only. Every file under `agent-foundry/src/foundry/` is a thin re-export of `arc.*`; the package's `pyproject.toml` declares the seven `arc-*` packages as runtime dependencies. The 16th outstanding module — `foundry.registry.catalog` — has been migrated to `arc.core.registry`. Durable artifacts that used to live under foundry have moved to top-level homes: `agent-foundry/policies/` → `policies/`, `agent-foundry/deploy/` → `deploy/`, `agent-foundry/docs/` → `docs/foundry-legacy/`. Tests and examples were deleted (arc has equivalents in `arc/packages/*/tests/` and `arc/agents/`).

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

## Exit criteria — when can foundry be deleted outright?

The platform decision (see "Endgame status" above) was to **keep `agent-foundry/`
as a back-compat shim package** rather than delete it. The original deletion
exit criteria are listed below for completeness; they are now satisfied — the
only reason `agent-foundry/` still exists is to keep `from foundry.X import Y`
imports working and to preserve `pip install 'agent-foundry[aws]'`-style
install aliases.

- [x] Every file under `agent-foundry/src/foundry/` contains only re-exports from `arc.*`
- [x] Every test in `agent-foundry/tests/` has an equivalent or replacement in `arc/packages/arc-*/tests/` (foundry tests deleted)
- [x] Every reference example in `agent-foundry/examples/` has been ported to `arc/agents/` (foundry examples deleted)
- [x] `arc-core` and `arc-harness` no longer declare `agent-foundry` as a dependency
- [x] `agent-team-template/` has been updated to import from `arc.*` instead of `foundry.*`
- [x] CI passes with `agent-foundry/` removed from the test command (foundry CI workflow deleted; arc tests cover the surface)
- [x] `git grep "from foundry"` and `git grep "import foundry"` return zero results outside `agent-foundry/` itself (modulo prose mentions in markdown)

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

## Endgame status

Foundry has been slimmed to a pure back-compat shim package:

- [x] Every file under `agent-foundry/src/foundry/` is a re-export from `arc.*`
- [x] Tests deleted from `agent-foundry/tests/` (arc has equivalents in `arc/packages/*/tests/`)
- [x] Reference examples deleted from `agent-foundry/examples/` (arc has equivalents in `arc/agents/`)
- [x] Vendored tollgate at `agent-foundry/src/foundry/tollgate/` deleted (canonical `tollgate/` is the only copy)
- [x] `agent-foundry/{docs,deploy,policies}` moved to top-level homes
- [x] `agent-foundry/.github/workflows/ci.yml` deleted (it ran the deleted test suite)
- [x] `agent-foundry/pyproject.toml` declares `arc-*` packages as runtime deps
- [x] `arc-core`, `arc-harness`, `arc-cli` no longer declare `agent-foundry` as a dependency
- [x] `agent-team-template/` imports from `arc.*`
- [x] `agent-registry/.github/workflows/ci.yml` imports from `arc.core.registry`
- [x] `git grep "from foundry"` and `git grep "import foundry"` outside `agent-foundry/` itself are zero (modulo doc strings and prose mentions in markdown)

Foundry continues to exist as a back-compat shim package so legacy
`from foundry.X import Y` imports keep working and `pip install 'agent-foundry[aws]'`
instructions in old docs / arc / tollgate error messages still resolve.

If at some future point you want to delete the package outright (because no
external consumer depends on it), `git rm -r agent-foundry/` is one safe
commit; the install-time aliases would need to be redirected to `arc-*`
extras first.
