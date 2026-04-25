"""
foundry.deploy.cdk.bedrock_agent_stack
────────────────────────────────────────
CDK construct that creates the Amazon Bedrock Agent and its dependencies.

Usage alongside FoundryAgentStack:

    from foundry_stack import FoundryAgentStack
    from bedrock_agent_stack import BedrockAgentConstruct

    app = cdk.App()

    infra = FoundryAgentStack(app, "FiduciaryWatchdogInfra",
        agent_id="fiduciary-watchdog",
        environment="production",
        ecr_image_uri="<ECR_URI>/fiduciary-watchdog:latest",
    )

    BedrockAgentConstruct(
        app, "FiduciaryWatchdogBedrock",
        agent_id="fiduciary-watchdog",
        agent_name="Fiduciary Watchdog",
        description="Monitors ERISA §404(a) compliance for plan fund lineups",
        lambda_function=infra.lambda_fn,
        schema_s3_bucket=infra.audit_bucket,
        schema_s3_key="agents/fiduciary-watchdog/schema.json",
        environment="production",
    )

    app.synth()

The BedrockAgentConstruct:
  - Creates foundry-bedrock-agent-role (IAM role that Bedrock assumes)
  - Creates the AWS::Bedrock::Agent resource
  - Creates an Action Group linked to the Lambda function
  - Prepares the agent (DRAFT → PREPARED)
  - Creates a production alias

Pre-requisites:
  - Bedrock model access granted in the AWS account
  - Foundation model access for claude-3-5-sonnet or claude-3-haiku
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import aws_cdk as cdk
from aws_cdk import aws_bedrock as bedrock
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_s3 as s3
from constructs import Construct

if TYPE_CHECKING:
    pass


class BedrockAgentConstruct(Construct):
    """
    CDK construct for an Amazon Bedrock Agent backed by a foundry Lambda handler.

    Creates:
      - IAM role for Bedrock to assume (bedrock-agent-role)
      - L1 CfnAgent resource with instruction and foundation model
      - Action Group linking agent_name → Lambda function
      - CfnAgentAlias for stable references (production, staging)

    All enforcement (Tollgate policy, ERISA/DOL rules) runs inside the
    Lambda function — Bedrock cannot bypass it.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        agent_id: str,
        agent_name: str,
        description: str,
        lambda_function: lambda_.IFunction,
        schema_s3_bucket: s3.IBucket,
        schema_s3_key: str,
        environment: str = "production",
        foundation_model: str = "anthropic.claude-3-5-sonnet-20241022-v2:0",
        alias_name: str = "production",
        instruction: str | None = None,
        idle_session_ttl: int = 1800,
    ) -> None:
        super().__init__(scope, construct_id)

        region  = cdk.Stack.of(self).region
        account = cdk.Stack.of(self).account

        # ── IAM role that Bedrock assumes to invoke the Lambda ─────────────────
        self.agent_role = iam.Role(
            self, "BedrockAgentRole",
            role_name=f"foundry-bedrock-{agent_id}-role",
            assumed_by=iam.ServicePrincipal(
                "bedrock.amazonaws.com",
                conditions={
                    "StringEquals": {"aws:SourceAccount": account},
                    "ArnLike": {"aws:SourceArn": f"arn:aws:bedrock:{region}:{account}:agent/*"},
                },
            ),
            description=f"Role assumed by Bedrock Agent Core to invoke {agent_id}",
        )

        # Allow Bedrock to invoke the foundry Lambda
        self.agent_role.add_to_policy(iam.PolicyStatement(
            sid="InvokeFoundryLambda",
            effect=iam.Effect.ALLOW,
            actions=["lambda:InvokeFunction"],
            resources=[
                lambda_function.function_arn,
                f"{lambda_function.function_arn}:*",
            ],
        ))

        # Allow Bedrock to use the foundation model
        self.agent_role.add_to_policy(iam.PolicyStatement(
            sid="InvokeFoundationModel",
            effect=iam.Effect.ALLOW,
            actions=["bedrock:InvokeModel"],
            resources=[
                f"arn:aws:bedrock:{region}::foundation-model/{foundation_model}",
                f"arn:aws:bedrock:{region}::foundation-model/anthropic.claude-3-haiku-20240307-v1:0",
            ],
        ))

        # Allow Bedrock to read the OpenAPI schema from S3
        schema_s3_bucket.grant_read(self.agent_role, schema_s3_key)

        # ── Grant Lambda permission to be invoked by Bedrock ──────────────────
        lambda_function.add_permission(
            "BedrockAgentInvoke",
            principal=iam.ServicePrincipal("bedrock.amazonaws.com"),
            action="lambda:InvokeFunction",
            source_account=account,
            source_arn=f"arn:aws:bedrock:{region}:{account}:agent/*",
        )

        # ── Instruction for the Bedrock Agent ────────────────────────────────
        _instruction = instruction or (
            f"You are {agent_name}, a governed AI agent for financial services. "
            f"You operate under ERISA §404(a) fiduciary standards and DOL regulations. "
            f"All your actions are policy-enforced by Tollgate ControlTower — "
            f"every tool call is audited and may require human review. "
            f"Never perform actions that are not covered by your declared effects. "
            f"Environment: {environment}."
        )

        # ── Bedrock Agent (L1 CfnAgent) ───────────────────────────────────────
        # Using L1 because the L2 construct is still experimental in CDK
        self.cfn_agent = bedrock.CfnAgent(
            self, "Agent",
            agent_name=agent_id,
            agent_resource_role_arn=self.agent_role.role_arn,
            description=description[:200],
            foundation_model=foundation_model,
            instruction=_instruction,
            idle_session_ttl_in_seconds=idle_session_ttl,
            auto_prepare=True,   # Automatically prepare DRAFT after update
            action_groups=[
                bedrock.CfnAgent.AgentActionGroupProperty(
                    action_group_name=f"{agent_id}-actions",
                    description=f"Policy-enforced operations from {agent_id} (Tollgate-governed)",
                    action_group_executor=bedrock.CfnAgent.ActionGroupExecutorProperty(
                        lambda_=lambda_function.function_arn,
                    ),
                    api_schema=bedrock.CfnAgent.APISchemaProperty(
                        s3=bedrock.CfnAgent.S3IdentifierProperty(
                            s3_bucket_name=schema_s3_bucket.bucket_name,
                            s3_object_key=schema_s3_key,
                        ),
                    ),
                )
            ],
        )

        # ── Agent Alias (production pointer) ──────────────────────────────────
        self.cfn_alias = bedrock.CfnAgentAlias(
            self, "ProductionAlias",
            agent_id=self.cfn_agent.attr_agent_id,
            agent_alias_name=alias_name,
            description=(
                f"Production alias for {agent_id} — "
                f"points to the prepared DRAFT after each deployment"
            ),
        )
        self.cfn_alias.add_dependency(self.cfn_agent)

        # ── CloudFormation Outputs ─────────────────────────────────────────────
        cdk.CfnOutput(self, "BedrockAgentId",
                      value=self.cfn_agent.attr_agent_id,
                      description=f"Bedrock Agent ID for {agent_id}")
        cdk.CfnOutput(self, "BedrockAgentArn",
                      value=self.cfn_agent.attr_agent_arn,
                      description=f"Bedrock Agent ARN for {agent_id}")
        cdk.CfnOutput(self, "BedrockAliasId",
                      value=self.cfn_alias.attr_agent_alias_id,
                      description=f"Production alias ID for {agent_id}")
        cdk.CfnOutput(self, "BedrockAgentRoleArn",
                      value=self.agent_role.role_arn,
                      description=f"IAM role ARN assumed by Bedrock Agent Core for {agent_id}")
