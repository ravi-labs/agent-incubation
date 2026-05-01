"""arc.core.config_loader — opt-in ``.env`` loader for local + sandbox dev.

Single helper, ``load_env_file()``, that reads a ``.env`` file into the
process environment when one is present. **Shell-set vars always win** —
the loader uses ``override=False`` so a value already set in the
parent shell is never replaced.

Why opt-in. Production deploys (AWS Lambda / ECS / Bedrock Agents) get
their environment through the platform's task-definition or
function-config layer, not from a filesystem ``.env`` file. The loader
is deliberately a no-op when no ``.env`` is present, so production
runtimes can call it without conditional logic — the file just
won't exist there.

Where it's called from:

  - ``arc.cli.main:cli`` (the ``arc`` CLI entry-point group)
  - ``arc.harness.HarnessBuilder.__init__``
  - ``arc.runtime.RuntimeBuilder.__init__``

Each call is idempotent — ``override=False`` means subsequent loads do
nothing if the keys are already in ``os.environ``. Safe to call many
times per process.

The loader walks **upward** from the working directory looking for a
``.env`` file. This means subdirectory invocations (``cd
arc/agents/email-triage && python agent.py``) still pick up the
repo-root ``.env`` without any wiring on the agent side.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


# Default depth for the upward search. The arc monorepo is rarely more
# than 3 levels deep at the call site (e.g. ``arc/agents/<name>/``);
# 5 gives headroom for unusual layouts without scanning past sensible
# project boundaries.
_DEFAULT_SEARCH_DEPTH = 5


def load_env_file(
    path: str | Path | None = None,
    *,
    search_parents: int = _DEFAULT_SEARCH_DEPTH,
) -> Path | None:
    """Load a ``.env`` file into ``os.environ`` if one is found.

    The function is **idempotent** and **non-overriding**:
      - Re-running it is safe; existing ``os.environ`` keys keep their values.
      - Shell-set vars always beat ``.env`` values.
      - When ``.env`` doesn't exist, the function is a silent no-op.

    Args:
        path: Explicit path to a ``.env`` file. If given, only that file
            is considered (no parent walk). When the file doesn't exist,
            the call is a no-op and returns ``None``.
        search_parents: How many parent directories to walk when looking
            for a ``.env`` file. Ignored when ``path`` is given. Default
            covers the arc monorepo layout.

    Returns:
        The :class:`Path` that was loaded, or ``None`` if no file was
        found (or the optional ``python-dotenv`` dependency is missing).
    """
    try:
        from dotenv import load_dotenv  # type: ignore[import-untyped]
    except ImportError:
        # python-dotenv is a runtime dep of arc-core; this branch only
        # triggers in unusual installs (e.g. partial extracts). Failing
        # silently keeps test fixtures and stripped-down environments
        # working — the trade-off is no ``.env`` loading there.
        logger.debug("python-dotenv not installed; .env file loading disabled")
        return None

    if path is not None:
        explicit = Path(path)
        if explicit.is_file():
            load_dotenv(dotenv_path=explicit, override=False)
            logger.debug("loaded env file: %s", explicit)
            return explicit
        return None

    # No explicit path — walk upward from CWD looking for `.env`.
    cur = Path.cwd().resolve()
    for _ in range(search_parents + 1):
        candidate = cur / ".env"
        if candidate.is_file():
            load_dotenv(dotenv_path=candidate, override=False)
            logger.debug("loaded env file (parent walk): %s", candidate)
            return candidate
        if cur.parent == cur:
            break  # filesystem root
        cur = cur.parent

    return None


__all__ = ["load_env_file"]
