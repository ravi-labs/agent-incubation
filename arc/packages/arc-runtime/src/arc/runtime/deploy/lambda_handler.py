"""
arc.runtime.deploy.lambda_handler
──────────────────────────────────
Wraps any BaseAgent as an AWS Lambda function.

Cold-start wires up the manifest, ControlTower, Gateway, and (optionally)
production backends — DynamoDB approval store, SQS approver, and Secrets Manager.
Warm invocations reuse the initialised agent.

Usage — in your agent repo, create handler.py:

    from arc.runtime.deploy.lambda_handler import make_handler
    from my_agents.fiduciary_watchdog import FiduciaryWatchdogAgent

    handler = make_handler(FiduciaryWatchdogAgent)

    # handler.handler is your Lambda entry point

Environment variables:
    ARC_MANIFEST_PATH      Path to manifest.yaml          (default: manifest.yaml)
    ARC_POLICY_DIR         Path to policies/              (default: policies/)
    ARC_ENV                sandbox | production            (default: sandbox)
    ARC_LOG_LEVEL          Python log level               (default: INFO)

    # Production backends (auto-enabled when set)
    ARC_APPROVALS_TABLE    DynamoDB table name for approvals
    ARC_REVIEW_QUEUE_URL   SQS queue URL for human review
    ARC_APPROVAL_TIMEOUT   Seconds to wait for review     (default: 3600)

    # Secrets (loaded from Secrets Manager / SSM at cold start)
    ARC_SECRET_<KEY>       Secrets Manager secret name    → injected as env var
    ARC_PARAM_<KEY>        SSM Parameter Store name       → injected as env var
"""

from __future__ import annotations

import json
import logging
import os
import time
import traceback
from typing import Any

logger = logging.getLogger(__name__)


def make_handler(agent_class: type, **agent_kwargs: Any):
    """
    Create a Lambda handler for the given agent class.

    Args:
        agent_class:    A BaseAgent subclass to run.
        **agent_kwargs: Extra kwargs forwarded to agent_class.__init__().

    Returns:
        An object with a .handler(event, context) → dict method.
    """
    return _FoundryLambdaHandler(agent_class, **agent_kwargs)


