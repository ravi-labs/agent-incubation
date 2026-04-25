# Deploying to Amazon Bedrock Agent Core

Bedrock Agent Core is AWS's managed runtime for production agents.
It handles scaling, IAM, VPC networking, and CloudWatch integration automatically.

## When to use Bedrock Agent Core

Use it for **Scale-stage agents** (lifecycle_stage: SCALE) that have passed
compliance review. For Discover → Govern stages, deploy to ECS Fargate or Lambda
so you retain full control over the runtime environment.

## How it works with Arc

```
Bedrock Agent Core
  │
  │  Action Group invocation
  ▼
AWS Lambda (your agent container)
  │
  │  arc.runtime.deploy.lambda_handler
  ▼
BaseAgent.execute()
  │
  │  run_effect() — every tool call
  ▼
Tollgate ControlTower
  │
  ├── ALLOW → execute
  ├── ASK   → Human Review Queue
  └── DENY  → raise TollgateDenied
```

Tollgate enforcement runs **inside** the Lambda, not as a Bedrock layer.
This means your ERISA/DOL policies are enforced even when Bedrock orchestrates
the agent — Bedrock cannot bypass the policy engine.

## Setup steps

### 1. Generate the Action Group schema

```python
from arc.runtime.deploy.bedrock import generate_action_schema, upload_schema_to_s3
from arc.core import AgentManifest

manifest = AgentManifest.from_yaml("manifest.yaml")
schema = generate_action_schema(manifest)

# Upload to S3
s3_uri = upload_schema_to_s3(
    schema,
    bucket="your-arc-schemas-bucket",
    key=f"agents/{manifest.agent_id}/schema.json",
)
```

### 2. Wrap your Lambda response

```python
# In your Lambda handler
from arc.runtime.deploy.lambda_handler import make_handler
from arc.runtime.deploy.bedrock import BedrockAgentAdapter
from my_agents.fiduciary_watchdog import FiduciaryWatchdogAgent

_handler = make_handler(FiduciaryWatchdogAgent)
_adapter = BedrockAgentAdapter(manifest)

def handler(event, context):
    result = _handler.handler(event, context)
    # Wrap for Bedrock if invoked from an Action Group
    if "actionGroup" in event:
        return _adapter.format_response(event, result)
    return result
```

### 3. Register in Bedrock (boto3)

```python
import boto3

bedrock = boto3.client("bedrock-agent")

# Create the agent
agent = bedrock.create_agent(
    agentName=manifest.agent_id,
    description=f"Managed by arc | owner: {manifest.owner}",
    foundationModel="anthropic.claude-3-5-sonnet-20241022-v2:0",
    agentResourceRoleArn="arn:aws:iam::ACCOUNT:role/arc-bedrock-agent-role",
)

# Add the action group
bedrock.create_agent_action_group(
    agentId=agent["agent"]["agentId"],
    agentVersion="DRAFT",
    actionGroupName=f"{manifest.agent_id}-effects",
    actionGroupExecutor={"lambda": "arn:aws:lambda:REGION:ACCOUNT:function:AGENT_FUNCTION"},
    apiSchema={"s3": {"s3BucketName": "your-bucket", "s3ObjectKey": f"agents/{manifest.agent_id}/schema.json"}},
)
```

## IAM roles

**arc-bedrock-agent-role** (Bedrock assumes this):
- `bedrock:InvokeModel`
- `lambda:InvokeFunction` on your agent Lambda

**arc-agent-task-role** (Lambda/ECS runs as this):
- `ssm:GetParameter` for secrets
- `logs:CreateLogStream`, `logs:PutLogEvents`
- Any data access your agent needs (scoped tightly per manifest.data_access)

## Lifecycle rule

Only agents at **lifecycle_stage: SCALE** should be registered in Bedrock Agent Core.
The `agent.promote` effect is a hard DENY in the policy engine — promotion
to Scale requires compliance officer sign-off via the registry PR process.
