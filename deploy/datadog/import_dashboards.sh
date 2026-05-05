#!/usr/bin/env bash
# import_dashboards.sh — import (or update) all Arc Datadog dashboards.
#
# Reads ${DATADOG_API_KEY} + ${DATADOG_APP_KEY} from the environment.
# Optionally set ${DD_SITE} (default: datadoghq.com — change to datadoghq.eu
# or ddog-gov.com if appropriate).
#
# Behaviour:
#   - For each *.json in deploy/datadog/dashboards/, search for an existing
#     dashboard with the same title.
#   - If found, PUT (update). If not, POST (create).
#   - Dashboards are idempotent — running this script multiple times keeps
#     the dashboard set in sync with what's checked in to git.

set -euo pipefail

: "${DATADOG_API_KEY:?Set DATADOG_API_KEY before running}"
: "${DATADOG_APP_KEY:?Set DATADOG_APP_KEY before running}"
DD_SITE="${DD_SITE:-datadoghq.com}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DASHBOARDS_DIR="$SCRIPT_DIR/dashboards"

if [[ ! -d "$DASHBOARDS_DIR" ]]; then
    echo "Dashboards directory not found: $DASHBOARDS_DIR" >&2
    exit 1
fi

if ! command -v jq >/dev/null 2>&1; then
    echo "jq is required (brew install jq / apt install jq)" >&2
    exit 1
fi

# Fetch the existing dashboards once — we'll match by title.
existing_json="$(
    curl -fsS "https://api.$DD_SITE/api/v1/dashboard" \
        -H "DD-API-KEY: $DATADOG_API_KEY" \
        -H "DD-APPLICATION-KEY: $DATADOG_APP_KEY"
)"

shopt -s nullglob
for f in "$DASHBOARDS_DIR"/*.json; do
    title="$(jq -r '.title' "$f")"
    if [[ -z "$title" || "$title" == "null" ]]; then
        echo "Skipping $f — no .title field" >&2
        continue
    fi

    existing_id="$(echo "$existing_json" \
        | jq -r --arg t "$title" '.dashboards[] | select(.title == $t) | .id' \
        | head -n1)"

    if [[ -n "$existing_id" && "$existing_id" != "null" ]]; then
        echo "→ Updating $title ($existing_id)"
        curl -fsS -X PUT \
            "https://api.$DD_SITE/api/v1/dashboard/$existing_id" \
            -H "Content-Type: application/json" \
            -H "DD-API-KEY: $DATADOG_API_KEY" \
            -H "DD-APPLICATION-KEY: $DATADOG_APP_KEY" \
            --data @"$f" >/dev/null
        echo "   updated"
    else
        echo "→ Creating $title"
        curl -fsS -X POST \
            "https://api.$DD_SITE/api/v1/dashboard" \
            -H "Content-Type: application/json" \
            -H "DD-API-KEY: $DATADOG_API_KEY" \
            -H "DD-APPLICATION-KEY: $DATADOG_APP_KEY" \
            --data @"$f" \
            | jq -r '"   created — id: " + .id'
    fi
done

echo
echo "Done. View at: https://app.$DD_SITE/dashboard/lists"
