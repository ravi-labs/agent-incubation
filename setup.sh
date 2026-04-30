#!/usr/bin/env bash
# setup.sh — one-shot environment setup for arc + tollgate.
#
# Builds a Python venv, installs every workspace package in dependency
# order with the right optional extras, and (optionally) the React
# frontend deps. Two profiles:
#
#   --mode dev   (default)   Local development. Adds [dev] extras
#                            (pytest, ruff, mypy). No AWS deps.
#                            Use for tests, harness runs, demos.
#   --mode aws               Production-like. Adds [aws] extras on
#                            arc-connectors + arc-runtime (boto3,
#                            langchain-aws). No dev tools.
#
# Optional flags:
#   --with-frontend          Also `npm install` the arc-platform React
#                            frontends (ops + dev). Skips on its own.
#   --python <path>          Use a specific Python interpreter.
#   --venv <dir>             Override the venv directory (default: .venv).
#
# Idempotent: safe to re-run. Stops on first error.
#
# Usage:
#   ./setup.sh                            # dev profile, no frontend
#   ./setup.sh --mode aws                 # AWS profile
#   ./setup.sh --mode dev --with-frontend # dev + npm install
#
# After it finishes:
#   source .venv/bin/activate
#   arc --help

set -euo pipefail

# ── Locate the script + repo root ───────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── Defaults ────────────────────────────────────────────────────────────────
MODE="dev"
WITH_FRONTEND=0
PYTHON_BIN="${PYTHON:-python3}"
VENV_DIR=".venv"
MIN_PY_MAJOR=3
MIN_PY_MINOR=11

# ── Parse args ──────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --mode)
            MODE="${2:-}"
            shift 2
            ;;
        --with-frontend)
            WITH_FRONTEND=1
            shift
            ;;
        --python)
            PYTHON_BIN="${2:-}"
            shift 2
            ;;
        --venv)
            VENV_DIR="${2:-}"
            shift 2
            ;;
        -h|--help)
            sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "Unknown argument: $1" >&2
            echo "Run with --help for usage." >&2
            exit 1
            ;;
    esac
done

if [[ "$MODE" != "dev" && "$MODE" != "aws" ]]; then
    echo "✗ --mode must be 'dev' or 'aws' (got '$MODE')" >&2
    exit 1
fi

# ── Pretty print helpers ───────────────────────────────────────────────────
say()   { printf "\033[1;36m▶ %s\033[0m\n" "$*"; }
ok()    { printf "\033[1;32m✓ %s\033[0m\n" "$*"; }
warn()  { printf "\033[1;33m! %s\033[0m\n" "$*"; }
fail()  { printf "\033[1;31m✗ %s\033[0m\n" "$*" >&2; exit 1; }

