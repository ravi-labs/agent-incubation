"""
Optional framework integrations for agent-foundry.

Each integration is opt-in — install only what your agent needs:

    pip install "agent-foundry[langchain]"    # LangChain FoundryTool, FoundryRunnable
    pip install "agent-foundry[langgraph]"    # LangGraph GraphAgent + checkpointing
    pip install "agent-foundry[strands]"      # AWS Strands Agents
    pip install "agent-foundry[aws]"          # boto3 + Bedrock KB/LLM/Agent clients

Available integrations:
  foundry.integrations.langchain
    FoundryTool        — StructuredTool wrapping a single FinancialEffect
    FoundryToolkit     — All manifest effects as a LangChain toolkit
    FoundryRunnable    — BaseAgent as a LangChain Runnable for LCEL |

  foundry.integrations.langgraph
    GraphAgent         — BaseAgent with LangGraph StateGraph + checkpointer + astream()
    FoundryState       — Base TypedDict for graph state schemas

  foundry.integrations.bedrock_kb
    BedrockKBClient    — Policy-enforced Amazon Bedrock Knowledge Base retriever

  foundry.integrations.bedrock_llm
    BedrockLLMClient   — Policy-enforced Amazon Bedrock LLM (Converse API)

  foundry.integrations.bedrock_agent_client
    BedrockAgentStreamingClient — Async streaming client for Bedrock Agent invocations
    AgentChunk                  — Streaming chunk dataclass

The foundry core (BaseAgent, ControlTower, policy engine) has zero dependency
on any of these frameworks. They live here as adapters so the policy enforcement
layer stays framework-agnostic.

NOTE: BaseAgent itself implements the LangChain Runnable protocol (invoke,
ainvoke, stream, astream, __or__, __ror__) as lightweight methods. No import
of langchain-core required unless you use the | pipe operator.
"""
