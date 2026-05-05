"""
DatadogForwarderConstruct — CloudWatch Logs → Datadog forwarder.

Why this exists
---------------
PR #17 wired CloudWatch EMF + Datadog DogStatsD as the two telemetry
targets. EMF metrics + structured logs go to CloudWatch automatically
in any Lambda or ECS-with-awslogs deployment. The Datadog half only
works out-of-the-box when a Datadog Agent / Lambda Extension is
present.

For Lambda-without-extension deployments (the most common case for
teams that already have Datadog), the standard pattern is:

    CloudWatch Logs ──► Datadog Forwarder Lambda ──► Datadog API
                         (datadog/serverless-mac)

This construct deploys that forwarder and subscribes the arc agent's
log group to it. One construct, one ``cdk deploy``, working Datadog.

Usage alongside ArcAgentStack
-----------------------------
    from arc_stack            import ArcAgentStack
    from datadog_forwarder    import DatadogForwarderConstruct

    app = cdk.App()

    infra = ArcAgentStack(app, "EmailTriageInfra",
        agent_id      = "email-triage",
        environment   = "production",
        ecr_image_uri = "<ECR_URI>/email-triage:latest",
    )

    DatadogForwarderConstruct(
        infra, "DatadogForwarder",
        log_groups          = [infra.log_group],
        dd_api_key_secret   = "arc/datadog/api-key",   # SecretsManager name or ARN
        dd_site             = "datadoghq.com",          # or datadoghq.eu, ddog-gov.com
        environment         = "production",
    )

Pre-requisites
--------------
- A SecretsManager secret holding the Datadog API key. Create once
  per account; multiple agents reuse it.
- The Datadog forwarder Lambda is the upstream-published artefact at
  https://github.com/DataDog/datadog-serverless-functions

The construct uses the publicly-versioned ZIP from datadog-cloudformation-template
S3 bucket. Pin via ``forwarder_version`` for reproducibility.

Reference
---------
  https://docs.datadoghq.com/logs/guide/forwarder/
  https://docs.datadoghq.com/serverless/forwarder/
"""

from __future__ import annotations

from typing import Sequence

import aws_cdk as cdk
from aws_cdk import (
    Duration,
    RemovalPolicy,
)
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_logs_destinations as logs_destinations
from aws_cdk import aws_secretsmanager as secretsmanager
from constructs import Construct


# Pinned to a recent stable Datadog forwarder version. Bump when you
# verify a newer release passes your integration tests.
_DEFAULT_FORWARDER_VERSION = "3.115.0"
_DEFAULT_DATADOG_SITE      = "datadoghq.com"


