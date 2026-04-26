"""
RuntimeConfig — env-var driven production configuration.

Reads all connector credentials at startup and validates that required
fields are present for the connectors the agent actually needs.
Fails fast with a clear error before the agent starts — not mid-run.

Uses pydantic-settings so the same config works from:
  - Environment variables (production / ECS)
  - .env file (local dev — same var names)
  - AWS Secrets Manager (via env vars that reference secrets)
"""

from __future__ import annotations
import os
from dataclasses import dataclass, field
from typing import Any

from arc.core import LLMConfig


@dataclass
class OutlookConfig:
    tenant_id:     str
    client_id:     str
    client_secret: str
    inbox_user:    str = ""   # UPN of the mailbox to monitor

    @classmethod
    def from_env(cls) -> "OutlookConfig":
        return cls(
            tenant_id     = _require("OUTLOOK_TENANT_ID"),
            client_id     = _require("OUTLOOK_CLIENT_ID"),
            client_secret = _require("OUTLOOK_CLIENT_SECRET"),
            inbox_user    = os.getenv("OUTLOOK_INBOX_USER", ""),
        )


@dataclass
class PegaCaseConfig:
    base_url:      str
    client_id:     str
    client_secret: str
    case_type:     str = "ITSM-Work-ServiceRequest"

    @classmethod
    def from_env(cls) -> "PegaCaseConfig":
        return cls(
            base_url      = _require("PEGA_BASE_URL"),
            client_id     = _require("PEGA_CLIENT_ID"),
            client_secret = _require("PEGA_CLIENT_SECRET"),
            case_type     = os.getenv("PEGA_CASE_TYPE", "ITSM-Work-ServiceRequest"),
        )


@dataclass
class PegaKnowledgeConfig:
    base_url: str
    api_key:  str

    @classmethod
    def from_env(cls) -> "PegaKnowledgeConfig":
        return cls(
            base_url = _require("PEGA_KB_BASE_URL"),
            api_key  = _require("PEGA_KB_API_KEY"),
        )


@dataclass
class ServiceNowConfig:
    instance_url:  str
    client_id:     str
    client_secret: str
    table:         str = "incident"

    @classmethod
    def from_env(cls) -> "ServiceNowConfig":
        return cls(
            instance_url  = _require("SNOW_INSTANCE_URL"),
            client_id     = _require("SNOW_CLIENT_ID"),
            client_secret = _require("SNOW_CLIENT_SECRET"),
            table         = os.getenv("SNOW_TABLE", "incident"),
        )


@dataclass
class BedrockConfig:
    region:   str = "us-east-1"
    model_id: str = "us.anthropic.claude-3-5-sonnet-20241022-v2:0"

    @classmethod
    def from_env(cls) -> "BedrockConfig":
        return cls(
            region   = os.getenv("AWS_REGION", "us-east-1"),
            model_id = os.getenv(
                "BEDROCK_MODEL_ID",
                "us.anthropic.claude-3-5-sonnet-20241022-v2:0",
            ),
        )


@dataclass
class AgentCoreConfig:
    agent_id:  str
    memory_id: str = ""
    region:    str = "us-east-1"

    @classmethod
    def from_env(cls) -> "AgentCoreConfig":
        return cls(
            agent_id  = _require("AGENTCORE_AGENT_ID"),
            memory_id = os.getenv("AGENTCORE_MEMORY_ID", ""),
            region    = os.getenv("AWS_REGION", "us-east-1"),
        )


