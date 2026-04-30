# Pega case-type schemas

This directory holds **per-case-type schema YAML files** used by the
email-triage agent's Pega router (see [`../pega_router.py`](../pega_router.py)).

## How to add a new case type

The whole point of this layout is that adding a Pega case type is a
**configuration change, not a code change**:

1. Get the Pega case-type ID from your Pega admin (e.g.
   `ITSM-Work-Liability`).
2. Get a working `POST /cases` payload from Pega's Dev Studio →
   API tester. Hand-craft + verify it works against your sandbox tenant
   *before* writing the schema.
3. Create `<case_type>.yaml` in this directory. Use
   [`auto_claim.yaml`](auto_claim.yaml) as a template — it covers every
   shape this loader supports.
4. Add a snapshot test in
   [`../tests/test_pega_router.py`](../tests/test_pega_router.py)
   asserting the captured payload matches your hand-crafted one.
5. Restart the agent — the registry loads at startup. No Python edits,
   no agent edits, no redeploy of the agent code.

## Files

| File | Pega case type | Purpose |
|---|---|---|
| [`auto_claim.yaml`](auto_claim.yaml)         | `ITSM-Work-AutoClaim`     | AutoClaim case mapping |
| [`property_claim.yaml`](property_claim.yaml) | `ITSM-Work-PropertyClaim` | PropertyClaim case mapping |

Files starting with `_` (underscore) are ignored by the loader —
useful for templates, shared fragments, or work-in-progress drafts.

## Schema fields

| Key | Required | Notes |
|---|---|---|
| `case_type`      | ✅ | Unique key — must match the agent's classification output (e.g. `auto`, `property`). |
| `pega_class`     | ✅ | Pega's case-type ID. From your Pega admin. |
| `schema_version` | ✅ | Semver string. **Bump whenever the Pega case-type shape or required-fields list changes.** Lands in the audit row. |
| `mapping`        | ✅ | `arc-field-path → pega-field-path`. Both sides accept dotted paths into nested dicts. |
| `required`       | ⬜ | List of *Pega* field paths that must be non-empty after mapping. Payload is rejected before sending if any are missing. |
| `defaults`       | ⬜ | Static fields injected on every payload of this case type (e.g. `pyStatusWork: Open`). |
| `transforms`     | ⬜ | Per-field transformations (see below). |

## Supported transforms

Deliberately small surface — anything complex belongs in Python, not YAML.

| Transform | Effect | Example |
|---|---|---|
| `round` (with `digits`) | Round numeric to N decimals | `claim_amount` → 2 decimals |
| `upper`                 | `str.upper()` | `vehicle_vin` to uppercase |
| `lower`                 | `str.lower()` | email normalisation |
| `date_iso8601`          | Coerce to ISO 8601 string | incident dates |

The short form `transforms: { field: upper }` is equivalent to
`transforms: { field: { type: upper } }`. Use whichever reads cleaner.

To add a new transform: extend `_apply_transform()` in
[`../pega_router.py`](../pega_router.py) and document it here.

## What lives here vs. in Python

| Lives in YAML | Lives in Python (`pega_router.py`) |
|---|---|
| Field-name renames (arc → Pega) | The registry / loader / dispatch logic |
| Required-field declarations | Path traversal helpers (`_get_path`, `_set_path`) |
| Per-call defaults | Transform implementations |
| Simple per-field transforms | Anything that needs a Pega lookup, multi-step coercion, or external state |

The split exists so **Pega admins + compliance reviewers can read and
edit the YAML without touching Python**. If your team finds you're
adding Python overrides for every new case type, the YAML is missing a
shape — promote it into the loader rather than scattering Python.

## Audit trail

Every Pega case creation records `schema_version` and `case_type` in
its audit-row metadata, alongside arc's standard fields. Use those to
reconstruct *exactly* which schema produced a given payload — even
months later, even after the schema YAML has changed several times.

## Related

- [`../pega_router.py`](../pega_router.py) — the registry implementation
- [`../graph.py`](../graph.py) — where the router gets called from the LangGraph node
- [`../tests/test_pega_router.py`](../tests/test_pega_router.py) — snapshot tests per case type