class _FoundryLambdaHandler:
    """Lambda handler — manages agent lifecycle across warm invocations."""

    def __init__(self, agent_class: type, **agent_kwargs: Any):
        self._agent_class  = agent_class
        self._agent_kwargs = agent_kwargs
        self._agent: Any   = None

        # Structured logging for CloudWatch Logs Insights
        log_level = os.environ.get("ARC_LOG_LEVEL", "INFO")
        logging.basicConfig(
            level=getattr(logging, log_level, logging.INFO),
            format='{"time":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","message":"%(message)s"}',
        )

    def _init_agent(self) -> None:
        """Cold-start: wire manifest, tower, gateway, and production backends."""
        manifest_path      = os.environ.get("ARC_MANIFEST_PATH", "manifest.yaml")
        policy_dir         = os.environ.get("ARC_POLICY_DIR",    "policies/")
        environment        = os.environ.get("ARC_ENV",           "sandbox")
        approvals_table    = os.environ.get("ARC_APPROVALS_TABLE")
        review_queue_url   = os.environ.get("ARC_REVIEW_QUEUE_URL")
        approval_timeout   = float(os.environ.get("ARC_APPROVAL_TIMEOUT", "3600"))
        region             = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")

        logger.info(
            "arc_cold_start agent_class=%s manifest=%s env=%s",
            self._agent_class.__name__, manifest_path, environment,
        )

        # ── Load secrets before touching anything that needs credentials ──────
        self._load_secrets(region)

        # ── Manifest ──────────────────────────────────────────────────────────
        from arc.core.manifest import AgentManifest
        manifest = AgentManifest.from_yaml(manifest_path)
        manifest.environment = environment

        # ── Policy evaluator ─────────────────────────────────────────────────
        from tollgate import YamlPolicyEvaluator
        policy = YamlPolicyEvaluator(policy_dir)

        # ── Approver: production (SQS+DDB) or sandbox (auto) ─────────────────
        if approvals_table and review_queue_url:
            logger.info(
                "arc_backend production approvals_table=%s queue=%s",
                approvals_table, review_queue_url.split("/")[-1],
            )
            from tollgate.backends.dynamodb_store import DynamoDBApprovalStore
            from tollgate.backends.sqs_approver   import SQSApprover
            store    = DynamoDBApprovalStore(table_name=approvals_table, region=region)
            approver = SQSApprover(
                queue_url=review_queue_url,
                store=store,
                timeout=approval_timeout,
                region=region,
            )
        elif approvals_table:
            # DynamoDB only — polling-based (no SQS notification)
            logger.info("arc_backend ddb-only approvals_table=%s", approvals_table)
            from tollgate.backends.dynamodb_store import DynamoDBApprovalStore
            from tollgate.approvals import AsyncQueueApprover
            store    = DynamoDBApprovalStore(table_name=approvals_table, region=region)
            approver = AsyncQueueApprover(store=store, timeout=approval_timeout)
        else:
            # Sandbox — auto-approver (never use in production)
            if environment == "production":
                logger.warning(
                    "arc_warning ARC_APPROVALS_TABLE not set in production — "
                    "ASK decisions will use AutoApprover. Set ARC_APPROVALS_TABLE."
                )
            from tollgate import AutoApprover
            approver = AutoApprover()

        # ── Audit sink ────────────────────────────────────────────────────────
        from tollgate import JsonlAuditSink
        audit_path = f"/tmp/audit-{manifest.agent_id}.jsonl"
        audit = JsonlAuditSink(audit_path)

        # ── ControlTower ──────────────────────────────────────────────────────
        from tollgate.tower import ControlTower
        tower = ControlTower(policy=policy, approver=approver, audit=audit)

        # ── Gateway ───────────────────────────────────────────────────────────
        # GatewayConnector is a Protocol — a real connector must be supplied.
        # Pass it via make_handler(MyAgent, gateway=HttpGateway("https://..."))
        # or via agent_kwargs={"gateway": my_connector}.
        if "gateway" not in self._agent_kwargs:
            raise ValueError(
                "No gateway provided to make_handler(). "
                "Pass a real connector: make_handler(MyAgent, gateway=HttpGateway('https://your-api'))."
            )
        gateway = self._agent_kwargs.pop("gateway")

        # ── Outcome tracker ───────────────────────────────────────────────────
        from arc.core.observability import OutcomeTracker
        tracker = OutcomeTracker(path=f"/tmp/outcomes-{manifest.agent_id}.jsonl")

        # ── Build agent ───────────────────────────────────────────────────────
        self._agent = self._agent_class(
            manifest=manifest,
            tower=tower,
            gateway=gateway,
            tracker=tracker,
            **self._agent_kwargs,
        )

        logger.info(
            "arc_agent_ready agent=%s version=%s stage=%s env=%s",
            manifest.agent_id, manifest.version,
            manifest.lifecycle_stage.value, environment,
        )

    @staticmethod
    def _load_secrets(region: str | None) -> None:
        """
        Load ARC_SECRET_* and ARC_PARAM_* environment variables
        from Secrets Manager / SSM and inject them as env vars.

        Pattern:
            ARC_SECRET_DB_URL=arc/my-agent/db-url
              → fetches the secret and sets DB_URL in os.environ

            ARC_PARAM_SLACK_URL=/arc/my-agent/slack-webhook
              → fetches the parameter and sets SLACK_URL in os.environ
        """
        secret_vars = {
            k[len("ARC_SECRET_"):]: v
            for k, v in os.environ.items()
            if k.startswith("ARC_SECRET_")
        }
        param_vars = {
            k[len("ARC_PARAM_"):]: v
            for k, v in os.environ.items()
            if k.startswith("ARC_PARAM_")
        }

        if not secret_vars and not param_vars:
            return

        try:
            from arc.runtime.deploy.secrets import FoundrySecrets
            secrets = FoundrySecrets(region=region)

            for env_key, secret_name in secret_vars.items():
                value = secrets.get_secret(secret_name)
                os.environ[env_key] = value
                logger.debug("Injected secret → %s", env_key)

            for env_key, param_name in param_vars.items():
                value = secrets.get_parameter(param_name)
                os.environ[env_key] = value
                logger.debug("Injected parameter → %s", env_key)

        except ImportError:
            logger.debug("Secrets loading skipped — boto3 not installed")

    # ── Lambda entry point ─────────────────────────────────────────────────────

    def handler(self, event: dict, context: Any) -> dict:
        """
        Lambda entry point.

        Supported event shapes:
          1. Direct invocation:   {"param": "value", ...}
          2. EventBridge:         {"detail-type": "...", "detail": {...}}
          3. Scheduled EventBridge: {"source": "aws.events", ...}
          4. SQS trigger:         {"Records": [{"body": "{...}"}]}
          5. Bedrock Agent Core (API-based):
             {"actionGroup": "...", "apiPath": "/...", "httpMethod": "POST",
              "parameters": [...], "requestBody": {...},
              "sessionAttributes": {...}, "promptSessionAttributes": {...}}
          6. Bedrock Agent Core (Function-based):
             {"actionGroup": "...", "function": "...",
              "parameters": [...],
              "sessionAttributes": {...}, "promptSessionAttributes": {...}}
        """
        import asyncio

        cold_start = self._agent is None
        start_time = time.monotonic()

        if cold_start:
            self._init_agent()

        request_id = getattr(context, "aws_request_id", "local")
        agent_id   = self._agent.manifest.agent_id

        is_bedrock = "actionGroup" in event

        logger.info(
            "arc_invoke agent=%s request_id=%s cold_start=%s bedrock=%s",
            agent_id, request_id, cold_start, is_bedrock,
        )

        try:
            loop = asyncio.new_event_loop()

            if is_bedrock:
                result = loop.run_until_complete(
                    self._invoke_bedrock(event, context, loop)
                )
            else:
                kwargs = self._normalise_event(event)
                result = loop.run_until_complete(self._agent.execute(**kwargs))
                loop.close()

                duration_ms = round((time.monotonic() - start_time) * 1000)
                logger.info(
                    "arc_complete agent=%s request_id=%s duration_ms=%d",
                    agent_id, request_id, duration_ms,
                )
                result = {
                    "statusCode": 200,
                    "agent":      agent_id,
                    "request_id": request_id,
                    "result":     result,
                }

        except PermissionError as exc:
            logger.error("arc_permission_denied agent=%s: %s", agent_id, exc)
            result = {"statusCode": 403, "error": "permission_denied", "message": str(exc)}

        except Exception as exc:  # noqa: BLE001
            logger.error(
                "arc_error agent=%s: %s\n%s",
                agent_id, exc, traceback.format_exc(),
            )
            result = {"statusCode": 500, "error": type(exc).__name__, "message": str(exc)}

        return result

    async def _invoke_bedrock(self, event: dict, context: Any, loop: Any) -> dict:
        """
        Handle a Bedrock Agent Core invocation with proper event parsing,
        session attribute extraction, and response formatting.

        Maps Tollgate decisions to Bedrock responses:
          ALLOW    → 200 SUCCESS
          ASK      → 202 REPROMPT (confirmation request to user)
          DENY     → 403 FAILURE
          Error    → 500 FAILURE
        """
        from arc.runtime.deploy.bedrock import BedrockAgentAdapter, BedrockEventParser

        try:
            from tollgate.exceptions import TollgateDenied, TollgateDeferred
            _has_tollgate_exceptions = True
        except ImportError:
            _has_tollgate_exceptions = False

        agent_id = self._agent.manifest.agent_id

        # ── Parse Bedrock event ───────────────────────────────────────────────
        try:
            parsed = BedrockEventParser.parse(event)
        except ValueError as exc:
            logger.error("bedrock_parse_error agent=%s: %s", agent_id, exc)
            return {
                "messageVersion": "1.0",
                "response": {
                    "actionGroup":    event.get("actionGroup", "unknown"),
                    "httpStatusCode": 400,
                    "responseBody": {"application/json": {"body": json.dumps({"error": str(exc)})}},
                },
            }

        adapter = BedrockAgentAdapter(self._agent.manifest)

        logger.info(
            "arc_bedrock_invoke agent=%s action_group=%s operation=%s "
            "session_keys=%s type=%s",
            agent_id, parsed.action_group, parsed.operation,
            list(parsed.session.keys()), parsed.invocation_type,
        )

        # ── Execute ───────────────────────────────────────────────────────────
        try:
            result = await self._agent.execute(**parsed.kwargs)
            return adapter.format_response(parsed, result)

        except PermissionError as exc:
            # Tollgate DENY or kill switch
            logger.warning(
                "arc_bedrock_denied agent=%s operation=%s: %s",
                agent_id, parsed.operation, exc,
            )
            return adapter.format_error(parsed, exc, status_code=403)

        except Exception as exc:
            # Check if this is a TollgateDeferred (ASK decision pending review)
            if _has_tollgate_exceptions:
                try:
                    from tollgate.exceptions import TollgateDeferred
                    if isinstance(exc, TollgateDeferred):
                        logger.info(
                            "arc_bedrock_ask agent=%s operation=%s",
                            agent_id, parsed.operation,
                        )
                        return adapter.format_confirmation_request(
                            parsed,
                            message=(
                                f"This action requires human approval: {parsed.operation}. "
                                f"A review request has been submitted. "
                                f"Please confirm once the review is complete."
                            ),
                            metadata={"operation": parsed.operation, "agent": agent_id},
                        )
                except ImportError:
                    pass

            logger.error(
                "arc_bedrock_error agent=%s operation=%s: %s\n%s",
                agent_id, parsed.operation, exc, traceback.format_exc(),
            )
            return adapter.format_error(parsed, exc, status_code=500)

    @staticmethod
    def _normalise_event(event: dict) -> dict:
        """Normalise non-Bedrock Lambda event shapes into execute() kwargs."""

        # SQS — single-item batches recommended
        if "Records" in event:
            record = event["Records"][0]
            body = record.get("body", "{}")
            parsed = json.loads(body) if isinstance(body, str) else body
            # Strip foundry envelope if present (e.g., from SQSApprover notification)
            if "arc_event" in parsed:
                return {"event": parsed}
            return parsed

        # EventBridge (scheduled or custom)
        if "detail" in event:
            detail = event["detail"]
            return detail if isinstance(detail, dict) else {"detail": detail}

        # Scheduled EventBridge with no detail
        if event.get("source") == "aws.events":
            return {}

        # Direct invocation — pass through
        return event


