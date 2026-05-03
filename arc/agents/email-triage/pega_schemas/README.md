# Pega case-type schemas — retirement plan email triage

This directory holds **per-case-type schema YAML files** used by the
email-triage agent's Pega router (see [`../pega_router.py`](../pega_router.py)).

The shipped schemas cover the three highest-volume retirement-plan
email categories:

| Schema | Pega case type (placeholder) | What it represents |
|---|---|---|
| [`distribution_request.yaml`](distribution_request.yaml)         | `ITSM-Work-DistributionRequest`  | Participant taking money out — rollover, lump-sum at termination, RMD, in-service withdrawal |
| [`loan_hardship_request.yaml`](loan_hardship_request.yaml)       | `ITSM-Work-LoanHardshipRequest`  | Participant requesting a 401(k) loan or a hardship withdrawal under IRS safe-harbor categories |
| [`sponsor_inquiry.yaml`](sponsor_inquiry.yaml)                   | `ITSM-Work-SponsorInquiry`       | Employer-side question — plan amendments, compliance, contribution failures, audit support |

> ## ⚠ Placeholder field names — swap with the real Pega team
>
> **The Pega field paths in these schemas are realistic-shaped placeholders, not live values.** They follow common Pega conventions (`Content.D_*.*`, `pyXxx`) so the agent code can be written + tested today, but they have NOT been validated against your live Pega tenant.
>
> When your Pega team is ready, swap in real values:
>
> 1. Get the real case-type ID for each → replace `pega_class:` (currently `ITSM-Work-*`)
> 2. Get a working `POST /cases` payload from Pega Dev Studio's API tester per case type
> 3. Replace each `mapping:` right-hand side with the actual Pega field path
> 4. Confirm the `required:` list matches Pega's server-side validation
> 5. Bump `schema_version` to `1.0` (or higher) in the same PR
> 6. Re-capture snapshot tests in [`../tests/test_pega_router.py`](../tests/test_pega_router.py)
>
> **No agent code changes are needed for the swap.** That's the whole point of the schema-driven approach.

## How to add a new case type

1. Get the Pega case-type ID from your Pega admin (e.g. `ITSM-Work-Beneficiary-Update`).
2. Get a working `POST /cases` payload from Pega's Dev Studio → API tester. Hand-craft + verify it works against your sandbox tenant.
3. Create `<case_type>.yaml` in this directory. Use one of the existing schemas as a template.
4. Add a snapshot test in [`../tests/test_pega_router.py`](../tests/test_pega_router.py) asserting the captured payload matches your hand-crafted one.
5. Wire the new case_type into `_classify_pega_case_type()` in [`../graph.py`](../graph.py) so the agent picks it up.
6. Restart the agent — the registry loads at startup. No edits to `pega_router.py`, no redeploy of unrelated agents.

Files starting with `_` (underscore) are ignored by the loader.

## Schema fields

| Key | Required | Notes |
|---|---|---|
| `case_type`      | ✅ | Unique key — must match what the agent's `_classify_pega_case_type()` returns. |
| `pega_class`     | ✅ | Pega's case-type ID. Get this from your Pega admin. |
| `schema_version` | ✅ | Semver string. **Bump whenever the Pega case-type shape or required-fields list changes.** Lands in the audit row. |
| `mapping`        | ✅ | `arc-field-path → pega-field-path`. Both sides accept dotted paths into nested dicts. |
| `required`       | ⬜ | List of *Pega* field paths that must be non-empty after mapping. Payload is rejected before sending if any are missing. |
| `defaults`       | ⬜ | Static fields injected on every payload of this case type. |
| `transforms`     | ⬜ | Per-field transformations: `round` (with `digits`), `upper`, `lower`, `date_iso8601`. |

## What lives here vs in Python

| Lives in YAML | Lives in Python (`pega_router.py`) |
|---|---|
| Field-name renames (arc → Pega) | The registry / loader / dispatch logic |
| Required-field declarations | Path traversal helpers (`_get_path`, `_set_path`) |
| Per-call defaults | Transform implementations |
| Simple per-field transforms | Anything that needs a Pega lookup, multi-step coercion, or external state |

## Audit trail

Every Pega case creation records `schema_version` and `case_type` in
its audit-row metadata, alongside arc's standard fields. Use those to
reconstruct *exactly* which schema produced a given payload — even
months later, even after the schema YAML has changed several times.

## Related

- [`../pega_router.py`](../pega_router.py) — the registry implementation
- [`../graph.py`](../graph.py) — where the router gets called from the LangGraph node
- [`../tests/test_pega_router.py`](../tests/test_pega_router.py) — snapshot tests per case type