@dataclass
class RuntimeConfig:
    """
    Full runtime configuration for a production Arc agent.

    Each connector config section is optional — only validated when
    the agent's manifest declares effects that require it.
    """

    bedrock:          BedrockConfig
    outlook:          OutlookConfig | None = None
    pega_case:        PegaCaseConfig | None = None
    pega_knowledge:   PegaKnowledgeConfig | None = None
    servicenow:       ServiceNowConfig | None = None
    agentcore:        AgentCoreConfig | None = None

    # LLM provider (platform default; per-agent manifest can override)
    llm:              "LLMConfig | None" = None

    # Audit
    audit_sink:       str = "jsonl"          # "jsonl" | "cloudwatch" | "s3"
    audit_path:       str = "arc_audit.jsonl"

    # Approvals
    approver_mode:    str = "sqs"            # "sqs" | "cli" | "webhook"
    sqs_queue_url:    str = ""

    @classmethod
    def from_env(cls) -> "RuntimeConfig":
        """
        Build RuntimeConfig from environment variables.
        Only loads connector configs that have their required vars set.
        """
        outlook  = None
        if os.getenv("OUTLOOK_CLIENT_ID"):
            outlook = OutlookConfig.from_env()

        pega_case = None
        if os.getenv("PEGA_CLIENT_ID"):
            pega_case = PegaCaseConfig.from_env()

        pega_kb = None
        if os.getenv("PEGA_KB_API_KEY"):
            pega_kb = PegaKnowledgeConfig.from_env()

        snow = None
        if os.getenv("SNOW_CLIENT_ID"):
            snow = ServiceNowConfig.from_env()

        agentcore = None
        if os.getenv("AGENTCORE_AGENT_ID"):
            agentcore = AgentCoreConfig.from_env()

        # LLM platform default — empty by default (no LLM injected). Set
        # ARC_LLM_PROVIDER=bedrock|litellm to enable. Agents can override
        # via their manifest's llm: block.
        llm = LLMConfig.from_env()

        return cls(
            bedrock        = BedrockConfig.from_env(),
            outlook        = outlook,
            pega_case      = pega_case,
            pega_knowledge = pega_kb,
            servicenow     = snow,
            agentcore      = agentcore,
            llm            = llm if not llm.is_empty() else None,
            audit_sink     = os.getenv("ARC_AUDIT_SINK", "jsonl"),
            audit_path     = os.getenv("ARC_AUDIT_PATH", "arc_audit.jsonl"),
            approver_mode  = os.getenv("ARC_APPROVER_MODE", "sqs"),
            sqs_queue_url  = os.getenv("ARC_SQS_QUEUE_URL", ""),
        )

    def validate_for_agent(self, required_connectors: list[str]) -> None:
        """
        Validate that all connectors required by the agent are configured.
        Call before starting the agent — fail fast, not mid-run.

        Args:
            required_connectors: list of connector names the agent needs,
                                 e.g. ["outlook", "pega_case"]
        """
        missing = []
        for name in required_connectors:
            if name == "outlook"          and self.outlook is None:
                missing.append("OUTLOOK_TENANT_ID / OUTLOOK_CLIENT_ID / OUTLOOK_CLIENT_SECRET")
            elif name == "pega_case"      and self.pega_case is None:
                missing.append("PEGA_BASE_URL / PEGA_CLIENT_ID / PEGA_CLIENT_SECRET")
            elif name == "pega_knowledge" and self.pega_knowledge is None:
                missing.append("PEGA_KB_BASE_URL / PEGA_KB_API_KEY")
            elif name == "servicenow"     and self.servicenow is None:
                missing.append("SNOW_INSTANCE_URL / SNOW_CLIENT_ID / SNOW_CLIENT_SECRET")
            elif name == "agentcore"      and self.agentcore is None:
                missing.append("AGENTCORE_AGENT_ID")

        if missing:
            raise EnvironmentError(
                f"RuntimeConfig missing required environment variables:\n"
                + "\n".join(f"  • {m}" for m in missing)
            )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _require(var: str) -> str:
    """Read a required env var — raise clearly if missing."""
    val = os.getenv(var)
    if not val:
        raise EnvironmentError(
            f"Required environment variable {var!r} is not set. "
            f"Check your .env file or AWS Secrets Manager configuration."
        )
    return val
