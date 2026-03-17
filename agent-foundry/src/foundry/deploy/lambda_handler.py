"""
foundry.deploy.lambda_handler
──────────────────────────────
Wraps any BaseAgent as an AWS Lambda function.

Cold-start wires up the manifest, ControlTower, Gateway, and (optionally)
production backends — DynamoDB approval store, SQS approver, and Secrets Manager.
Warm invocations reuse the initialised agent.

Usage — in your agent repo, create handler.py:

    from foundry.deploy.lambda_handler import make_handler
    from my_agents.fiduciary_watchdog import FiduciaryWatchdogAgent

    handler = make_handler(FiduciaryWatchdogAgent)

    # handler.handler is your Lambda entry point

Environment variables:
    FOUNDRY_MANIFEST_PATH      Path to manifest.yaml          (default: manifest.yaml)
    FOUNDRY_POLICY_DIR         Path to policies/              (default: policies/)
    FOUNDRY_ENV                sandbox | production            (default: sandbox)
    FOUNDRY_LOG_LEVEL          Python log level               (default: INFO)

    # Production backends (auto-enabled when set)
    FOUNDRY_APPROVALS_TABLE    DynamoDB table name for approvals
    FOUNDRY_REVIEW_QUEUE_URL   SQS queue URL for human review
    FOUNDRY_APPROVAL_TIMEOUT   Seconds to wait for review     (default: 3600)

    # Secrets (loaded from Secrets Manager / SSM at cold start)
    FOUNDRY_SECRET_<KEY>       Secrets Manager secret name    → injected as env var
    FOUNDRY_PARAM_<KEY>        SSM Parameter Store name       → injected as env var
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
        log_level = os.environ.get("FOUNDRY_LOG_LEVEL", "INFO")
        logging.basicConfig(
            level=getattr(logging, log_level, logging.INFO),
            format='{"time":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","message":"%(message)s"}',
        )

    def _init_agent(self) -> None:
        """Cold-start: wire manifest, tower, gateway, and production backends."""
        manifest_path      = os.environ.get("FOUNDRY_MANIFEST_PATH", "manifest.yaml")
        policy_dir         = os.environ.get("FOUNDRY_POLICY_DIR",    "policies/")
        environment        = os.environ.get("FOUNDRY_ENV",           "sandbox")
        approvals_table    = os.environ.get("FOUNDRY_APPROVALS_TABLE")
        review_queue_url   = os.environ.get("FOUNDRY_REVIEW_QUEUE_URL")
        approval_timeout   = float(os.environ.get("FOUNDRY_APPROVAL_TIMEOUT", "3600"))
        region             = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")

        logger.info(
            "foundry_cold_start agent_class=%s manifest=%s env=%s",
            self._agent_class.__name__, manifest_path, environment,
        )

        # ── Load secrets before touching anything that needs credentials ──────
        self._load_secrets(region)

        # ── Manifest ──────────────────────────────────────────────────────────
        from foundry.scaffold.manifest import AgentManifest
        manifest = AgentManifest.from_yaml(manifest_path)
        manifest.environment = environment

        # ── Policy evaluator ─────────────────────────────────────────────────
        from foundry.tollgate import YamlPolicyEvaluator
        policy = YamlPolicyEvaluator(policy_dir)

        # ── Approver: production (SQS+DDB) or sandbox (auto) ─────────────────
        if approvals_table and review_queue_url:
            logger.info(
                "foundry_backend production approvals_table=%s queue=%s",
                approvals_table, review_queue_url.split("/")[-1],
            )
            from foundry.tollgate.backends.dynamodb_store import DynamoDBApprovalStore
            from foundry.tollgate.backends.sqs_approver   import SQSApprover
            store    = DynamoDBApprovalStore(table_name=approvals_table, region=region)
            approver = SQSApprover(
                queue_url=review_queue_url,
                store=store,
                timeout=approval_timeout,
                region=region,
            )
        elif approvals_table:
            # DynamoDB only — polling-based (no SQS notification)
            logger.info("foundry_backend ddb-only approvals_table=%s", approvals_table)
            from foundry.tollgate.backends.dynamodb_store import DynamoDBApprovalStore
            from foundry.tollgate.approvals import AsyncQueueApprover
            store    = DynamoDBApprovalStore(table_name=approvals_table, region=region)
            approver = AsyncQueueApprover(store=store, timeout=approval_timeout)
        else:
            # Sandbox — auto-approver (never use in production)
            if environment == "production":
                logger.warning(
                    "foundry_warning FOUNDRY_APPROVALS_TABLE not set in production — "
                    "ASK decisions will use AutoApprover. Set FOUNDRY_APPROVALS_TABLE."
                )
            from foundry.tollgate import AutoApprover
            approver = AutoApprover()

        # ── Audit sink ────────────────────────────────────────────────────────
        from foundry.tollgate import JsonlAuditSink
        audit_path = f"/tmp/audit-{manifest.agent_id}.jsonl"
        audit = JsonlAuditSink(audit_path)

        # ── ControlTower ──────────────────────────────────────────────────────
        from foundry.tollgate.tower import ControlTower
        tower = ControlTower(policy=policy, approver=approver, audit=audit)

        # ── Gateway ───────────────────────────────────────────────────────────
        from foundry.gateway.base import GatewayConnector
        gateway = GatewayConnector()

        # ── Outcome tracker ───────────────────────────────────────────────────
        from foundry.observability import OutcomeTracker
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
            "foundry_agent_ready agent=%s version=%s stage=%s env=%s",
            manifest.agent_id, manifest.version,
            manifest.lifecycle_stage.value, environment,
        )

    @staticmethod
    def _load_secrets(region: str | None) -> None:
        """
        Load FOUNDRY_SECRET_* and FOUNDRY_PARAM_* environment variables
        from Secrets Manager / SSM and inject them as env vars.

        Pattern:
            FOUNDRY_SECRET_DB_URL=foundry/my-agent/db-url
              → fetches the secret and sets DB_URL in os.environ

            FOUNDRY_PARAM_SLACK_URL=/foundry/my-agent/slack-webhook
              → fetches the parameter and sets SLACK_URL in os.environ
        """
        secret_vars = {
            k[len("FOUNDRY_SECRET_"):]: v
            for k, v in os.environ.items()
            if k.startswith("FOUNDRY_SECRET_")
        }
        param_vars = {
            k[len("FOUNDRY_PARAM_"):]: v
            for k, v in os.environ.items()
            if k.startswith("FOUNDRY_PARAM_")
        }

        if not secret_vars and not param_vars:
            return

        try:
            from foundry.deploy.secrets import FoundrySecrets
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
          5. Bedrock Agent Core:  {"actionGroup": "...", "function": "...", "parameters": [...]}
        """
        import asyncio

        cold_start = self._agent is None
        start_time = time.monotonic()

        if cold_start:
            self._init_agent()

        request_id = getattr(context, "aws_request_id", "local")
        agent_id   = self._agent.manifest.agent_id

        logger.info(
            "foundry_invoke agent=%s request_id=%s cold_start=%s",
            agent_id, request_id, cold_start,
        )

        kwargs = self._normalise_event(event)

        try:
            loop = asyncio.new_event_loop()
            result = loop.run_until_complete(self._agent.execute(**kwargs))
            loop.close()

            duration_ms = round((time.monotonic() - start_time) * 1000)
            logger.info(
                "foundry_complete agent=%s request_id=%s duration_ms=%d",
                agent_id, request_id, duration_ms,
            )

            return {
                "statusCode": 200,
                "agent":      agent_id,
                "request_id": request_id,
                "result":     result,
            }

        except PermissionError as exc:
            logger.error("foundry_permission_denied agent=%s: %s", agent_id, exc)
            return {"statusCode": 403, "error": "permission_denied", "message": str(exc)}

        except Exception as exc:  # noqa: BLE001
            logger.error(
                "foundry_error agent=%s: %s\n%s",
                agent_id, exc, traceback.format_exc(),
            )
            return {"statusCode": 500, "error": type(exc).__name__, "message": str(exc)}

    @staticmethod
    def _normalise_event(event: dict) -> dict:
        """Normalise various Lambda event shapes into execute() kwargs."""

        # Bedrock Agent Core action group
        if "actionGroup" in event:
            params = {p["name"]: p["value"] for p in event.get("parameters", [])}
            return {"action": event.get("function", "execute"), **params}

        # SQS — single-item batches recommended
        if "Records" in event:
            record = event["Records"][0]
            body = record.get("body", "{}")
            parsed = json.loads(body) if isinstance(body, str) else body
            # Strip foundry envelope if present (e.g., from SQSApprover notification)
            if "foundry_event" in parsed:
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
