"""
foundry.deploy.bedrock
───────────────────────
Amazon Bedrock Agent Core adapter for agent-foundry.

Bedrock Agent Core invokes your agent via Action Groups — each Action Group
maps to a Lambda function with a defined OpenAPI schema. This module:

  1. Generates the Action Group OpenAPI schema from your agent's manifest
     (effects → API operations, with ERISA/DOL annotations)

  2. Provides a Bedrock-compatible Lambda response formatter so your
     FoundryLambdaHandler output maps cleanly to what Bedrock expects

  3. Provides helpers for configuring the Bedrock Agent via boto3

Install:
    pip install "agent-foundry[aws]"

Usage:

    # In your team repo — generate the OpenAPI schema for Bedrock:
    from foundry.deploy.bedrock import BedrockAgentAdapter, generate_action_schema
    from foundry.scaffold.manifest import AgentManifest

    manifest = AgentManifest.from_yaml("manifest.yaml")
    schema   = generate_action_schema(manifest)

    # Returns an OpenAPI 3.0 dict — register this as a Bedrock Action Group

    # In your Lambda handler — wrap the response for Bedrock:
    adapter = BedrockAgentAdapter(manifest)
    bedrock_response = adapter.format_response(event, result)
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


class BedrockAgentAdapter:
    """
    Formats agent responses for Bedrock Agent Core's expected structure.

    Bedrock expects Lambda responses in the form:
    {
        "messageVersion": "1.0",
        "response": {
            "actionGroup": "...",
            "function": "...",
            "functionResponse": {
                "responseBody": {
                    "application/json": { "body": "..." }
                }
            }
        }
    }
    """

    def __init__(self, manifest: Any):
        self.manifest = manifest

    def format_response(self, event: dict, result: Any) -> dict:
        """
        Wrap a foundry agent result into Bedrock Agent Core's response format.

        Args:
            event:  The original Bedrock invocation event.
            result: The result from agent.execute() (or FoundryLambdaHandler).

        Returns:
            A dict in Bedrock Agent Core Lambda response format.
        """
        action_group = event.get("actionGroup", self.manifest.agent_id)
        function     = event.get("function",    "execute")

        # If result is already a FoundryLambdaHandler response envelope, unwrap it
        if isinstance(result, dict) and "result" in result:
            payload = result["result"]
            status  = result.get("statusCode", 200)
        else:
            payload = result
            status  = 200

        # Map foundry status codes to Bedrock response state
        response_state = "FAILURE" if status >= 400 else "SUCCESS"

        body = json.dumps(payload, default=str)

        return {
            "messageVersion": "1.0",
            "response": {
                "actionGroup": action_group,
                "function":    function,
                "functionResponse": {
                    "responseState": response_state,
                    "responseBody": {
                        "application/json": {"body": body}
                    },
                },
            },
        }


def generate_action_schema(manifest: Any) -> dict:
    """
    Generate a Bedrock Agent Core Action Group OpenAPI 3.0 schema
    from an AgentManifest.

    The schema is derived from the agent's declared effects — each tier-4+
    effect (Output, Persistence, System) becomes an API operation with
    compliance annotations in the description.

    Returns:
        An OpenAPI 3.0 dict suitable for uploading to Bedrock as an
        inline schema or S3 object.
    """
    from foundry.policy.effects import EFFECT_METADATA, FinancialEffect

    agent_id    = manifest.agent_id
    description = f"{agent_id} — managed by agent-foundry incubation platform"

    paths: dict[str, Any] = {}

    for effect_value in manifest.allowed_effects:
        try:
            effect = FinancialEffect(effect_value)
        except ValueError:
            continue

        meta = EFFECT_METADATA.get(effect, {})
        tier = meta.get("tier", 0)

        # Only expose output/persistence/system effects as Bedrock operations
        # (data access and computation are internal — not called by Bedrock directly)
        if tier < 4:
            continue

        operation_id = effect_value.replace(".", "_")
        path         = f"/{operation_id}"
        default      = meta.get("default", "ALLOW")
        description_text = (
            f"{meta.get('description', effect_value)}. "
            f"Default policy: {default}. "
            f"Tier {tier} effect — enforced by Tollgate ControlTower."
        )

        paths[path] = {
            "post": {
                "operationId": operation_id,
                "summary":     meta.get("description", effect_value),
                "description": description_text,
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "description": f"Parameters for {effect_value}",
                            }
                        }
                    },
                },
                "responses": {
                    "200": {
                        "description": "Effect executed successfully",
                        "content": {
                            "application/json": {
                                "schema": {"type": "object"}
                            }
                        },
                    },
                    "403": {"description": "Effect denied by policy"},
                    "202": {"description": "Effect deferred — queued for human approval"},
                },
            }
        }

    return {
        "openapi": "3.0.0",
        "info": {
            "title":   agent_id,
            "version": manifest.version,
            "description": description,
            "x-foundry-lifecycle-stage": manifest.lifecycle_stage.value,
            "x-foundry-owner":           manifest.owner,
            "x-tollgate-policy-path":    manifest.policy_path,
        },
        "paths": paths,
    }


def upload_schema_to_s3(schema: dict, bucket: str, key: str) -> str:
    """
    Upload an Action Group schema to S3 for use with Bedrock Agent Core.

    Args:
        schema: OpenAPI 3.0 dict from generate_action_schema().
        bucket: S3 bucket name.
        key:    S3 object key (e.g., "agents/fiduciary-watchdog/schema.json").

    Returns:
        S3 URI of the uploaded schema (s3://bucket/key).
    """
    try:
        import boto3
    except ImportError as exc:
        raise ImportError(
            "boto3 is not installed. Run: pip install 'agent-foundry[aws]'"
        ) from exc

    s3 = boto3.client("s3")
    body = json.dumps(schema, indent=2)
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=body.encode(),
        ContentType="application/json",
    )
    uri = f"s3://{bucket}/{key}"
    logger.info("Schema uploaded to %s", uri)
    return uri