class DatadogForwarderConstruct(Construct):
    """Deploys the Datadog Forwarder Lambda and subscribes log groups.

    The forwarder Lambda reads CloudWatch logs (including EMF metric
    payloads), unpacks them, and POSTs to Datadog's intake API. Once
    deployed, every line your agent writes to stdout is queryable
    from Datadog logs + every EMF metric becomes a Datadog metric.

    Args:
        scope:               Parent construct (typically the
                             ``ArcAgentStack`` itself).
        construct_id:        CDK construct ID (must be unique per parent).
        log_groups:          The CloudWatch log groups to subscribe.
                             Pass ``[infra.log_group]`` for one agent;
                             a list for several agents in the same
                             stack.
        dd_api_key_secret:   SecretsManager secret name OR full ARN
                             holding the Datadog API key. Create the
                             secret in the same region as the stack.
        dd_site:             Datadog site (default ``datadoghq.com``).
                             Use ``datadoghq.eu`` for EU customers,
                             ``ddog-gov.com`` for GovCloud.
        environment:         Tag value (``production`` / ``sandbox``)
                             so the same Datadog account can host
                             multiple environments cleanly.
        forwarder_version:   Pin to a specific Datadog forwarder
                             release. Defaults to a known-good version.
        forwarder_memory_mb: Lambda memory size. Default 1024 MB —
                             adequate for the volume one arc agent
                             generates. Bump if you subscribe many
                             log groups.
        log_retention_days:  How long the forwarder's own logs are
                             retained in CloudWatch. Default 7 days.
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        log_groups: Sequence[logs.ILogGroup],
        dd_api_key_secret: str,
        dd_site: str = _DEFAULT_DATADOG_SITE,
        environment: str = "sandbox",
        forwarder_version: str = _DEFAULT_FORWARDER_VERSION,
        forwarder_memory_mb: int = 1024,
        log_retention_days: int = 7,
    ) -> None:
        super().__init__(scope, construct_id)

        if not log_groups:
            raise ValueError(
                "DatadogForwarderConstruct needs at least one log group "
                "to subscribe."
            )

        # ── Resolve the Datadog API-key secret ────────────────────────────
        # Accept either a bare secret name or a full ARN. The forwarder
        # Lambda only needs read access to the secret value.
        if dd_api_key_secret.startswith("arn:"):
            secret = secretsmanager.Secret.from_secret_complete_arn(
                self, "DatadogApiKeySecret", dd_api_key_secret,
            )
        else:
            secret = secretsmanager.Secret.from_secret_name_v2(
                self, "DatadogApiKeySecret", dd_api_key_secret,
            )

        # ── Forwarder Lambda role ─────────────────────────────────────────
        role = iam.Role(
            self, "ForwarderRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                ),
            ],
            description="Role for the Datadog CloudWatch Logs forwarder",
        )
        secret.grant_read(role)

        # ── Pull the published forwarder Lambda code ──────────────────────
        # Datadog publishes versioned zips at
        # https://github.com/DataDog/datadog-serverless-functions/releases
        # CDK fetches via the Lambda CfnFunction asset, but using
        # ``CfnApplication`` keeps us aligned with how Datadog distributes.
        # We use ``Code.from_bucket`` against the official artefact bucket.
        code = lambda_.Code.from_bucket(
            bucket=self._datadog_artefact_bucket(),
            key=f"aws/forwarder/{forwarder_version}.zip",
        )

        # ── Forwarder Lambda function ─────────────────────────────────────
        self.forwarder = lambda_.Function(
            self, "Forwarder",
            runtime         = lambda_.Runtime.PYTHON_3_11,
            handler         = "lambda_function.lambda_handler",
            code            = code,
            role            = role,
            memory_size     = forwarder_memory_mb,
            timeout         = Duration.minutes(2),
            log_retention   = logs.RetentionDays.ONE_WEEK
                              if log_retention_days <= 7
                              else logs.RetentionDays.ONE_MONTH,
            environment     = {
                "DD_SITE":             dd_site,
                "DD_API_KEY_SECRET_ARN": secret.secret_arn,
                "DD_ENV":              environment,
                "DD_TAGS":             f"env:{environment},source:arc",
                "DD_ENHANCED_METRICS": "true",
            },
            description = (
                "Forwards CloudWatch logs + EMF metrics from arc agents "
                "to Datadog. Reads the API key from SecretsManager at "
                "cold-start."
            ),
        )

        cdk.Tags.of(self.forwarder).add("arc:component", "datadog-forwarder")
        cdk.Tags.of(self.forwarder).add("arc:environment", environment)

        # ── Subscribe each agent log group ───────────────────────────────
        # Subscription filter "" matches every log line. The forwarder
        # decides what to do based on EMF envelope vs. plain log line.
        for i, lg in enumerate(log_groups):
            logs.SubscriptionFilter(
                self, f"Subscription{i}",
                log_group        = lg,
                destination      = logs_destinations.LambdaDestination(self.forwarder),
                filter_pattern   = logs.FilterPattern.all_events(),
                filter_name      = f"datadog-{environment}",
            )

        cdk.CfnOutput(
            self, "ForwarderArn",
            value       = self.forwarder.function_arn,
            description = "Datadog forwarder Lambda ARN",
            export_name = f"arc-datadog-forwarder-{environment}",
        )

    # ── Helpers ────────────────────────────────────────────────────────

    def _datadog_artefact_bucket(self):
        """Return the regional Datadog forwarder artefact bucket.

        Datadog publishes the forwarder Lambda zip to a public bucket
        per region. The naming convention is documented at
        https://docs.datadoghq.com/serverless/forwarder/.
        """
        from aws_cdk import aws_s3 as s3
        # Public bucket, no IAM needed; CDK will make a versioned read.
        return s3.Bucket.from_bucket_name(
            self, "DatadogArtefactBucket",
            bucket_name="datadog-cloudformation-template",
        )
