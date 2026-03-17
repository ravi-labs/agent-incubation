"""Global network policy enforcement for AI agent tool calls.

Provides a NetworkGuard that validates any URL-like parameters against
a global allow/blocklist, independent of per-tool constraints in the
manifest. This is the systematic solution for network-level security.

Configuration is typically loaded from ``policy.yaml``:

    network_policy:
      default: deny       # "deny" or "allow"
      allowlist:
        - pattern: "https://api.github.com/*"
        - pattern: "https://arxiv.org/*"
      blocklist:
        - pattern: "http://*"          # No plaintext HTTP
        - pattern: "*.internal.*"      # No internal hosts
      param_fields_to_check:           # Which param keys to inspect
        - url
        - endpoint
        - target
        - href
        - uri
"""

import fnmatch
from typing import Any


class NetworkGuard:
    """Global URL policy enforcement.

    Inspects tool parameters for URL values and validates them against
    allow/blocklists. Works alongside per-tool constraints in the manifest
    (roadmap 1.4) to provide defense-in-depth.

    Args:
        default: "deny" (block unlisted URLs) or "allow" (permit unless blocked).
        allowlist: List of dicts with ``pattern`` key (glob patterns).
        blocklist: List of dicts with ``pattern`` key (glob patterns).
        param_fields_to_check: List of parameter names to inspect for URLs.
            If None, all string params starting with http(s):// are checked.
    """

    def __init__(
        self,
        *,
        default: str = "deny",
        allowlist: list[dict[str, str]] | None = None,
        blocklist: list[dict[str, str]] | None = None,
        param_fields_to_check: list[str] | None = None,
    ):
        if default not in ("deny", "allow"):
            raise ValueError(f"default must be 'deny' or 'allow', got '{default}'")

        self.default = default
        self._allow_patterns = [
            e["pattern"] for e in (allowlist or []) if "pattern" in e
        ]
        self._block_patterns = [
            e["pattern"] for e in (blocklist or []) if "pattern" in e
        ]
        self._param_fields = (
            set(param_fields_to_check) if param_fields_to_check else None
        )

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "NetworkGuard":
        """Create a NetworkGuard from a policy.yaml ``network_policy`` dict."""
        return cls(
            default=config.get("default", "deny"),
            allowlist=config.get("allowlist"),
            blocklist=config.get("blocklist"),
            param_fields_to_check=config.get("param_fields_to_check"),
        )

    def check(self, params: dict[str, Any]) -> list[str]:
        """Check tool parameters against the network policy.

        Returns a list of violation strings (empty = OK).
        """
        violations: list[str] = []

        for key, value in params.items():
            if not isinstance(value, str):
                continue
            if not (value.startswith("http://") or value.startswith("https://")):
                continue

            # If specific fields are configured, only check those
            if self._param_fields is not None and key not in self._param_fields:
                continue

            # Check blocklist first (always wins)
            for pattern in self._block_patterns:
                if fnmatch.fnmatch(value, pattern):
                    violations.append(
                        f"Parameter '{key}': URL '{value}' blocked by "
                        f"network policy (matches '{pattern}')"
                    )
                    break  # One block match is enough

            # Check allowlist
            if self._allow_patterns:
                if any(fnmatch.fnmatch(value, p) for p in self._allow_patterns):
                    continue  # Explicitly allowed

                # Not in allowlist
                if self.default == "deny":
                    violations.append(
                        f"Parameter '{key}': URL '{value}' not in "
                        f"network policy allowlist"
                    )
            elif self.default == "deny":
                # No allowlist defined + default deny = block all URLs
                violations.append(
                    f"Parameter '{key}': URL '{value}' blocked by "
                    f"network policy (default: deny, no allowlist defined)"
                )

        return violations