# ── Lambda Response Streaming ───────────────────────────────────────────────────


def make_streaming_handler(agent_class: type, **agent_kwargs: Any):
    """
    Create a Lambda Response Streaming handler for the given agent class.

    Lambda Response Streaming lets your function progressively return data
    to the caller as it is produced, rather than buffering the full result.

    This handler calls agent.execute_stream() if it exists (should return an
    async generator of JSON-serialisable chunks), otherwise falls back to
    agent.execute() and returns a single chunk.

    Usage — in your agent repo, create handler.py:

        from arc.runtime.deploy.lambda_handler import make_streaming_handler
        from my_agents.streaming_agent import StreamingAgent

        streaming_handler = make_streaming_handler(StreamingAgent)
        # Configure Lambda with InvokeMode: RESPONSE_STREAM

    Your agent can implement execute_stream() to support true streaming:

        class StreamingAgent(BaseAgent):
            async def execute_stream(self, **kwargs):
                async for chunk in self._generate_chunks(**kwargs):
                    yield chunk          # each chunk is JSON-serialisable

            async def execute(self, **kwargs):
                return [c async for c in self.execute_stream(**kwargs)]

    The streaming handler sends each chunk as a newline-delimited JSON (NDJSON)
    byte sequence, compatible with most streaming consumers.

    Args:
        agent_class:    A BaseAgent subclass.
        **agent_kwargs: Extra kwargs forwarded to agent_class.__init__().

    Returns:
        A _FoundryStreamingHandler with a .handler(event, context, response_stream)
        method compatible with Lambda's streaming invocation.
    """
    return _FoundryStreamingHandler(agent_class, **agent_kwargs)


