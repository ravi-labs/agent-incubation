# Migrated to arc-core. Kept as a shim so existing `from foundry.registry`
# imports keep working.
from arc.core.registry import CatalogEntry, RegistryCatalog, build_catalog  # noqa: F401

__all__ = ["CatalogEntry", "RegistryCatalog", "build_catalog"]
