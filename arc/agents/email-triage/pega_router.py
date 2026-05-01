"""Pega case-type schema registry and triage→Pega payload router.

This module lets the email-triage agent create cases of *multiple*
Pega case types (AutoClaim, PropertyClaim, future Liability, …) without
the agent code knowing anything about Pega field names. Each case type
gets its own declarative schema YAML in ``./pega_schemas/``; the
registry loads them at startup and produces the Pega-shaped payload at
call time.

The split between this Python module and the YAML files mirrors arc's
broader pattern:

  • YAML — declarations a Pega admin or compliance reviewer can read
    and edit. Field renames, required-field lists, defaults, simple
    transforms.
  • Python — behaviour that the registry implements once and reuses
    across every case type. Path traversal, transform dispatch,
    validation.

Adding a new case type is a configuration change, not a code change:

  1. Drop ``./pega_schemas/<case_type>.yaml`` next to the existing two.
  2. Restart the agent — the registry loads at startup.
  3. No edits to this file, no edits to the agent.

The pattern works because the email-triage agent's classify_node
already labels claims by type (auto / property / health / liability).
That label is the case_type key the registry routes on.

See ``./pega_schemas/README.md`` for the schema YAML format and
``./tests/test_pega_router.py`` for behavioural tests including
snapshot tests per shipped schema.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


class PegaSchemaError(ValueError):
    """Raised when a payload can't be assembled.

    Distinct exception class so callers can catch this without
    catching every ValueError. Subclassing ValueError keeps the
    standard "this is bad input" semantics.
    """


# ── Registry ────────────────────────────────────────────────────────────────


class PegaSchemaRegistry:
    """Loads case-type schemas from a directory; maps triage data to Pega payloads.

    Usage::

        from arc.agents.email_triage.pega_router import PegaSchemaRegistry

        registry = PegaSchemaRegistry("arc/agents/email-triage/pega_schemas")
        payload  = registry.map("auto", triage_data)
        # payload = {"caseTypeID": "ITSM-Work-AutoClaim",
        #            "content": {...},
        #            "schema_version": "1.0"}

    Files starting with ``_`` (underscore) in the schemas directory are
    ignored — convention for templates, shared fragments, or
    work-in-progress drafts.
    """

    # Top-level YAML keys every schema file must declare. Listed here as
    # the contract — the validator in `_validate_schema` enforces it.
    REQUIRED_TOP_LEVEL_KEYS: tuple[str, ...] = (
        "case_type",
        "pega_class",
        "schema_version",
        "mapping",
    )

    def __init__(self, schemas_dir: str | Path) -> None:
        self._dir = Path(schemas_dir)
        if not self._dir.is_dir():
            raise PegaSchemaError(
                f"Pega schemas directory not found: {self._dir}"
            )
        self._schemas: dict[str, dict[str, Any]] = {}
        self._load_all()

    # ── public API ──────────────────────────────────────────────────────────

    def case_types(self) -> list[str]:
        """List of all registered case-type keys, sorted for stable output."""
        return sorted(self._schemas)

    def has(self, case_type: str) -> bool:
        return case_type in self._schemas

    def schema(self, case_type: str) -> dict[str, Any]:
        """Return the loaded schema dict for ``case_type``.

        Raises ``PegaSchemaError`` if unknown. Useful for introspection
        and for tooling that wants to render the schema (e.g. the
        ops dashboard's "what fields will this agent populate?" view).
        """
        s = self._schemas.get(case_type)
        if s is None:
            raise PegaSchemaError(self._unknown_case_type_msg(case_type))
        return s

    def map(self, case_type: str, triage_data: dict[str, Any]) -> dict[str, Any]:
        """Build a Pega-shaped payload from triage data using the registered schema.

        Args:
            case_type:    Key matching a registered schema (e.g. ``"auto"``).
            triage_data:  Dict from the agent's state. Dotted paths in the
                          schema's mapping (e.g. ``claimant.email``) traverse
                          nested dicts.

        Returns:
            ``{"caseTypeID": str, "content": dict, "schema_version": str}``.
            The ``content`` dict is shaped exactly like Pega's POST /cases
            payload expects — defaults applied first, mapped fields applied
            second (so mappings can override defaults if intentional).

        Raises:
            PegaSchemaError: case_type unknown, or required field missing
                in the resulting payload, or unknown transform requested.
        """
        schema = self._schemas.get(case_type)
        if schema is None:
            raise PegaSchemaError(self._unknown_case_type_msg(case_type))

        # 1. Defaults first. Mappings can override, which is intentional —
        #    a per-call value should beat a static default.
        payload: dict[str, Any] = {}
        for k, v in (schema.get("defaults") or {}).items():
            _set_path(payload, k, v)

        # 2. Apply each arc → Pega field mapping, with optional per-field transform.
        transforms = schema.get("transforms") or {}
        for arc_path, pega_path in schema["mapping"].items():
            value = _get_path(triage_data, arc_path)
            if value is None:
                continue   # absent input means absent output — required check catches anything we needed
            tf = transforms.get(arc_path)
            if tf is not None:
                value = _apply_transform(value, tf)
            _set_path(payload, pega_path, value)

        # 3. Required-field check. Catch missing values *here*, not when
        #    Pega returns a 400 with a cryptic message.
        missing = [
            r for r in (schema.get("required") or [])
            if _get_path(payload, r) in (None, "")
        ]
        if missing:
            raise PegaSchemaError(
                f"Pega payload missing required fields {missing} for "
                f"case_type={case_type!r}. Schema: "
                f"{schema['_source']}"
            )

        return {
            "caseTypeID":     schema["pega_class"],
            "content":        payload,
            "schema_version": schema["schema_version"],
        }

    # ── internals ──────────────────────────────────────────────────────────

    def _load_all(self) -> None:
        for f in sorted(self._dir.glob("*.yaml")):
            if f.name.startswith("_"):
                continue
            try:
                schema = yaml.safe_load(f.read_text())
            except yaml.YAMLError as exc:
                raise PegaSchemaError(f"Failed to parse {f}: {exc}") from exc

            if not isinstance(schema, dict):
                raise PegaSchemaError(
                    f"{f}: top-level YAML must be a mapping, got {type(schema).__name__}"
                )
            self._validate_schema(f, schema)

            ct = schema["case_type"]
            if ct in self._schemas:
                raise PegaSchemaError(
                    f"Duplicate case_type {ct!r}: defined in both "
                    f"{self._schemas[ct]['_source']} and {f}"
                )
            schema["_source"] = str(f)
            self._schemas[ct] = schema

    @classmethod
    def _validate_schema(cls, path: Path, schema: dict[str, Any]) -> None:
        for required in cls.REQUIRED_TOP_LEVEL_KEYS:
            if required not in schema:
                raise PegaSchemaError(
                    f"{path}: missing required top-level key {required!r}"
                )
        if not isinstance(schema["mapping"], dict):
            raise PegaSchemaError(
                f"{path}: 'mapping' must be a dict, got {type(schema['mapping']).__name__}"
            )
        if not schema["mapping"]:
            raise PegaSchemaError(f"{path}: 'mapping' cannot be empty")

    def _unknown_case_type_msg(self, case_type: str) -> str:
        return (
            f"No Pega schema registered for case_type={case_type!r}. "
            f"Registered: {self.case_types()}. "
            f"To add one, drop a YAML file in {self._dir}/."
        )


# ── Dotted-path helpers ─────────────────────────────────────────────────────
#
# Schemas describe nested fields as dotted paths (e.g.
# ``Content.PropertyDetails.AddressLine1``). These two functions
# implement get/set on nested dicts. Kept module-private and tiny on
# purpose — anything fancier (lists, escaping, etc.) belongs in a real
# library, not here.


def _get_path(d: dict[str, Any], path: str) -> Any:
    """Get a value from a nested dict by dotted path. Returns ``None`` if any segment is missing."""
    cur: Any = d
    for part in path.split("."):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
        if cur is None:
            return None
    return cur


def _set_path(d: dict[str, Any], path: str, value: Any) -> None:
    """Set a value in a nested dict by dotted path, creating intermediate dicts as needed.

    Raises ``PegaSchemaError`` if a non-dict value is in the way at an
    intermediate path segment — that means two mappings are fighting
    over the same parent, which is a schema bug worth surfacing
    explicitly.
    """
    parts = path.split(".")
    cur = d
    for p in parts[:-1]:
        nxt = cur.get(p)
        if nxt is None:
            cur[p] = nxt = {}
        elif not isinstance(nxt, dict):
            raise PegaSchemaError(
                f"Path conflict at {path!r}: segment {p!r} already holds a "
                f"non-dict value ({type(nxt).__name__}). Two mappings are "
                f"colliding on the same parent."
            )
        cur = nxt
    cur[parts[-1]] = value


# ── Transforms ──────────────────────────────────────────────────────────────
#
# Deliberately tiny dispatch. Anything more complex than these belongs
# in Python where it can be tested + version-controlled with full
# tooling, not in YAML where a typo silently breaks production.


def _apply_transform(value: Any, transform: dict[str, Any] | str) -> Any:
    """Apply a named transform to a value.

    Accepts either the short form (``"upper"``) or the long form
    (``{"type": "upper"}``); the latter lets transforms take parameters
    (e.g. ``{"type": "round", "digits": 2}``).

    To add a new transform:
      1. Implement it here under a new ``elif t == "...":`` branch.
      2. Document it in ``./pega_schemas/README.md``.
      3. Add a test in ``./tests/test_pega_router.py``.
    """
    if isinstance(transform, str):
        transform = {"type": transform}

    t = transform.get("type")

    if t == "round":
        return round(float(value), int(transform.get("digits", 0)))
    if t == "upper":
        return str(value).upper()
    if t == "lower":
        return str(value).lower()
    if t == "date_iso8601":
        # Trust the caller's format; this transform exists so a schema can
        # *declare* a date field for downstream tooling. Real parsing logic
        # belongs in Python if we ever need it.
        return str(value)

    raise PegaSchemaError(
        f"Unknown transform type: {t!r}. Supported: round, upper, lower, date_iso8601. "
        f"To add a new one, extend _apply_transform() in arc.agents.email_triage.pega_router."
    )


__all__ = ["PegaSchemaError", "PegaSchemaRegistry"]
