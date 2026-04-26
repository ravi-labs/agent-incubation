"""
AgentManifest — the core artifact that moves through the incubation pipeline.

Every incubated agent declares a manifest before any code is written. The
manifest defines scope, permissions, success criteria, and lifecycle stage.
It becomes the audit record that proves due diligence was done.

Manifest YAML schema:
  agent_id:         Unique identifier (e.g., "retirement-trajectory")
  version:          Semantic version (e.g., "1.0.0")
  owner:            Owning team or individual
  description:      What the agent does in plain language
  lifecycle_stage:  Current incubation stage (DISCOVER → SCALE)
  allowed_effects:  List of effect values this agent may invoke
                    (any registered domain: financial, healthcare, legal,
                    ITSM, compliance)
  data_access:      List of data sources the agent may read via Gateway
  policy_path:      Path to the agent's YAML policy file
  success_metrics:  List of measurable outcomes that define success
  environment:      "sandbox" or "production"
  tags:             Optional list of tags for categorization
"""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Protocol

import yaml

from arc.core.effects import (
    ComplianceEffect,
    FinancialEffect,
    HealthcareEffect,
    ITSMEffect,
    LegalEffect,
)

from arc.core.lifecycle import LifecycleStage
from arc.core.llm import LLMConfig
from arc.core.slo import SLOConfig

# All known effect enums — tried in order when parsing manifest YAML.
# Add new domain taxonomies here to make them available in manifests.
_EFFECT_ENUMS = (FinancialEffect, HealthcareEffect, LegalEffect, ITSMEffect, ComplianceEffect)


def _parse_effect(value: str):
    """Parse an effect string to its typed enum, trying all known domains."""
    for enum_cls in _EFFECT_ENUMS:
        try:
            return enum_cls(value)
        except ValueError:
            continue
    raise ValueError(
        f"Unknown effect value {value!r}. "
        f"Must be a valid FinancialEffect, HealthcareEffect, LegalEffect, "
        f"ITSMEffect, or ComplianceEffect."
    )


class AgentStatus(str, Enum):
    """
    Runtime status of a registered agent.

    ACTIVE:     Agent is running normally.
    SUSPENDED:  Agent has been halted — kill switch engaged. No effects
                will be executed until status returns to ACTIVE.
    DEPRECATED: Agent has been superseded. Will not accept new runs.
    """
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DEPRECATED = "deprecated"


@dataclass
class AgentManifest:
    """
    Declarative specification for an incubated agent.

    Loaded from a YAML file. Represents the agent's agreed scope,
    permissions, and current lifecycle position.
    """
    agent_id: str
    version: str
    owner: str
    description: str
    lifecycle_stage: LifecycleStage
    allowed_effects: list[FinancialEffect | HealthcareEffect | LegalEffect | ITSMEffect | ComplianceEffect]
    data_access: list[str]
    policy_path: str
    success_metrics: list[str]
    environment: str = "sandbox"
    tags: list[str] = field(default_factory=list)
    status: AgentStatus = AgentStatus.ACTIVE
    team_repo: str = ""
    arc_version: str = ""
    # Optional per-agent LLM override. When set, takes precedence over
    # the platform default in RuntimeConfig.llm. None = use platform default.
    llm: LLMConfig | None = None
    # Optional Service-Level Objective declaration. When set + non-empty,
    # the auto-demotion watcher (``arc agent watch``) evaluates outcomes
    # against these rules and either queues a PendingApproval or demotes
    # the agent one stage on sustained breach. None = no auto-demotion.
    slo: SLOConfig | None = None

    @property
    def manifest_version(self) -> str:
        """Version string used as Tollgate manifest_version for ALLOW decisions."""
        return f"{self.agent_id}@{self.version}"

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def is_sandbox(self) -> bool:
        return self.environment == "sandbox"

    @property
    def is_active(self) -> bool:
        """Return True if agent is allowed to run. Suspended agents are blocked."""
        return self.status == AgentStatus.ACTIVE

    def allows_effect(self, effect) -> bool:
        """Check whether an effect is in this agent's declared scope.

        Compares by `.value` so cross-domain checks are safe even when the
        runtime effect is from a different enum class than the manifest entry.
        """
        effect_val = effect.value if hasattr(effect, "value") else str(effect)
        return any(
            (e.value if hasattr(e, "value") else str(e)) == effect_val
            for e in self.allowed_effects
        )

    @classmethod
    def from_yaml(cls, path: "str | Path") -> "AgentManifest":
        """
        Load an AgentManifest from a YAML file.

        Convenience classmethod that delegates to the module-level
        ``load_manifest()`` function. Used by the Lambda handler and CLI.
        """
        return load_manifest(path)

    def save(self, path: "str | Path") -> None:
        """
        Persist this manifest to a YAML file.

        Round-trips with ``load_manifest`` — saving then loading produces
        an equal AgentManifest. Used by the promotion pipeline to write
        stage transitions back to disk.
        """
        save_manifest(self, path)

    def to_dict(self) -> dict:
        out: dict = {
            "agent_id": self.agent_id,
            "version": self.version,
            "owner": self.owner,
            "description": self.description,
            "lifecycle_stage": self.lifecycle_stage.value,
            "status": self.status.value,
            "allowed_effects": [e.value for e in self.allowed_effects],
            "data_access": self.data_access,
            "policy_path": self.policy_path,
            "success_metrics": self.success_metrics,
            "environment": self.environment,
            "tags": self.tags,
            "team_repo": self.team_repo,
            "arc_version": self.arc_version,
        }
        # Only emit `llm:` when the agent overrides the platform default.
        # Manifests without an llm: block stay clean.
        if self.llm is not None and not self.llm.is_empty():
            out["llm"] = self.llm.to_dict()
        # Only emit `slo:` when the agent declares one. Manifests without
        # an slo block stay clean and aren't subject to auto-demotion.
        if self.slo is not None and not self.slo.is_empty():
            out["slo"] = self.slo.to_dict()
        return out


