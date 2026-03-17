"""
Optional framework integrations for agent-foundry.

Each integration is opt-in — install only what your agent needs:

    pip install "agent-foundry[langchain]"    # LangChain tool wrappers
    pip install "agent-foundry[langgraph]"    # LangGraph state machines
    pip install "agent-foundry[strands]"      # AWS Strands Agents
    pip install "agent-foundry[aws]"          # Strands + boto3 + deployment

The foundry core (BaseAgent, ControlTower, policy engine) has zero dependency
on any of these frameworks. They live here as adapters so the policy enforcement
layer stays framework-agnostic.
"""
