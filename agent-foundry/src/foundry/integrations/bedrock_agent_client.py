"""Migrated to arc.connectors.bedrock_agent_client. Thin re-export shim."""

from arc.connectors.bedrock_agent_client import AgentChunk, BedrockAgentStreamingClient

__all__ = ["BedrockAgentStreamingClient", "AgentChunk"]
