"""Migrated to arc.runtime.deploy.secrets. Thin re-export shim."""

from arc.runtime.deploy.secrets import FoundrySecrets, get_secrets

__all__ = ["FoundrySecrets", "get_secrets"]
