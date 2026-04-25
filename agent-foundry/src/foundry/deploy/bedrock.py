"""Migrated to arc.runtime.deploy.bedrock. Thin re-export shim."""

from arc.runtime.deploy.bedrock import (
    BedrockAgentAdapter,
    BedrockEventParser,
    generate_action_schema,
    register_bedrock_agent,
    upload_schema_to_s3,
)

__all__ = [
    "BedrockAgentAdapter",
    "BedrockEventParser",
    "generate_action_schema",
    "register_bedrock_agent",
    "upload_schema_to_s3",
]
