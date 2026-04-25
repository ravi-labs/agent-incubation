"""
arc.runtime — production wiring layer.

Reads all connector credentials from environment variables or
AWS Secrets Manager, validates them at startup, and wires a
production-ready agent in one call.

Swap from harness to production:
    # Harness (dev)
    from arc.harness import HarnessBuilder
    agent = HarnessBuilder(...).with_fixtures(...).build(MyAgent)

    # Production (one line change)
    from arc.runtime import RuntimeBuilder, RuntimeConfig
    agent = RuntimeBuilder(RuntimeConfig.from_env(), ...).build(MyAgent)

Environment variables:
    # Outlook / MS Graph
    OUTLOOK_TENANT_ID, OUTLOOK_CLIENT_ID, OUTLOOK_CLIENT_SECRET

    # Pega Case Management
    PEGA_BASE_URL, PEGA_CLIENT_ID, PEGA_CLIENT_SECRET

    # Pega Knowledge Buddy
    PEGA_KB_BASE_URL, PEGA_KB_API_KEY

    # ServiceNow
    SNOW_INSTANCE_URL, SNOW_CLIENT_ID, SNOW_CLIENT_SECRET

    # AWS / Bedrock
    AWS_REGION, BEDROCK_MODEL_ID

    # AgentCore
    AGENTCORE_AGENT_ID, AGENTCORE_MEMORY_ID
"""

from .config import RuntimeConfig
from .builder import RuntimeBuilder

__all__ = ["RuntimeConfig", "RuntimeBuilder"]