def load_manifest(path: str | Path) -> AgentManifest:
    """
    Load an AgentManifest from a YAML file.

    Args:
        path: Path to the manifest YAML file.

    Returns:
        Parsed and validated AgentManifest.

    Raises:
        ValueError: If required fields are missing or invalid.
    """
    with open(path) as f:
        data = yaml.safe_load(f)

    required = {"agent_id", "version", "owner", "description",
                "lifecycle_stage", "allowed_effects", "data_access",
                "policy_path", "success_metrics"}
    missing = required - set(data.keys())
    if missing:
        raise ValueError(f"Manifest missing required fields: {missing}")

    try:
        stage = LifecycleStage(data["lifecycle_stage"])
    except ValueError:
        valid = [s.value for s in LifecycleStage]
        raise ValueError(
            f"Invalid lifecycle_stage '{data['lifecycle_stage']}'. "
            f"Must be one of: {valid}"
        ) from None

    try:
        effects = [_parse_effect(e) for e in data["allowed_effects"]]
    except ValueError as e:
        raise ValueError(f"Invalid effect in allowed_effects: {e}") from None

    raw_status = data.get("status", "active")
    try:
        status = AgentStatus(raw_status)
    except ValueError:
        valid = [s.value for s in AgentStatus]
        raise ValueError(
            f"Invalid status '{raw_status}'. Must be one of: {valid}"
        ) from None

    # Optional per-agent LLM override. Missing → None (use platform default).
    llm_block = data.get("llm")
    llm_config: LLMConfig | None = None
    if isinstance(llm_block, dict) and llm_block:
        llm_config = LLMConfig.from_dict(llm_block)

    # Optional SLO block. Missing or empty → no auto-demotion for this agent.
    slo_block = data.get("slo")
    slo_config: SLOConfig | None = None
    if isinstance(slo_block, dict) and slo_block:
        slo_config = SLOConfig.from_dict(slo_block)

    return AgentManifest(
        agent_id=data["agent_id"],
        version=data["version"],
        owner=data["owner"],
        description=data["description"],
        lifecycle_stage=stage,
        allowed_effects=effects,
        data_access=data["data_access"],
        policy_path=data["policy_path"],
        success_metrics=data["success_metrics"],
        environment=data.get("environment", "sandbox"),
        tags=data.get("tags", []),
        status=status,
        team_repo=data.get("team_repo", ""),
        # Accept legacy `foundry_version` key for back-compat with manifests
        # written before the foundry → arc rename. New manifests should use
        # `arc_version`; the loader prefers it when both are present.
        arc_version=data.get("arc_version", data.get("foundry_version", "")),
        llm=llm_config,
        slo=slo_config,
    )


