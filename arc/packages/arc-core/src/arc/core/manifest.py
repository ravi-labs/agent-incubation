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

import yaml

from arc.core.effects import (
    ComplianceEffect,
    FinancialEffect,
    HealthcareEffect,
    ITSMEffect,
    LegalEffect,
)

# LifecycleStage still lives in agent-foundry — migrates as module 10. See
# docs/migration-plan.md. Switches to `from arc.core.lifecycle import …` then.
from foundry.lifecycle.stages import LifecycleStage

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
    foundry_version: str = ""

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

    def to_dict(self) -> dict:
        return {
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
            "foundry_version": self.foundry_version,
        }


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
        foundry_version=data.get("foundry_version", ""),
    )
