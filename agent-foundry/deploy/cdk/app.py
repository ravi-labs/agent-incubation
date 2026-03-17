#!/usr/bin/env python3
"""
Agent Foundry — AWS CDK App

Deploy the full platform infrastructure for one agent.

Usage:
    pip install aws-cdk-lib constructs
    export AWS_ACCOUNT=123456789012
    export AWS_REGION=us-east-1
    cdk deploy --context agent_id=fiduciary-watchdog \
               --context ecr_image_uri=<ECR_URI>/fiduciary-watchdog:latest

Context variables:
    agent_id          (required) Agent ID — used for all resource naming
    ecr_image_uri     (required) ECR image URI for the agent Lambda/ECS container
    environment       sandbox | production  (default: sandbox)
    approval_timeout  Seconds to wait for human review  (default: 3600)
    vpc_id            Existing VPC to deploy into (optional — creates new if not set)
    enable_bedrock    true | false — grant Bedrock model access  (default: true)
"""

import aws_cdk as cdk
from foundry_stack import FoundryAgentStack

app = cdk.App()

agent_id         = app.node.try_get_context("agent_id")         or "my-agent"
ecr_image_uri    = app.node.try_get_context("ecr_image_uri")    or ""
environment      = app.node.try_get_context("environment")      or "sandbox"
approval_timeout = int(app.node.try_get_context("approval_timeout") or "3600")
vpc_id           = app.node.try_get_context("vpc_id")
enable_bedrock   = (app.node.try_get_context("enable_bedrock") or "true").lower() == "true"

FoundryAgentStack(
    app,
    f"Foundry-{agent_id.title().replace('-', '')}",
    agent_id=agent_id,
    ecr_image_uri=ecr_image_uri,
    environment=environment,
    approval_timeout=approval_timeout,
    vpc_id=vpc_id,
    enable_bedrock=enable_bedrock,
    env=cdk.Environment(
        account=app.node.try_get_context("account"),
        region=app.node.try_get_context("region"),
    ),
)

app.synth()
