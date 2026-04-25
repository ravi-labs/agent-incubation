"""
foundry.deploy.secrets
───────────────────────
AWS Secrets Manager and SSM Parameter Store integration.

Loads agent credentials, API keys, and sensitive configuration at startup —
never from environment variables or bundled in the container image.

Install:
    pip install "arc-runtime[aws]"

Usage in a Lambda handler or agent init:

    from arc.runtime.deploy.secrets import FoundrySecrets

    secrets = FoundrySecrets(region="us-east-1")

    # Load a database URL from Secrets Manager
    db_url = secrets.get_secret("foundry/fiduciary-watchdog/db-url")

    # Load a config value from SSM Parameter Store
    slack_webhook = secrets.get_parameter("/foundry/notifications/slack-webhook")

    # Load a full JSON secret as a dict
    api_creds = secrets.get_secret_json("foundry/bloomberg/credentials")
    # → {"api_key": "...", "secret": "..."}

Naming conventions (enforced by CDK stack):
    Secrets Manager:  foundry/{agent_id}/{secret_name}
    SSM Parameter:    /foundry/{agent_id}/{param_name}
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from typing import Any

logger = logging.getLogger(__name__)


class FoundrySecrets:
    """
    Cached loader for AWS Secrets Manager and SSM Parameter Store.

    Values are cached in-process for the Lambda container lifetime.
    For secrets that rotate, call invalidate() to clear the cache.
    """

    def __init__(
        self,
        region: str | None = None,
        cache_ttl_seconds: int = 300,   # 5-minute in-process cache
    ):
        self.region = region
        self._secrets_cache: dict[str, str] = {}
        self._params_cache:  dict[str, str] = {}
        self._sm_client: Any = None
        self._ssm_client: Any = None

    def _get_sm(self) -> Any:
        if self._sm_client is None:
            try:
                import boto3
            except ImportError as exc:
                raise ImportError(
                    "boto3 is not installed. Run: pip install 'arc-runtime[aws]'"
                ) from exc
            kwargs: dict[str, Any] = {}
            if self.region:
                kwargs["region_name"] = self.region
            self._sm_client = boto3.client("secretsmanager", **kwargs)
        return self._sm_client

    def _get_ssm(self) -> Any:
        if self._ssm_client is None:
            try:
                import boto3
            except ImportError as exc:
                raise ImportError(
                    "boto3 is not installed. Run: pip install 'arc-runtime[aws]'"
                ) from exc
            kwargs: dict[str, Any] = {}
            if self.region:
                kwargs["region_name"] = self.region
            self._ssm_client = boto3.client("ssm", **kwargs)
        return self._ssm_client

    # ── Secrets Manager ────────────────────────────────────────────────────────

    def get_secret(self, secret_name: str) -> str:
        """
        Load a secret string from Secrets Manager (cached).

        Args:
            secret_name: Full secret name or ARN.

        Returns:
            Secret string value.
        """
        if secret_name in self._secrets_cache:
            return self._secrets_cache[secret_name]

        try:
            response = self._get_sm().get_secret_value(SecretId=secret_name)
            value = response.get("SecretString") or ""
            self._secrets_cache[secret_name] = value
            logger.debug("Loaded secret: %s", secret_name)
            return value

        except Exception as exc:
            logger.error("Failed to load secret %r: %s", secret_name, exc)
            raise

    def get_secret_json(self, secret_name: str) -> dict[str, Any]:
        """
        Load a JSON secret from Secrets Manager and parse it.

        Returns:
            Parsed dict (e.g., {"api_key": "...", "host": "..."}).
        """
        raw = self.get_secret(secret_name)
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"Secret {secret_name!r} is not valid JSON"
            ) from exc

    # ── SSM Parameter Store ────────────────────────────────────────────────────

    def get_parameter(self, param_name: str, decrypt: bool = True) -> str:
        """
        Load a parameter from SSM Parameter Store (cached).

        Args:
            param_name: Full parameter name (e.g., "/foundry/agent/key").
            decrypt:    Decrypt SecureString parameters (default True).

        Returns:
            Parameter value string.
        """
        if param_name in self._params_cache:
            return self._params_cache[param_name]

        try:
            response = self._get_ssm().get_parameter(
                Name=param_name, WithDecryption=decrypt
            )
            value = response["Parameter"]["Value"]
            self._params_cache[param_name] = value
            logger.debug("Loaded parameter: %s", param_name)
            return value

        except Exception as exc:
            logger.error("Failed to load parameter %r: %s", param_name, exc)
            raise

    def get_parameters_by_path(
        self, path: str, decrypt: bool = True
    ) -> dict[str, str]:
        """
        Load all parameters under an SSM path prefix.

        Args:
            path:    Parameter path prefix (e.g., "/foundry/fiduciary-watchdog/").
            decrypt: Decrypt SecureString values.

        Returns:
            Dict of {name: value} for all parameters under the path.
        """
        result: dict[str, str] = {}
        paginator = self._get_ssm().get_paginator("get_parameters_by_path")

        for page in paginator.paginate(Path=path, WithDecryption=decrypt):
            for param in page.get("Parameters", []):
                name  = param["Name"]
                value = param["Value"]
                result[name] = value
                self._params_cache[name] = value

        return result

    # ── Cache management ───────────────────────────────────────────────────────

    def invalidate(self, name: str | None = None) -> None:
        """
        Invalidate the cache for a specific secret/parameter, or clear all.

        Call this before reading a secret that may have been rotated.

        Args:
            name: Specific secret/parameter name to invalidate, or None for all.
        """
        if name is None:
            self._secrets_cache.clear()
            self._params_cache.clear()
            logger.debug("Secrets cache fully invalidated")
        else:
            self._secrets_cache.pop(name, None)
            self._params_cache.pop(name, None)
            logger.debug("Invalidated cache for: %s", name)


# ── Module-level helper for simple use cases ──────────────────────────────────

_default_secrets: FoundrySecrets | None = None


def get_secrets(region: str | None = None) -> FoundrySecrets:
    """Return a module-level FoundrySecrets instance (singleton per process)."""
    global _default_secrets
    if _default_secrets is None:
        _default_secrets = FoundrySecrets(region=region)
    return _default_secrets