class _FoundryStreamingHandler(_FoundryLambdaHandler):
    """
    Lambda Response Streaming handler — sends NDJSON chunks progressively.

    Inherits cold-start wiring from _FoundryLambdaHandler. The handler()
    method signature extends the standard Lambda handler with response_stream.

    Deployment notes:
        - The Lambda function must be configured with InvokeMode: RESPONSE_STREAM
          in the function URL configuration or via the CLI:
            aws lambda put-function-event-invoke-config ...
        - Use awslambdaric >= 1.1.0 which provides the streaming response_stream
          context manager via @streaming_response decorator.
        - Memory: streaming handlers need at least 256 MB for stable throughput.

    The response_stream protocol:
        response_stream.write(bytes)  — write a byte chunk
        response_stream.close()       — signal completion (called automatically)
    """

    def streaming_handler(self, event: dict, context: Any, response_stream: Any) -> None:
        """
        Lambda Response Streaming entry point.

        Writes NDJSON (newline-delimited JSON) to response_stream.
        Each line is a complete JSON object — either a data chunk or an error.

        Format:
            {"type": "chunk",    "data": <any>, "index": 0}
            {"type": "chunk",    "data": <any>, "index": 1}
            ...
            {"type": "complete", "agent": "...", "total_chunks": N}

        Errors:
            {"type": "error", "error": "...", "message": "..."}
        """
        import asyncio

        cold_start = self._agent is None
        if cold_start:
            self._init_agent()

        kwargs   = self._normalise_event(event)
        agent_id = self._agent.manifest.agent_id

        loop = asyncio.new_event_loop()
        try:
            loop.run_until_complete(
                self._stream_to_response(kwargs, agent_id, response_stream)
            )
        finally:
            loop.close()

    async def _stream_to_response(
        self,
        kwargs: dict,
        agent_id: str,
        response_stream: Any,
    ) -> None:
        """Write agent output chunks to the Lambda response stream."""
        index = 0

        def _write_chunk(obj: Any) -> None:
            nonlocal index
            line = json.dumps({"type": "chunk", "data": obj, "index": index}, default=str)
            response_stream.write((line + "\n").encode())
            index += 1

        try:
            if hasattr(self._agent, "execute_stream"):
                # Agent supports native async streaming
                async for chunk in self._agent.execute_stream(**kwargs):
                    _write_chunk(chunk)
            else:
                # Fallback: run execute() and return single chunk
                result = await self._agent.execute(**kwargs)
                _write_chunk(result)

            # Final completion event
            done = json.dumps({
                "type":         "complete",
                "agent":        agent_id,
                "total_chunks": index,
            })
            response_stream.write((done + "\n").encode())

        except PermissionError as exc:
            err = json.dumps({"type": "error", "error": "permission_denied", "message": str(exc)})
            response_stream.write((err + "\n").encode())

        except Exception as exc:  # noqa: BLE001
            logger.error("arc_stream_error agent=%s: %s", agent_id, exc)
            err = json.dumps({"type": "error", "error": type(exc).__name__, "message": str(exc)})
            response_stream.write((err + "\n").encode())
