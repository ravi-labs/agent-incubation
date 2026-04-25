"""
FixtureLoader — loads synthetic test data for harness runs.

Fixtures are YAML or JSON files that define the data sources an agent
would read from in production. The FixtureLoader maps those files to
source names, which MockGatewayConnector serves during the run.

Fixture file format (YAML):
    sources:
      email.inbox:
        - id: "email-001"
          subject: "Cannot login to portal"
          body: "I have been unable to log in since yesterday morning..."
          sender: "john.smith@acme.com"
          received_at: "2026-04-24T09:15:00Z"
      user.directory:
        "john.smith@acme.com":
          name: "John Smith"
          department: "Operations"
          tier: "enterprise"

Usage:
    loader = FixtureLoader("examples/email_triage/fixtures/emails.yaml")
    gateway = loader.to_gateway()   # returns MockGatewayConnector

Or inline:
    loader = FixtureLoader.from_dict({
        "email.inbox": [...],
        "user.directory": {...},
    })
"""

import json
from pathlib import Path
from typing import Any

try:
    import yaml
    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

from foundry.gateway.base import MockGatewayConnector


class FixtureLoader:
    """
    Loads fixture data from YAML/JSON files or inline dicts and
    wraps them in a MockGatewayConnector for harness runs.
    """

    def __init__(self, path: str | Path | None = None, *, data: dict | None = None):
        """
        Args:
            path: Path to a YAML or JSON fixture file.
            data: Inline fixture data dict (alternative to file).
        """
        if path is not None:
            self._data = self._load_file(Path(path))
        elif data is not None:
            self._data = data
        else:
            self._data = {}

    # ── Constructors ──────────────────────────────────────────────────────

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "FixtureLoader":
        """Create a FixtureLoader from an inline dict."""
        return cls(data=data)

    @classmethod
    def empty(cls) -> "FixtureLoader":
        """Create an empty fixture loader (agent returns empty responses)."""
        return cls(data={})

    # ── Data access ───────────────────────────────────────────────────────

    @property
    def sources(self) -> dict[str, Any]:
        """The raw fixture data keyed by source name."""
        return dict(self._data)

    def add(self, source: str, data: Any) -> "FixtureLoader":
        """Add or replace a source's fixture data. Returns self for chaining."""
        self._data[source] = data
        return self

    def source_names(self) -> list[str]:
        """List all source names in this fixture set."""
        return list(self._data.keys())

    # ── Gateway creation ──────────────────────────────────────────────────

    def to_gateway(self) -> MockGatewayConnector:
        """
        Wrap fixture data in a MockGatewayConnector.

        The connector maps source names to fixture data and returns
        them as DataResponse objects when the agent calls gateway.fetch().
        """
        return MockGatewayConnector(self._data)

    # ── Internal ──────────────────────────────────────────────────────────

    @staticmethod
    def _load_file(path: Path) -> dict:
        if not path.exists():
            raise FileNotFoundError(f"Fixture file not found: {path}")

        text = path.read_text(encoding="utf-8")

        if path.suffix in (".yaml", ".yml"):
            if not _HAS_YAML:
                raise ImportError(
                    "PyYAML is required to load YAML fixtures: pip install pyyaml"
                )
            raw = yaml.safe_load(text)
        elif path.suffix == ".json":
            raw = json.loads(text)
        else:
            raise ValueError(f"Unsupported fixture format: {path.suffix} (use .yaml or .json)")

        # Support both top-level sources dict and wrapped {"sources": {...}}
        if isinstance(raw, dict) and "sources" in raw and len(raw) == 1:
            return raw["sources"]
        return raw or {}

    def __repr__(self) -> str:
        return f"FixtureLoader(sources={self.source_names()})"
