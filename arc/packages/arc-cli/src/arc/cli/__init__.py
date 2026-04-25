"""arc.cli — command-line interface for the agent incubation platform.

Run as ``arc <subcommand>`` after ``pip install arc-cli``. The same
top-level command tree is also available as ``foundry`` for backward
compatibility with the legacy script.
"""

from .main import cli

__all__ = ["cli"]
