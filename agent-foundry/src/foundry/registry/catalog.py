# Migrated to arc-core. Kept as a shim so existing `from foundry.registry.catalog`
# imports keep working.
from arc.core.registry.catalog import (  # noqa: F401
    CatalogEntry,
    RegistryCatalog,
    build_catalog,
)
