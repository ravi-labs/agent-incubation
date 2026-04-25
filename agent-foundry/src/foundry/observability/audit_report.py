"""Migrated to arc.core.observability.audit_report. Thin re-export shim.

The CLI entry point `foundry-audit` registered in pyproject.toml still
points at this module's `main` function, which we re-export below.
"""

from arc.core.observability.audit_report import generate_report

# The audit_report module exposes a `main()` for the foundry-audit CLI script.
try:
    from arc.core.observability.audit_report import main
except ImportError:  # pragma: no cover — main was always optional
    main = None  # type: ignore[assignment]

__all__ = ["generate_report", "main"]
