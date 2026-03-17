"""
foundry.deploy
──────────────
Deployment adapters for agent-foundry agents.

Supported targets:
  - AWS Lambda          foundry.deploy.lambda_handler
  - Bedrock Agent Core  foundry.deploy.bedrock
  - Container (ECS/EKS) use the provided Dockerfile in deploy/

Install:
    pip install "agent-foundry[aws]"
"""
