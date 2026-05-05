# Arc Datadog dashboards

Three dashboards, ready to import. They're built on the metric vocabulary
defined in [`docs/concepts/telemetry.md`](../../docs/concepts/telemetry.md):

| Dashboard | Audience | Cadence |
|---|---|---|
| `live-operations.json` | On-call engineers | Real-time during incidents |
| `quality-governance.json` | Ops leads + compliance | Daily / weekly |
| `pii-heatmap.json` | Compliance leads | Daily |

## Import

### Option 1 — `dog` CLI

```bash
pip install datadog
export DATADOG_API_KEY=<your-api-key>
export DATADOG_APP_KEY=<your-app-key>

dog dashboard post < deploy/datadog/dashboards/live-operations.json
dog dashboard post < deploy/datadog/dashboards/quality-governance.json
dog dashboard post < deploy/datadog/dashboards/pii-heatmap.json
```

### Option 2 — Datadog API directly

```bash
for f in deploy/datadog/dashboards/*.json; do
  curl -X POST "https://api.${DD_SITE:-datadoghq.com}/api/v1/dashboard" \
    -H "Content-Type: application/json" \
    -H "DD-API-KEY: ${DATADOG_API_KEY}" \
    -H "DD-APPLICATION-KEY: ${DATADOG_APP_KEY}" \
    -d @"$f"
done
```

### Option 3 — bundled import script

```bash
deploy/datadog/import_dashboards.sh
```

(Reads `DATADOG_API_KEY` + `DATADOG_APP_KEY` from environment.)

## Template variables

Every dashboard ships with two template variables (Datadog dropdown selectors at the top):

- `agent_id` — filter to one agent or `*` for all
- `env` — `production` / `sandbox`

These match the tag conventions emitted by `arc.core.telemetry`. No
edits needed if you tag your CDK stack consistently
(`arc:environment=production`).

## Customising

The JSON is plain Datadog dashboard schema. Two common edits:

1. **Add a chart** — duplicate any widget block, change the query, add
   to the `widgets` array.
2. **Adjust thresholds** — the `conditional_formats` arrays on
   `query_value` widgets control the green/yellow/red colour bands.
   Default thresholds are conservative; tighten for your traffic.

After edits, re-import. Datadog will create a new dashboard each time
unless you preserve the `id` field — the import script handles this
automatically by checking title before creating.

## When you don't have these metrics yet

The dashboards are populated by `arc.core.telemetry` emitting via:

- **CloudWatch EMF + Datadog forwarder** (Lambda — see
  [`deploy/cdk/DATADOG.md`](../cdk/DATADOG.md))
- **DogStatsD UDP** (ECS / Fargate / EKS with the Datadog Agent
  sidecar)

If your agents emit nothing yet, set:

```bash
export ARC_TELEMETRY=cloudwatch+datadog
```

and ensure either the Datadog forwarder is deployed (Lambda) or the
agent sidecar is reachable on `127.0.0.1:8125` (ECS/EKS).

Verify metrics are flowing: in Datadog → Metrics Explorer, search for
`arc.` — you should see `arc.effect.outcome`, `arc.effect.latency_ms`,
`arc.llm.tokens_in`, `arc.outcome.event`, `arc.redaction.match`,
`arc.slo.breach`. If only some appear, the missing ones are simply
metrics for code paths that haven't fired yet.
