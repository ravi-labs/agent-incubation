"""
Migrated to arc.cli (see docs/migration-plan.md, module 11).

Thin re-export shim. The `foundry` script registered in pyproject.toml
keeps working through this shim — both `foundry agent ...` and
`arc agent ...` invoke the same code.
"""

from arc.cli.main import cli

__all__ = ["cli"]
