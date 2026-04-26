"""
arc.connectors — connector library for Arc agents.

Provides connectors for:
  - Microsoft Outlook / Graph API (email)
  - Pega Case Management (ITSM tickets)
  - Pega Knowledge Buddy (RAG knowledge base)
  - ServiceNow Table API (ITSM incidents)
  - MockTicketConnector (in-memory harness testing)

All connectors implement the ArcConnector base class with fetch() and
execute() methods compatible with GatewayConnector, making them
substitutable by MockGatewayConnector in harness mode.

Usage:
    from arc.connectors import (
        OutlookConnector,
        PegaCaseConnector,
        PegaKnowledgeConnector,
        ServiceNowConnector,
        MockTicketConnector,
    )
"""

from .base import ArcConnector, OAuthMixin
from .mock import MockTicketConnector
from .outlook import OutlookConnector
from .pega_case import PegaCaseConnector
from .pega_knowledge import PegaKnowledgeConnector
from .servicenow import ServiceNowConnector


# Lazy-loaded clients. Each requires its own optional dependency:
#   Bedrock*  → arc-connectors[aws]   (boto3)
#   LiteLLM*  → arc-connectors[litellm] (litellm)
def __getattr__(name: str):
    lazy_map = {
        # Bedrock (boto3)
        "BedrockKBClient":             ("bedrock_kb",            "BedrockKBClient"),
        "RetrievedPassage":            ("bedrock_kb",            "RetrievedPassage"),
        "BedrockLLMClient":            ("bedrock_llm",           "BedrockLLMClient"),
        "BedrockGuardrailsAdapter":    ("bedrock_guardrails",    "BedrockGuardrailsAdapter"),
        "GuardrailsMixin":             ("bedrock_guardrails",    "GuardrailsMixin"),
        "GuardrailIntervention":       ("bedrock_guardrails",    "GuardrailIntervention"),
        "GuardrailAssessment":         ("bedrock_guardrails",    "GuardrailAssessment"),
        "BedrockAgentStreamingClient": ("bedrock_agent_client",  "BedrockAgentStreamingClient"),
        "AgentChunk":                  ("bedrock_agent_client",  "AgentChunk"),
        # LiteLLM (multi-provider)
        "LiteLLMClient":               ("litellm_client",        "LiteLLMClient"),
    }
    if name in lazy_map:
        from importlib import import_module
        mod_name, attr = lazy_map[name]
        mod = import_module(f"arc.connectors.{mod_name}")
        return getattr(mod, attr)
    raise AttributeError(f"module 'arc.connectors' has no attribute {name!r}")


__all__ = [
    "ArcConnector",
    "OAuthMixin",
    "OutlookConnector",
    "PegaCaseConnector",
    "PegaKnowledgeConnector",
    "ServiceNowConnector",
    "MockTicketConnector",
    # Bedrock connectors (lazy, requires arc-connectors[aws])
    "BedrockKBClient", "RetrievedPassage",
    "BedrockLLMClient",
    "BedrockGuardrailsAdapter", "GuardrailsMixin",
    "GuardrailIntervention", "GuardrailAssessment",
    "BedrockAgentStreamingClient", "AgentChunk",
    # LiteLLM client (lazy, requires arc-connectors[litellm])
    "LiteLLMClient",
]
