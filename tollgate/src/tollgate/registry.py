import fnmatch
import hashlib
import re
from pathlib import Path
from typing import Any

import yaml

from .types import Effect


class ToolRegistry:
    """A registry of trusted tool metadata.

    Supports:
      - Tool effect / resource_type resolution (core v1.0)
      - Parameter schema validation (v1.1 — roadmap 1.1)
      - Tool constraints such as URL allowlisting (v1.1 — roadmap 1.4)
      - Cryptographic manifest signing verification (v1.2 — roadmap 2.2)
    """

    def __init__(
        self,
        manifest_path: str | Path,
        *,
        signing_key: bytes | None = None,
    ):
        self.path = Path(manifest_path)
        if not self.path.exists():
            raise FileNotFoundError(f"Manifest file not found: {manifest_path}")

        # 2.2: Verify manifest signature before loading
        if signing_key is not None:
            from .manifest_signing import verify_manifest

            if not verify_manifest(self.path, secret_key=signing_key):
                raise ValueError(
                    f"Manifest signature verification failed for {manifest_path}. "
                    "The manifest may have been tampered with, or the "
                    "signature file (.sig) is missing."
                )

        with self.path.open("r") as f:
            content = f.read()
            self.data = yaml.safe_load(content)
            if not self.data:
                self.data = {}
            # Use content hash as manifest version if not provided
            self.version = str(
                self.data.get(
                    "version", hashlib.sha256(content.encode()).hexdigest()[:8]
                )
            )
        self.tools = self.data.get("tools", {})
        self._validate_manifest()

    def _validate_manifest(self):
        """Basic validation of manifest structure."""
        if not isinstance(self.tools, dict):
            raise ValueError("Manifest 'tools' must be a dictionary.")

        for key, entry in self.tools.items():
            if not isinstance(entry, dict):
                raise ValueError(f"Tool entry '{key}' must be a dictionary.")
            if "effect" in entry:
                try:
                    Effect(entry["effect"])
                except ValueError as e:
                    raise ValueError(
                        f"Invalid effect '{entry['effect']}' for tool '{key}'."
                    ) from e

    def resolve_tool(self, tool_key: str) -> tuple[Effect, str, str | None]:
        """
        Resolve tool key to (effect, resource_type, manifest_version).
        Returns (UNKNOWN, "unknown", None) if not found.
        """
        meta = self.tools.get(tool_key)
        if not meta:
            return Effect.UNKNOWN, "unknown", None

        effect = Effect(meta.get("effect", "unknown"))
        resource_type = meta.get("resource_type", "unknown")
        return effect, resource_type, self.version

    # ------------------------------------------------------------------
    # 1.1  Parameter Schema Validation
    # ------------------------------------------------------------------

    def get_params_schema(self, tool_key: str) -> dict[str, Any] | None:
        """Return the params_schema for a tool, or None if not defined."""
        meta = self.tools.get(tool_key)
        if not meta:
            return None
        return meta.get("params_schema")

    def validate_params(self, tool_key: str, params: dict[str, Any]) -> list[str]:
        """Validate tool parameters against the manifest schema.

        Returns a list of validation error strings.  An empty list means
        the parameters are valid (or no schema is defined for this tool).

        The validator is intentionally self-contained (no ``jsonschema``
        dependency) and supports a practical subset of JSON Schema:

        - ``type`` (string, number, integer, boolean, object, array, null)
        - ``required`` (list of required property names)
        - ``properties`` (per-property sub-schemas)
        - ``pattern`` (regex for string values)
        - ``maxLength`` / ``minLength``
        - ``minimum`` / ``maximum``
        - ``enum``
        """
        schema = self.get_params_schema(tool_key)
        if schema is None:
            return []  # No schema ⇒ no validation errors

        return self._validate_value(params, schema, path="params")

    def _validate_value(
        self, value: Any, schema: dict[str, Any], path: str
    ) -> list[str]:
        """Recursively validate a value against a JSON-Schema-like dict."""
        errors: list[str] = []

        # --- type ---
        if "type" in schema:
            expected = schema["type"]
            if not self._type_matches(value, expected):
                errors.append(
                    f"{path}: expected type '{expected}', got {type(value).__name__}"
                )
                return errors  # No point checking further if type is wrong

        # --- enum ---
        if "enum" in schema and value not in schema["enum"]:
            errors.append(f"{path}: value {value!r} not in enum {schema['enum']}")

        # --- string constraints ---
        if isinstance(value, str):
            if "minLength" in schema and len(value) < schema["minLength"]:
                errors.append(
                    f"{path}: length {len(value)} < minLength {schema['minLength']}"
                )
            if "maxLength" in schema and len(value) > schema["maxLength"]:
                errors.append(
                    f"{path}: length {len(value)} > maxLength {schema['maxLength']}"
                )
            if "pattern" in schema and not re.search(schema["pattern"], value):
                errors.append(
                    f"{path}: value does not match pattern '{schema['pattern']}'"
                )

        # --- numeric constraints ---
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if "minimum" in schema and value < schema["minimum"]:
                errors.append(f"{path}: value {value} < minimum {schema['minimum']}")
            if "maximum" in schema and value > schema["maximum"]:
                errors.append(f"{path}: value {value} > maximum {schema['maximum']}")

        # --- object: required + properties ---
        if isinstance(value, dict):
            if "required" in schema:
                for req_key in schema["required"]:
                    if req_key not in value:
                        errors.append(f"{path}: missing required key '{req_key}'")

            if "properties" in schema:
                for prop_key, prop_schema in schema["properties"].items():
                    if prop_key in value:
                        errors.extend(
                            self._validate_value(
                                value[prop_key], prop_schema, path=f"{path}.{prop_key}"
                            )
                        )

        # --- array: items ---
        if isinstance(value, list) and "items" in schema:
            for i, item in enumerate(value):
                errors.extend(
                    self._validate_value(item, schema["items"], path=f"{path}[{i}]")
                )

        return errors

    @staticmethod
    def _type_matches(value: Any, expected: str) -> bool:
        """Check if a Python value matches a JSON Schema type string."""
        type_map = {
            "string": str,
            "integer": int,
            "number": (int, float),
            "boolean": bool,
            "object": dict,
            "array": list,
            "null": type(None),
        }
        py_type = type_map.get(expected)
        if py_type is None:
            return True  # Unknown type ⇒ permissive
        # bool is a subtype of int in Python — exclude it for "integer"/"number"
        if expected in ("integer", "number") and isinstance(value, bool):
            return False
        return isinstance(value, py_type)

    # ------------------------------------------------------------------
    # 1.4  Tool Constraints (e.g. URL allowlisting)
    # ------------------------------------------------------------------

    def get_constraints(self, tool_key: str) -> dict[str, Any] | None:
        """Return the constraints dict for a tool, or None."""
        meta = self.tools.get(tool_key)
        if not meta:
            return None
        return meta.get("constraints")

    def check_constraints(self, tool_key: str, params: dict[str, Any]) -> list[str]:
        """Check tool parameters against manifest constraints.

        Currently supports:
          - ``allowed_url_patterns``: list of glob patterns. Any param
            value that looks like a URL (starts with ``http://`` or
            ``https://``) is checked against these patterns.
          - ``blocked_url_patterns``: list of glob patterns that are
            explicitly denied.
          - ``param_constraints``: per-parameter constraints with
            ``allowed_values`` or ``pattern``.

        Returns a list of violation strings (empty = OK).
        """
        constraints = self.get_constraints(tool_key)
        if not constraints:
            return []

        violations: list[str] = []

        # --- URL allowlisting ---
        allowed_urls = constraints.get("allowed_url_patterns")
        blocked_urls = constraints.get("blocked_url_patterns")

        if allowed_urls or blocked_urls:
            for param_key, param_val in params.items():
                if isinstance(param_val, str) and (
                    param_val.startswith("http://") or param_val.startswith("https://")
                ):
                    # Check blocked first
                    if blocked_urls:
                        for pattern in blocked_urls:
                            if fnmatch.fnmatch(param_val, pattern):
                                violations.append(
                                    f"Parameter '{param_key}': URL '{param_val}' "
                                    f"matches blocked pattern '{pattern}'"
                                )

                    # Check allowed (only if allowlist is defined)
                    if allowed_urls and not any(
                        fnmatch.fnmatch(param_val, p) for p in allowed_urls
                    ):
                        violations.append(
                            f"Parameter '{param_key}': URL '{param_val}' "
                            f"does not match any allowed URL pattern"
                        )

        # --- Per-parameter constraints ---
        param_constraints = constraints.get("param_constraints")
        if param_constraints:
            for param_key, pc in param_constraints.items():
                if param_key not in params:
                    continue
                val = params[param_key]

                if "allowed_values" in pc and val not in pc["allowed_values"]:
                    violations.append(
                        f"Parameter '{param_key}': value {val!r} "
                        f"not in allowed_values {pc['allowed_values']}"
                    )
                if (
                    "pattern" in pc
                    and isinstance(val, str)
                    and not re.search(pc["pattern"], val)
                ):
                    violations.append(
                        f"Parameter '{param_key}': value does not match "
                        f"pattern '{pc['pattern']}'"
                    )

        return violations
