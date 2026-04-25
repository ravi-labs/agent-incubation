"""Migrated to arc.cli.main. Thin re-export shim — the `foundry` script
registered in pyproject.toml continues to work because it points at
`foundry.cli.main:cli`, which now resolves through this shim to
`arc.cli.main:cli`.
"""

from arc.cli.main import cli

__all__ = ["cli"]
