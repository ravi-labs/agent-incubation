"""arc.runtime.deploy — production deployment adapters.

Supported targets:
  - AWS Lambda          arc.runtime.deploy.lambda_handler
  - Bedrock Agent Core  arc.runtime.deploy.bedrock
  - Container (ECS/EKS) use the provided Dockerfile

Install:
    pip install "arc-runtime[aws]"
"""
