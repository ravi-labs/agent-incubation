"""
ArcAgentStack — AWS CDK infrastructure for one arc agent.

Creates the full AWS resource set needed to run an agent in production:
  - Lambda function (agent execution)
  - DynamoDB table (approval store — persistent human review queue)
  - SQS queue (human review notification + dead-letter queue)
  - S3 bucket (audit logs, policy storage)
  - KMS key (encryption at rest for DDB, SQS, S3)
  - IAM roles (task role with least-privilege, execution role)
  - CloudWatch log group (structured JSON logs, 90-day retention)
  - EventBridge rule (optional scheduled trigger)
  - Bedrock model access (IAM policy for Claude invocations)
"""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import (
    Duration,
    RemovalPolicy,
    Stack,
)
from aws_cdk import aws_dynamodb as dynamodb
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_events as events
from aws_cdk import aws_events_targets as targets
from aws_cdk import aws_iam as iam
from aws_cdk import aws_kms as kms
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_sqs as sqs
from constructs import Construct


class ArcAgentStack(Stack):
    """
    Complete infrastructure stack for one arc-incubated agent.

    Instantiate once per agent. Teams apply this stack per environment
    (sandbox vs. production) by passing the environment parameter.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        agent_id: str,
        ecr_image_uri: str,
        environment: str = "sandbox",
        approval_timeout: int = 3600,
        schedule_expression: str | None = None,  # e.g. "rate(1 hour)"
        vpc_id: str | None = None,
        enable_bedrock: bool = True,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.agent_id    = agent_id
        self.environment = environment
        slug             = agent_id.replace("-", "")   # for resource names

        is_production = environment == "production"
        removal       = RemovalPolicy.RETAIN if is_production else RemovalPolicy.DESTROY

        # ── Tags (applied to all resources in this stack) ─────────────────────
        cdk.Tags.of(self).add("arc:agent-id",        agent_id)
        cdk.Tags.of(self).add("arc:environment",     environment)
        cdk.Tags.of(self).add("arc:managed-by",      "agent-arc-cdk")

        # ── VPC ───────────────────────────────────────────────────────────────
        if vpc_id:
            vpc = ec2.Vpc.from_lookup(self, "VPC", vpc_id=vpc_id)
        else:
            vpc = None  # Lambda without VPC (simpler; use vpc_id for private subnets)

        # ── KMS key (shared encryption for all arc resources) ─────────────
        self.key = kms.Key(
            self, "ArcKey",
            alias=f"arc/{agent_id}",
            description=f"arc agent encryption key — {agent_id}",
            enable_key_rotation=True,
            removal_policy=removal,
        )

        # ── S3 — audit logs and policy storage ───────────────────────────────
        self.audit_bucket = s3.Bucket(
            self, "AuditBucket",
            bucket_name=f"arc-{slug}-audit-{self.account}",
            encryption=s3.BucketEncryption.KMS,
            encryption_key=self.key,
            versioned=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="archive-old-logs",
                    transitions=[
                        s3.Transition(
                            storage_class=s3.StorageClass.INFREQUENT_ACCESS,
                            transition_after=Duration.days(90),
                        ),
                        # ERISA §107 requires 6-year retention
                        s3.Transition(
                            storage_class=s3.StorageClass.GLACIER,
                            transition_after=Duration.days(365),
                        ),
                    ],
                    expiration=Duration.days(365 * 7),   # 7 years to be safe
                )
            ],
            removal_policy=removal,
        )

        # ── DynamoDB — approval store ─────────────────────────────────────────
        self.approvals_table = dynamodb.Table(
            self, "ApprovalsTable",
            table_name=f"arc-{agent_id}-approvals",
            partition_key=dynamodb.Attribute(
                name="approval_id",
                type=dynamodb.AttributeType.STRING,
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            encryption=dynamodb.TableEncryption.CUSTOMER_MANAGED,
            encryption_key=self.key,
            time_to_live_attribute="ttl",
            point_in_time_recovery=is_production,
            removal_policy=removal,
        )

        # GSI for querying pending approvals by agent
        self.approvals_table.add_global_secondary_index(
            index_name="status-index",
            partition_key=dynamodb.Attribute(
                name="status",
                type=dynamodb.AttributeType.STRING,
            ),
            sort_key=dynamodb.Attribute(
                name="created_at",
                type=dynamodb.AttributeType.STRING,
            ),
        )

        # ── SQS — human review queue ──────────────────────────────────────────
        dlq = sqs.Queue(
            self, "ReviewDLQ",
            queue_name=f"arc-{agent_id}-review-dlq",
            encryption=sqs.QueueEncryption.KMS,
            encryption_master_key=self.key,
            retention_period=Duration.days(14),
        )

        self.review_queue = sqs.Queue(
            self, "ReviewQueue",
            queue_name=f"arc-{agent_id}-review",
            encryption=sqs.QueueEncryption.KMS,
            encryption_master_key=self.key,
            visibility_timeout=Duration.seconds(approval_timeout + 60),
            retention_period=Duration.days(7),
            dead_letter_queue=sqs.DeadLetterQueue(
                max_receive_count=3,
                queue=dlq,
            ),
        )

        # ── CloudWatch log group ──────────────────────────────────────────────
        self.log_group = logs.LogGroup(
            self, "LogGroup",
            log_group_name=f"/arc/agents/{agent_id}",
            retention=logs.RetentionDays.THREE_MONTHS,
            encryption_key=self.key,
            removal_policy=removal,
        )

        # ── IAM — agent task role ─────────────────────────────────────────────
        self.agent_role = iam.Role(
            self, "AgentRole",
            role_name=f"arc-{agent_id}-agent",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            description=f"arc agent execution role — {agent_id}",
        )

        # CloudWatch Logs
        self.agent_role.add_to_policy(iam.PolicyStatement(
            sid="AllowCloudWatchLogs",
            actions=["logs:CreateLogStream", "logs:PutLogEvents"],
            resources=[self.log_group.log_group_arn + ":*"],
        ))

        # DynamoDB — approval store (read/write/query)
        self.approvals_table.grant_read_write_data(self.agent_role)

        # SQS — send to review queue
        self.review_queue.grant_send_messages(self.agent_role)

        # S3 — write audit logs
        self.audit_bucket.grant_write(self.agent_role, "audit/*")

        # KMS — encrypt/decrypt all arc resources
        self.key.grant_encrypt_decrypt(self.agent_role)

        # Secrets Manager — read agent secrets (pattern: arc/{agent_id}/*)
        self.agent_role.add_to_policy(iam.PolicyStatement(
            sid="AllowSecretsRead",
            actions=["secretsmanager:GetSecretValue"],
            resources=[
                f"arn:aws:secretsmanager:{self.region}:{self.account}"
                f":secret:arc/{agent_id}/*"
            ],
        ))

        # SSM Parameter Store — read agent parameters
        self.agent_role.add_to_policy(iam.PolicyStatement(
            sid="AllowSSMRead",
            actions=["ssm:GetParameter", "ssm:GetParametersByPath"],
            resources=[
                f"arn:aws:ssm:{self.region}:{self.account}"
                f":parameter/arc/{agent_id}/*"
            ],
        ))

        # Bedrock — invoke Claude models
        if enable_bedrock:
            self.agent_role.add_to_policy(iam.PolicyStatement(
                sid="AllowBedrockInvoke",
                actions=["bedrock:InvokeModel"],
                resources=[
                    # Claude Sonnet 3.5 v2 — default model
                    f"arn:aws:bedrock:{self.region}::foundation-model/"
                    f"anthropic.claude-3-5-sonnet-20241022-v2:0",
                    # Claude Haiku 3.5 — for low-cost calls
                    f"arn:aws:bedrock:{self.region}::foundation-model/"
                    f"anthropic.claude-3-5-haiku-20241022-v1:0",
                ],
            ))

        # ── Lambda function ───────────────────────────────────────────────────
        lambda_env: dict[str, str] = {
            "ARC_ENV":              environment,
            "ARC_MANIFEST_PATH":    "manifest.yaml",
            "ARC_POLICY_DIR":       "policies/",
            "ARC_LOG_LEVEL":        "INFO",
            "ARC_APPROVALS_TABLE":  self.approvals_table.table_name,
            "ARC_REVIEW_QUEUE_URL": self.review_queue.queue_url,
            "ARC_APPROVAL_TIMEOUT": str(approval_timeout),
            "ARC_AUDIT_BUCKET":     self.audit_bucket.bucket_name,
            "AWS_REGION":               self.region,
        }

        if ecr_image_uri:
            fn_code = lambda_.Code.from_ecr_image(
                repository=None,     # type: ignore[arg-type]
                tag_or_digest=ecr_image_uri.split(":")[-1] if ":" in ecr_image_uri else "latest",
            ) if False else None  # placeholder — use from_docker_image_asset in real CDK apps
            # ☝️ In practice, use:
            # lambda_.DockerImageCode.from_ecr(ecr_repo, tag="latest")

        self.lambda_fn = lambda_.Function(
            self, "AgentFunction",
            function_name=f"arc-{agent_id}",
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler="handler.handler",
            code=lambda_.Code.from_asset("../../"),   # agent package directory
            role=self.agent_role,
            environment=lambda_env,
            timeout=Duration.minutes(15),
            memory_size=1024,
            log_group=self.log_group,
            tracing=lambda_.Tracing.ACTIVE,           # X-Ray tracing
            **({"vpc": vpc, "vpc_subnets": ec2.SubnetSelection(
                subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS
            )} if vpc else {}),
        )

        # ── EventBridge scheduled trigger (optional) ──────────────────────────
        if schedule_expression:
            rule = events.Rule(
                self, "ScheduleRule",
                rule_name=f"arc-{agent_id}-schedule",
                description=f"Scheduled trigger for {agent_id}",
                schedule=events.Schedule.expression(schedule_expression),
            )
            rule.add_target(targets.LambdaFunction(self.lambda_fn))

        # ── Outputs ───────────────────────────────────────────────────────────
        cdk.CfnOutput(self, "LambdaFunctionArn",
            value=self.lambda_fn.function_arn,
            description="Agent Lambda function ARN",
        )
        cdk.CfnOutput(self, "ApprovalsTableName",
            value=self.approvals_table.table_name,
            description="DynamoDB approvals table",
        )
        cdk.CfnOutput(self, "ReviewQueueUrl",
            value=self.review_queue.queue_url,
            description="SQS human review queue URL",
        )
        cdk.CfnOutput(self, "AuditBucketName",
            value=self.audit_bucket.bucket_name,
            description="S3 audit log bucket",
        )