def save_manifest(manifest: AgentManifest, path: str | Path) -> None:
    """
    Persist an AgentManifest to a YAML file.

    Round-trips with ``load_manifest``. Creates parent directories if needed.
    Output uses block style with stable key order so diffs stay readable.

    Args:
        manifest: The manifest to write.
        path:     Destination YAML file. Parent directories are created.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        yaml.safe_dump(
            manifest.to_dict(),
            f,
            sort_keys=False,
            default_flow_style=False,
        )


# ── ManifestStore — pluggable persistence for AgentManifest ─────────────────


class ManifestStore(Protocol):
    """
    Persistence layer for ``AgentManifest``.

    The promotion pipeline produces decisions; a ManifestStore is what
    actually writes the new ``lifecycle_stage`` (or status, version, etc.)
    back to durable storage.

    Two built-in implementations cover the common cases:

      - ``LocalFileManifestStore`` — single ``manifest.yaml`` in a team's
        own repo. The store knows one agent.

      - ``DirectoryManifestStore`` — directory tree like the agent-registry
        layout (``<root>/<agent_id>/manifest.yaml``). The store knows many.

    Custom backends (S3, DynamoDB, a registry API) just need to implement
    these three methods.
    """

    def load(self, agent_id: str) -> AgentManifest:
        """Load the manifest for ``agent_id``. Raises if not found."""
        ...

    def save(self, manifest: AgentManifest) -> None:
        """Persist a manifest, replacing any prior version for the same agent."""
        ...

    def exists(self, agent_id: str) -> bool:
        """True iff a manifest is stored for ``agent_id``."""
        ...


class LocalFileManifestStore:
    """
    Manifest store backed by a single YAML file (one agent per store).

    Use when an agent's manifest lives at a known path in its own repo —
    typically the team-template layout where each team repo has one or a
    few manifests, each with a fixed location.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self, agent_id: str | None = None) -> AgentManifest:
        manifest = load_manifest(self.path)
        if agent_id is not None and manifest.agent_id != agent_id:
            raise ValueError(
                f"manifest at {self.path} declares agent_id={manifest.agent_id!r}, "
                f"requested {agent_id!r}"
            )
        return manifest

    def save(self, manifest: AgentManifest) -> None:
        save_manifest(manifest, self.path)

    def exists(self, agent_id: str | None = None) -> bool:
        if not self.path.exists():
            return False
        if agent_id is None:
            return True
        try:
            return load_manifest(self.path).agent_id == agent_id
        except Exception:
            return False


class DirectoryManifestStore:
    """
    Manifest store backed by a directory tree, one subdirectory per agent.

    Layout (matches ``agent-registry/registry/`` and ``arc/agents/``):

        <root>/
          retirement-trajectory/manifest.yaml
          fiduciary-watchdog/manifest.yaml
          ...

    Use this for the central governance catalog, where many agents'
    manifests live side by side and are looked up by agent_id.
    """

    def __init__(
        self,
        root: str | Path,
        *,
        manifest_filename: str = "manifest.yaml",
    ) -> None:
        self.root = Path(root)
        self.manifest_filename = manifest_filename

    def _path_for(self, agent_id: str) -> Path:
        return self.root / agent_id / self.manifest_filename

    def load(self, agent_id: str) -> AgentManifest:
        path = self._path_for(agent_id)
        if not path.exists():
            raise FileNotFoundError(
                f"no manifest for agent_id={agent_id!r} at {path}"
            )
        return load_manifest(path)

    def save(self, manifest: AgentManifest) -> None:
        save_manifest(manifest, self._path_for(manifest.agent_id))

    def exists(self, agent_id: str) -> bool:
        return self._path_for(agent_id).exists()

    def agent_ids(self) -> list[str]:
        """List every agent_id with a manifest under ``root``."""
        if not self.root.exists():
            return []
        return sorted(
            sub.name
            for sub in self.root.iterdir()
            if sub.is_dir() and (sub / self.manifest_filename).exists()
        )
