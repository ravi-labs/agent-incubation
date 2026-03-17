"""
foundry.deploy.lambda_handler
──────────────────────────────
Wraps any BaseAgent as an AWS Lambda function.

The handler reads the agent manifest at startup (cold start), initialises
the ControlTower and Gateway, and invokes execute() on every event.

Usage — in your agent repo, create handler.py:

    from foundry.deploy.lambda_handler import make_handler
    from my_agents.fiduciary_watchdog import FiduciaryWatchdogAgent

    handler = make_handler(FiduciaryWatchdogAgent)

Then set Lambda handler to: handler.handler

Environment variables:
    FOUNDRY_MANIFEST_PATH   Path to manifest.yaml (default: manifest.yaml)
    FOUNDRY_POLICY_DIR      Path to policy directory (default: policies/)
    FOUNDRY_ENV             "sandbox" | "production" (default: sandbox)
    FOUNDRY_LOG_LEVEL       Python log level (default: INFO)
"""

from __future__ import annotations

import json
import logging
import os
import traceback
from typing import Any

logger = logging.getLogger(__name__)


def make_handler(agent_class: type, **agent_kwargs: Any):
    """
    Create a Lambda handler for the given agent class.

    Args:
        agent_class:  A BaseAgent subclass to run.
        **agent_kwargs: Extra kwargs passed to agent_class.__init__().

    Returns:
        A module-level object with a .handler(event, context) method.
    """
    return _FoundryLambdaHandler(agent_class, **agent_kwargs)


class _FoundryLambdaHandler:
    """Lambda handler that manages agent lifecycle across warm invocations."""

    def __init__(self, agent_class: type, **agent_kwargs: Any):
        self._agent_class = agent_class
        self._agent_kwargs = agent_kwargs
        self._agent = None  # lazy-initialised on first cold start

        log_level = os.environ.get("FOUNDRY_LOG_LEVEL", "INFO")
        logging.basicConfig(level=getattr(logging, log_level))

    def _init_agent(self):
        """Cold-start initialisation — runs once per Lambda container lifetime."""
        from foundry.gateway.base import GatewayConnector
        from foundry.scaffold.manifest import AgentManifest
        from foundry.tollgate.tower import ControlTower

        manifest_path = os.environ.get("FOUNDRY_MANIFEST_PATH", "manifest.yaml")
        policy_dir    = os.environ.get("FOUNDRY_POLICY_DIR",    "policies/")
        environment   = os.environ.get("FOUNDRY_ENV",           "sandbox")

        logger.info(
            "foundry_cold_start agent=%s manifest=%s env=%s",
            self._agent_class.__name__, manifest_path, environment,
        )

        manifest = AgentManifest.from_yaml(manifest_path)

        # Override environment from env var so the same package works in both
        # sandbox (FOUNDRY_ENV=sandbox) and production (FOUNDRY_ENV=production)
        manifest.environment = environment

        tower   = ControlTower.from_policy_dir(policy_dir)
        gateway = GatewayConnector()

        self._agent = self._agent_class(
            manifest=manifest,
            tower=tower,
            gateway=gateway,
            **self._agent_kwargs,
        )

        logger.info("foundry_agent_ready agent=%s", manifest.agent_id)

    def handler(self, event: dict, context: Any) -> dict:
        """
        Lambda entry point.

        Event formats supported:
          1. Direct invocation:     {"action": "...", "params": {...}}
          2. EventBridge:           {"detail-type": "...", "detail": {...}}
          3. SQS trigger:           {"Records": [{"body": "{...}"}]}
          4. Bedrock Agent Core:    {"actionGroup": "...", "function": "...", "parameters": [...]}
        """
        import asyncio

        if self._agent is None:
            self._init_agent()

        # Normalise event into kwargs for execute()
        kwargs = self._normalise_event(event)

        logger.info(
            "foundry_invoke agent=%s request_id=%s",
            self._agent.manifest.agent_id,
            getattr(context, "aws_request_id", "local"),
        )

        try:
            loop = asyncio.new_event_loop()
            result = loop.run_until_complete(self._agent.execute(**kwargs))
            loop.close()

            return {
                "statusCode": 200,
                "agent":      self._agent.manifest.agent_id,
                "result":     result,
            }

        except PermissionError as exc:
            logger.error("foundry_permission_denied: %s", exc)
            return {
                "statusCode": 403,
                "error":      "permission_denied",
                "message":    str(exc),
            }

        except Exception as exc:  # noqa: BLE001
            logger.error("foundry_error: %s\n%s", exc, traceback.format_exc())
            return {
                "statusCode": 500,
                "error":      type(exc).__name__,
                "message":    str(exc),
            }

    @staticmethod
    def _normalise_event(event: dict) -> dict:
        """Extract kwargs from various Lambda event shapes."""

        # Bedrock Agent Core action group invocation
        if "actionGroup" in event:
            params = {p["name"]: p["value"] for p in event.get("parameters", [])}
            return {"action": event.get("function", "execute"), **params}

        # SQS batch — process first record (single-item batches recommended)
        if "Records" in event:
            record = event["Records"][0]
            body = record.get("body", "{}")
            return json.loads(body) if isinstance(body, str) else body

        # EventBridge
        if "detail" in event:
            detail = event["detail"]
            return detail if isinstance(detail, dict) else {"detail": detail}

        # Direct invocation or unknown — pass through as-is
        return event