# ── 1. Python version check ────────────────────────────────────────────────
say "Checking Python interpreter…"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    fail "$PYTHON_BIN not found. Install Python ${MIN_PY_MAJOR}.${MIN_PY_MINOR}+ or pass --python <path>."
fi
PY_VERSION_RAW=$("$PYTHON_BIN" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")')
PY_MAJOR=$("$PYTHON_BIN" -c 'import sys; print(sys.version_info.major)')
PY_MINOR=$("$PYTHON_BIN" -c 'import sys; print(sys.version_info.minor)')
if (( PY_MAJOR < MIN_PY_MAJOR )) || (( PY_MAJOR == MIN_PY_MAJOR && PY_MINOR < MIN_PY_MINOR )); then
    fail "Python ${MIN_PY_MAJOR}.${MIN_PY_MINOR}+ required (found $PY_VERSION_RAW)."
fi
ok "Python $PY_VERSION_RAW at $($PYTHON_BIN -c 'import sys; print(sys.executable)')"

# ── 2. Create / reuse venv ─────────────────────────────────────────────────
if [[ -d "$VENV_DIR" ]]; then
    say "Reusing existing venv at $VENV_DIR"
else
    say "Creating venv at $VENV_DIR"
    "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

# Use the venv's pip directly — no need to source activate in a script.
# Resolve absolute-vs-relative venv path: only prepend SCRIPT_DIR when
# VENV_DIR is relative.
if [[ "$VENV_DIR" = /* ]]; then
    VENV_ABS="$VENV_DIR"
else
    VENV_ABS="$SCRIPT_DIR/$VENV_DIR"
fi
VENV_PY="$VENV_ABS/bin/python"
if [[ ! -x "$VENV_PY" ]]; then
    fail "venv Python not at $VENV_PY — venv creation failed."
fi
VENV_PIP="$VENV_PY -m pip"

say "Upgrading pip"
$VENV_PIP install --quiet --upgrade pip

# ── 3. Package list with per-mode extras ───────────────────────────────────
# Order matters: tollgate first, then arc-core, then everything that
# depends on either. Each entry is "<path>|<dev_extras>|<aws_extras>".
# Empty string = no extras for that mode.
#
# Indexed array (not associative) so this works on bash 3.2 / macOS too.

PACKAGES=(
    "tollgate|                       |aws"
    "arc/packages/arc-core|          dev|aws"
    "arc/packages/arc-connectors|    |aws,litellm,outlook,pega,servicenow"
    "arc/packages/arc-harness|       dev|"
    "arc/packages/arc-eval|          dev|"
    "arc/packages/arc-orchestrators| langchain,langgraph|all"
    "arc/packages/arc-runtime|       dev|aws"
    "arc/packages/arc-cli|           dev|"
    "arc/packages/arc-platform|      dev|"
    "agent-team-template|            dev|"
)

# ── 4. Install each package editable, in order ─────────────────────────────
say "Installing ${#PACKAGES[@]} Python packages in '$MODE' mode (editable, in dependency order)…"

for entry in "${PACKAGES[@]}"; do
    # Split on `|`. Whitespace is trimmed so the table above stays readable.
    IFS='|' read -r pkg dev_extras aws_extras <<< "$entry"
    pkg="$(echo "$pkg" | xargs)"
    dev_extras="$(echo "$dev_extras" | xargs)"
    aws_extras="$(echo "$aws_extras" | xargs)"

    if [[ ! -d "$pkg" ]]; then
        warn "skipping $pkg — directory not present"
        continue
    fi
    if [[ "$MODE" == "dev" ]]; then
        extras="$dev_extras"
    else
        extras="$aws_extras"
    fi
    if [[ -n "$extras" ]]; then
        target="$pkg[$extras]"
    else
        target="$pkg"
    fi
    printf "    %-40s extras=%s\n" "$pkg" "${extras:-—}"
    # `--quiet` keeps the output readable; failures still bubble up.
    if ! $VENV_PIP install --quiet --editable "$target"; then
        fail "pip install failed for $target — see output above."
    fi
done
ok "All Python packages installed."

# ── 5. Optional: frontend ──────────────────────────────────────────────────
if (( WITH_FRONTEND )); then
    FRONTEND_DIR="arc/packages/arc-platform/frontend"
    if [[ ! -d "$FRONTEND_DIR" ]]; then
        warn "frontend dir $FRONTEND_DIR missing — skipping"
    elif ! command -v npm >/dev/null 2>&1; then
        warn "npm not on PATH — skipping frontend install (install Node.js to fix)"
    else
        say "Installing frontend npm workspaces (ops + dev + shared)…"
        ( cd "$FRONTEND_DIR" && npm install --silent )
        ok "Frontend deps installed."
    fi
fi

# ── 6. Bootstrap .env on first run ─────────────────────────────────────────
# Copy .env.example → .env so subsequent CLI / agent runs find a starter
# config. Never overwrite an existing .env. Production deploys never run
# this script, so there's no risk of shipping defaults.
if [[ -f ".env.example" && ! -f ".env" ]]; then
    cp .env.example .env
    ok "Created .env from .env.example — fill in real values before running real connectors."
elif [[ -f ".env" ]]; then
    say "Reusing existing .env (won't overwrite)."
fi

# ── 7. Smoke check ─────────────────────────────────────────────────────────
say "Smoke-checking the install…"
if ! "$VENV_PY" -c "import arc.core; import tollgate" 2>/dev/null; then
    fail "Sanity import failed — arc.core / tollgate not importable from the venv."
fi

ARC_BIN="$VENV_ABS/bin/arc"
if [[ -x "$ARC_BIN" ]]; then
    if "$ARC_BIN" --help >/dev/null 2>&1; then
        ok "'arc' CLI works."
    else
        warn "'arc' CLI is on PATH but '--help' failed."
    fi
else
    warn "'arc' CLI not found at $ARC_BIN — arc-cli may have failed to install."
fi

# ── 8. Done ────────────────────────────────────────────────────────────────
echo
ok "Setup complete (mode=$MODE)."
cat <<NEXT

Next steps:
  source $VENV_DIR/bin/activate
  arc --help
  pytest arc/packages

Try the demo:
  docs/guides/demo.md

NEXT
