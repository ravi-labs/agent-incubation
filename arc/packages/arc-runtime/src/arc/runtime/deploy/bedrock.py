"""
foundry.deploy.bedrock
───────────────────────
Amazon Bedrock Agent Core adapter for agent-foundry.

Bedrock Agent Core invokes your agent via Action Groups using one of two
invocation models:

  API-based (OpenAPI schema):
      Bedrock sends: actionGroup, apiPath, httpMethod, requestBody, parameters,
                     sessionAttributes, promptSessionAttributes

  Function-based (inline):
      Bedrock sends: actionGroup, function, parameters,
                     sessionAttributes, promptSessionAttributes

This module handles all of the above:

  1. BedrockEventParser — normalises both event shapes into clean **kwargs
     for agent.execute(), extracting session context as first-class metadata.

  2. BedrockAgentAdapter — formats agent responses back to Bedrock's expected
     structure, handles ASK→CONFIRM mapping, and error serialisation.

  3. generate_action_schema() — derives an OpenAPI 3.0 Action Group schema
     from the agent manifest's declared effects (Tier 4+ → API operations).

  4. upload_schema_to_s3() — uploads the schema to S3 for Bedrock registration.

  5. register_bedrock_agent() — boto3 helper that creates the Bedrock Agent,
     Action Group, Lambda permission, and alias in one call.

Install:
    pip install "agent-foundry[aws]"

Bedrock event shapes handled by BedrockEventParser:

    # API-based action group
    {
        "messageVersion": "1.0",
        "agent": {"name": "...", "id": "...", "alias": "TSTALIASID", "version": "DRAFT"},
        "inputText": "Compute risk score for participant p-001",
        "sessionId": "123456",
        "actionGroup": "RetirementActions",
        "apiPath": "/risk_score_compute",
        "httpMethod": "POST",
        "parameters": [{"name": "participant_id", "type": "string", "value": "p-001"}],
        "requestBody": {
            "content": {
                "application/json": {
                    "properties": [{"name": "participant_id", "type": "string", "value": "p-001"}]
                }
            }
        },
        "sessionAttributes": {"plan_id": "plan-001"},
        "promptSessionAttributes": {"user_role": "compliance_officer"}
    }

    # Function-based action group
    {
        "messageVersion": "1.0",
        "agent": {"name": "...", "id": "...", "alias": "TSTALIASID", "version": "DRAFT"},
        "actionGroup": "RetirementActions",
        "function": "compute_risk_score",
        "parameters": [{"name": "participant_id", "type": "string", "value": "p-001"}],
        "sessionAttributes": {"plan_id": "plan-001"},
        "promptSessionAttributes": {}
    }
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# ── Bedrock Event Parser ───────────────────────────────────────────────────────


class BedrockEventParser:
    """
    Parses Bedrock Agent Core Lambda events into clean Python kwargs for
    agent.execute(), and extracts session context as first-class metadata.

    Handles both API-based (OpenAPI schema) and Function-based action groups.

    Usage:
        parsed = BedrockEventParser.parse(event)
        # parsed.kwargs   → passed to agent.execute(**kwargs)
        # parsed.session  → sessionAttributes dict
        # parsed.prompt   → promptSessionAttributes dict
        # parsed.meta     → raw Bedrock metadata (agent, actionGroup, etc.)
        # parsed.input_text → the user's original prompt text
    """

    def __init__(
        self,
        kwargs: dict,
        session: dict,
        prompt: dict,
        meta: dict,
        input_text: str,
        action_group: str,
        operation: str,
        invocation_type: str,   # "api" | "function"
    ):
        self.kwargs         = kwargs
        self.session        = session
        self.prompt         = prompt
        self.meta           = meta
        self.input_text     = input_text
        self.action_group   = action_group
        self.operation      = operation
        self.invocation_type = invocation_type

    @classmethod
    def parse(cls, event: dict) -> "BedrockEventParser":
        """
        Parse a raw Bedrock Agent Core invocation event.

        Args:
            event: The raw Lambda event dict from Bedrock.

        Returns:
            BedrockEventParser with extracted kwargs, session context, and metadata.

        Raises:
            ValueError: If the event is not a recognised Bedrock event shape.
        """
        if "actionGroup" not in event:
            raise ValueError(
                "Not a Bedrock Agent Core event — missing 'actionGroup' field"
            )

        action_group = event.get("actionGroup", "")
        session      = dict(event.get("sessionAttributes",       {}))
        prompt       = dict(event.get("promptSessionAttributes", {}))
        input_text   = event.get("inputText", "")

        # ── Bedrock agent metadata ────────────────────────────────────────────
        agent_meta = event.get("agent", {})
        meta = {
            "bedrock_agent_name":    agent_meta.get("name"),
            "bedrock_agent_id":      agent_meta.get("id"),
            "bedrock_agent_alias":   agent_meta.get("alias"),
            "bedrock_agent_version": agent_meta.get("version"),
            "bedrock_session_id":    event.get("sessionId"),
            "action_group":          action_group,
            "message_version":       event.get("messageVersion", "1.0"),
        }

        # ── Parameters: shared by both API and function invocations ───────────
        raw_params = event.get("parameters", [])
        params = cls._extract_params(raw_params)

        # ── Detect invocation type ────────────────────────────────────────────
        if "function" in event and "apiPath" not in event:
            # Function-based action group
            operation       = event.get("function", "execute")
            invocation_type = "function"
            meta["function"] = operation

            kwargs = {**params}

        else:
            # API-based action group
            api_path        = event.get("apiPath", "/execute")
            http_method     = event.get("httpMethod", "POST")
            operation       = api_path.lstrip("/")
            invocation_type = "api"
            meta["api_path"]     = api_path
            meta["http_method"]  = http_method

            # Merge requestBody properties into params (POST body takes precedence)
            body_params = cls._extract_request_body(event.get("requestBody", {}))
            kwargs = {**params, **body_params}

        # ── Inject session context so agents can access plan/participant IDs ──
        # Session attributes become first-class kwargs so agents don't need
        # to know about the Bedrock event format at all.
        #
        # Example:  sessionAttributes = {"plan_id": "plan-001"}
        #   → plan_id="plan-001" is available in agent.execute(**kwargs)
        #
        # Prompt session attributes are prefixed with "_prompt_" to avoid
        # collisions (they're LLM context, not domain data).
        for k, v in session.items():
            kwargs.setdefault(k, v)   # session attrs don't override explicit params

        for k, v in prompt.items():
            kwargs.setdefault(f"_prompt_{k}", v)

        # ── Inject Bedrock metadata as _bedrock context ───────────────────────
        kwargs["_bedrock"] = meta

        logger.debug(
            "bedrock_event_parsed action_group=%s operation=%s type=%s params=%s",
            action_group, operation, invocation_type, list(params.keys()),
        )

        return cls(
            kwargs=kwargs,
            session=session,
            prompt=prompt,
            meta=meta,
            input_text=input_text,
            action_group=action_group,
            operation=operation,
            invocation_type=invocation_type,
        )

    @staticmethod
    def _extract_params(raw_params: list) -> dict:
        """
        Convert Bedrock's parameter list to a plain dict.

        Handles type coercion:
            "integer" / "number" → int/float
            "boolean"            → bool
            "string"             → str (default)
        """
        result: dict = {}
        for p in raw_params:
            name  = p.get("name",  "")
            value = p.get("value", "")
            ptype = p.get("type",  "string").lower()

            if ptype == "integer":
                try:
                    value = int(value)
                except (ValueError, TypeError):
                    pass
            elif ptype == "number":
                try:
                    value = float(value)
                except (ValueError, TypeError):
                    pass
            elif ptype == "boolean":
                value = str(value).lower() in ("true", "1", "yes")

            if name:
                result[name] = value

        return result

    @staticmethod
    def _extract_request_body(request_body: dict) -> dict:
        """
        Extract parameters from a Bedrock API-based requestBody.

        Bedrock encodes the body as:
            {"content": {"application/json": {"properties": [...]}}}
        """
        content = request_body.get("content", {})
        json_content = content.get("application/json", {})
        properties   = json_content.get("properties", [])

        result: dict = {}
        for prop in properties:
            name  = prop.get("name", "")
            value = prop.get("value", "")
            ptype = prop.get("type", "string").lower()

            if ptype == "integer":
                try:
                    value = int(value)
                except (ValueError, TypeError):
                    pass
            elif ptype == "number":
                try:
                    value = float(value)
                except (ValueError, TypeError):
                    pass
            elif ptype == "boolean":
                value = str(value).lower() in ("true", "1", "yes")

            if name:
                result[name] = value

        return result


# ── Bedrock Agent Adapter ──────────────────────────────────────────────────────


class BedrockAgentAdapter:
    """
    Formats agent responses and errors for Bedrock Agent Core's expected structure.

    Bedrock expects Lambda responses in the form:
    {
        "messageVersion": "1.0",
        "response": {
            "actionGroup": "...",
            "apiPath": "...",           # API-based only
            "httpMethod": "...",        # API-based only
            "httpStatusCode": 200,
            "responseBody": {
                "application/json": {"body": "<JSON string>"}
            }
        },
        "sessionAttributes": {...},
        "promptSessionAttributes": {...}
    }

    For Function-based action groups:
    {
        "messageVersion": "1.0",
        "response": {
            "actionGroup": "...",
            "function": "...",
            "functionResponse": {
                "responseState": "SUCCESS",
                "responseBody": {
                    "application/json": {"body": "<JSON string>"}
                }
            }
        },
        "sessionAttributes": {...},
        "promptSessionAttributes": {...}
    }
    """

    def __init__(self, manifest: Any):
        self.manifest = manifest

    def format_response(
        self,
        parsed_event: "BedrockEventParser",
        result: Any,
        session_updates: dict | None = None,
    ) -> dict:
        """
        Wrap a foundry agent result into Bedrock Agent Core's response format.

        Args:
            parsed_event:    BedrockEventParser from parse_event().
            result:          The result from agent.execute().
            session_updates: Optional dict to merge into sessionAttributes
                             (e.g., updated participant state).

        Returns:
            A dict in Bedrock Agent Core Lambda response format.
        """
        # Build response body
        if isinstance(result, dict) and "statusCode" in result:
            # Foundry envelope — unwrap it
            payload     = result.get("result", result)
            status_code = result.get("statusCode", 200)
        else:
            payload     = result
            status_code = 200

        body_str = json.dumps(payload, default=str)

        # Build updated session attributes
        session = {**parsed_event.session}
        if session_updates:
            session.update(session_updates)

        if parsed_event.invocation_type == "function":
            response = {
                "messageVersion": "1.0",
                "response": {
                    "actionGroup": parsed_event.action_group,
                    "function":    parsed_event.operation,
                    "functionResponse": {
                        "responseState": "SUCCESS" if status_code < 400 else "FAILURE",
                        "responseBody": {
                            "application/json": {"body": body_str}
                        },
                    },
                },
                "sessionAttributes":       session,
                "promptSessionAttributes": parsed_event.prompt,
            }
        else:
            # API-based
            response = {
                "messageVersion": "1.0",
                "response": {
                    "actionGroup":    parsed_event.action_group,
                    "apiPath":        parsed_event.meta.get("api_path", f"/{parsed_event.operation}"),
                    "httpMethod":     parsed_event.meta.get("http_method", "POST"),
                    "httpStatusCode": status_code,
                    "responseBody": {
                        "application/json": {"body": body_str}
                    },
                },
                "sessionAttributes":       session,
                "promptSessionAttributes": parsed_event.prompt,
            }

        logger.debug(
            "bedrock_response action_group=%s operation=%s status=%d",
            parsed_event.action_group, parsed_event.operation, status_code,
        )
        return response

    def format_error(
        self,
        parsed_event: "BedrockEventParser",
        error: Exception,
        status_code: int = 500,
    ) -> dict:
        """
        Format an error response for Bedrock Agent Core.

        PermissionError (Tollgate DENY or kill switch) → 403
        General exceptions                             → 500
        """
        error_payload = {
            "error":   type(error).__name__,
            "message": str(error),
        }
        body_str = json.dumps(error_payload)

        if parsed_event.invocation_type == "function":
            return {
                "messageVersion": "1.0",
                "response": {
                    "actionGroup": parsed_event.action_group,
                    "function":    parsed_event.operation,
                    "functionResponse": {
                        "responseState": "FAILURE",
                        "responseBody": {
                            "application/json": {"body": body_str}
                        },
                    },
                },
                "sessionAttributes":       parsed_event.session,
                "promptSessionAttributes": parsed_event.prompt,
            }
        else:
            return {
                "messageVersion": "1.0",
                "response": {
                    "actionGroup":    parsed_event.action_group,
                    "apiPath":        parsed_event.meta.get("api_path", f"/{parsed_event.operation}"),
                    "httpMethod":     parsed_event.meta.get("http_method", "POST"),
                    "httpStatusCode": status_code,
                    "responseBody": {
                        "application/json": {"body": body_str}
                    },
                },
                "sessionAttributes":       parsed_event.session,
                "promptSessionAttributes": parsed_event.prompt,
            }

    def format_confirmation_request(
        self,
        parsed_event: "BedrockEventParser",
        message: str,
        metadata: dict | None = None,
    ) -> dict:
        """
        Format a human confirmation request for Bedrock Agent Core.

        When Tollgate raises TollgateDeferred (ASK decision pending human review),
        return this response to tell Bedrock to pause and ask the user to confirm
        before proceeding.

        Bedrock will display the message to the user and wait for confirmation.
        The agent Lambda will be re-invoked with the user's response.

        Args:
            parsed_event: The event that triggered the ASK.
            message:      Human-readable description of what needs approval.
            metadata:     Additional context for the reviewer.

        Returns:
            Bedrock Agent Core confirmation response dict.
        """
        confirmation_payload = {
            "confirmationState": "CONFIRM_REQUIRED",
            "message": message,
            **(metadata or {}),
        }
        body_str = json.dumps(confirmation_payload)

        if parsed_event.invocation_type == "function":
            return {
                "messageVersion": "1.0",
                "response": {
                    "actionGroup": parsed_event.action_group,
                    "function":    parsed_event.operation,
                    "functionResponse": {
                        "responseState": "REPROMPT",
                        "responseBody": {
                            "application/json": {"body": body_str}
                        },
                    },
                },
                "sessionAttributes":       parsed_event.session,
                "promptSessionAttributes": parsed_event.prompt,
            }
        else:
            return {
                "messageVersion": "1.0",
                "response": {
                    "actionGroup":    parsed_event.action_group,
                    "apiPath":        parsed_event.meta.get("api_path", f"/{parsed_event.operation}"),
                    "httpMethod":     parsed_event.meta.get("http_method", "POST"),
                    "httpStatusCode": 202,  # Accepted — pending confirmation
                    "responseBody": {
                        "application/json": {"body": body_str}
                    },
                },
                "sessionAttributes":       parsed_event.session,
                "promptSessionAttributes": parsed_event.prompt,
            }


# ── Schema generation ──────────────────────────────────────────────────────────


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
    from arc.core.effects import EFFECT_METADATA, FinancialEffect

    agent_id    = manifest.agent_id
    description = (
        f"{manifest.description or agent_id} — "
        f"managed by agent-foundry incubation platform. "
        f"All operations are policy-enforced via Tollgate ControlTower."
    )

    paths: dict[str, Any] = {}

    for effect_value in manifest.allowed_effects:
        try:
            effect = FinancialEffect(effect_value)
        except ValueError:
            continue

        meta = EFFECT_METADATA.get(effect)
        if meta is None:
            continue

        tier = getattr(meta, "tier", None)
        if tier is None or tier.value < 4:
            continue

        operation_id     = effect_value.replace(".", "_")
        path             = f"/{operation_id}"
        default_decision = getattr(meta, "default_decision", None)
        default_str      = default_decision.value if default_decision else "ALLOW"
        requires_review  = getattr(meta, "requires_human_review", False)

        description_text = (
            f"Tier {tier.value} effect — default policy: {default_str}. "
            f"{'Requires human review before execution. ' if requires_review else ''}"
            f"Enforced by Tollgate ControlTower inside Lambda."
        )

        paths[path] = {
            "post": {
                "operationId":  operation_id,
                "summary":      effect_value,
                "description":  description_text,
                "x-foundry-effect": effect_value,
                "x-foundry-tier":   tier.value,
                "x-tollgate-default-decision": default_str,
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
                    "202": {
                        "description": "Effect deferred — pending human approval (ASK decision)",
                    },
                    "403": {
                        "description": "Effect denied by policy or agent kill switch",
                    },
                    "500": {"description": "Internal agent error"},
                },
            }
        }

    return {
        "openapi": "3.0.0",
        "info": {
            "title":       agent_id,
            "version":     manifest.version,
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

    s3   = boto3.client("s3")
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


# ── Bedrock Agent Registration ─────────────────────────────────────────────────


def register_bedrock_agent(
    *,
    manifest: Any,
    lambda_arn: str,
    agent_role_arn: str,
    schema_s3_bucket: str,
    schema_s3_key: str,
    foundation_model: str = "anthropic.claude-3-5-sonnet-20241022-v2:0",
    region: str | None = None,
    create_alias: bool = True,
    alias_name: str = "production",
) -> dict:
    """
    Register a foundry agent with Amazon Bedrock Agent Core via boto3.

    This function:
      1. Creates the Bedrock Agent (or updates if it exists)
      2. Associates the Lambda function as an Action Group
      3. Uploads the OpenAPI schema to S3
      4. Prepares the agent (compiles the agent graph in Bedrock)
      5. Optionally creates a stable alias for the production stage

    Args:
        manifest:          AgentManifest — source of agent_id, description, etc.
        lambda_arn:        ARN of the Lambda function to use as the action group handler.
        agent_role_arn:    ARN of the IAM role Bedrock will assume to invoke Lambda.
        schema_s3_bucket:  S3 bucket to upload the OpenAPI schema to.
        schema_s3_key:     S3 key for the OpenAPI schema.
        foundation_model:  Bedrock foundation model ID (Claude Sonnet default).
        region:            AWS region (default: from environment).
        create_alias:      Whether to create a stable alias after preparation.
        alias_name:        Name for the production alias (default: "production").

    Returns:
        dict with keys:
            agent_id:    Bedrock Agent ID (not the foundry agent_id)
            agent_arn:   Full ARN of the Bedrock Agent
            alias_id:    ID of the created alias (if create_alias=True)
            alias_arn:   Full ARN of the alias (if create_alias=True)
            schema_uri:  S3 URI of the uploaded schema
    """
    try:
        import boto3
    except ImportError as exc:
        raise ImportError(
            "boto3 is not installed. Run: pip install 'agent-foundry[aws]'"
        ) from exc

    bedrock_client = boto3.client("bedrock-agent", region_name=region)

    # Step 1 — Upload OpenAPI schema
    schema     = generate_action_schema(manifest)
    schema_uri = upload_schema_to_s3(schema, schema_s3_bucket, schema_s3_key)
    logger.info("Schema uploaded: %s", schema_uri)

    # Step 2 — Create (or retrieve) the Bedrock Agent
    agent_name   = manifest.agent_id
    description  = manifest.description or f"{manifest.agent_id} — managed by agent-foundry"

    try:
        create_resp = bedrock_client.create_agent(
            agentName=agent_name,
            agentResourceRoleArn=agent_role_arn,
            description=description[:200],  # Bedrock max 200 chars
            foundationModel=foundation_model,
            instruction=(
                f"You are {manifest.agent_id}, a governed financial-services agent "
                f"managed by the agent-foundry incubation platform. "
                f"All actions are policy-enforced by Tollgate ControlTower. "
                f"Owner: {manifest.owner}."
            ),
            idleSessionTTLInSeconds=1800,
        )
        bedrock_agent_id  = create_resp["agent"]["agentId"]
        bedrock_agent_arn = create_resp["agent"]["agentArn"]
        logger.info("Bedrock Agent created: %s (%s)", agent_name, bedrock_agent_id)

    except bedrock_client.exceptions.ConflictException:
        # Agent already exists — list and find it
        agents = bedrock_client.list_agents(maxResults=100)["agentSummaries"]
        existing = next((a for a in agents if a["agentName"] == agent_name), None)
        if not existing:
            raise RuntimeError(f"Conflict creating Bedrock Agent '{agent_name}' but could not find existing")
        bedrock_agent_id  = existing["agentId"]
        bedrock_agent_arn = existing.get("agentArn", "")
        logger.info("Using existing Bedrock Agent: %s (%s)", agent_name, bedrock_agent_id)

    # Step 3 — Create Action Group
    op_count = len(schema.get("paths", {}))
    bedrock_client.create_agent_action_group(
        agentId=bedrock_agent_id,
        agentVersion="DRAFT",
        actionGroupName=f"{agent_name}-actions",
        actionGroupExecutor={"lambda": lambda_arn},
        apiSchema={
            "s3": {
                "s3BucketName": schema_s3_bucket,
                "s3ObjectKey":  schema_s3_key,
            }
        },
        description=f"{op_count} policy-enforced operations from {agent_name}",
    )
    logger.info("Action Group created with %d operations", op_count)

    # Step 4 — Prepare the agent (compile in Bedrock)
    bedrock_client.prepare_agent(agentId=bedrock_agent_id)
    logger.info("Agent prepared (DRAFT version compiled)")

    # Step 5 — Create production alias
    result: dict[str, Any] = {
        "agent_id":  bedrock_agent_id,
        "agent_arn": bedrock_agent_arn,
        "schema_uri": schema_uri,
    }

    if create_alias:
        alias_resp = bedrock_client.create_agent_alias(
            agentId=bedrock_agent_id,
            agentAliasName=alias_name,
            description=f"Production alias for {agent_name}@{manifest.version}",
        )
        result["alias_id"]  = alias_resp["agentAlias"]["agentAliasId"]
        result["alias_arn"] = alias_resp["agentAlias"]["agentAliasArn"]
        logger.info(
            "Alias '%s' created: %s",
            alias_name, result["alias_id"],
        )

    return result
