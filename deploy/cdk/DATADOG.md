# Datadog forwarder — wiring CloudWatch → Datadog

Arc emits operational metrics in two formats from `arc.core.telemetry`:

- **CloudWatch EMF** — structured JSON to stdout. CloudWatch Logs auto-extracts metrics. Always on in Lambda + ECS-with-awslogs.
- **DogStatsD UDP** — only works when a Datadog Agent / Lambda Extension is reachable.

For Lambda deployments without the Datadog Lambda Extension, the standard
production pattern is:

```
CloudWatch Logs ──► Datadog Forwarder Lambda ──► Datadog Logs + Metrics API
```

This is what `datadog_forwarder_stack.DatadogForwarderConstruct` deploys.
One construct, one `cdk deploy`, working Datadog dashboards.

---

## Prerequisites

1. **Datadog API key in Secrets Manager.** Create once per AWS account:

   ```bash
   aws secretsmanager create-secret \
     --name arc/datadog/api-key \
     --secret-string '<YOUR_DATADOG_API_KEY>' \
     --region us-east-1
   ```

2. **Datadog site you're forwarding to.** US (`datadoghq.com`),
   EU (`datadoghq.eu`), or GovCloud (`ddog-gov.com`).

---

## Deploying

The forwarder is opt-in. Set `datadog_api_key_secret` in the CDK context:

```bash
cdk deploy \
  --context agent_id=email-triage \
  --context ecr_image_uri=<ECR_URI>/email-triage:latest \
  --context environment=production \
  --context datadog_api_key_secret=arc/datadog/api-key \
  --context datadog_site=datadoghq.com
```

The construct:

1. Creates a Lambda role with read access to your Datadog API key secret
2. Pulls the Datadog forwarder Lambda zip from the public Datadog
   artefact bucket (`datadog-cloudformation-template`), pinned to a known-good
   version (override via `forwarder_version=` in code)
3. Subscribes the agent's CloudWatch log group to the forwarder via a
   `SubscriptionFilter`
4. Tags everything with `arc:component=datadog-forwarder` and
   `arc:environment=<env>` for clean cost attribution

---

## What you get in Datadog after deploy

Within ~2 minutes of an agent invocation:

| Datadog surface | Source |
|---|---|
| **Logs Explorer** | Every line your agent writes to stdout (already structured JSON) |
| **Metrics → arc.\*** | EMF metrics: `arc.effect.outcome`, `arc.effect.latency_ms`, `arc.llm.tokens_in`, `arc.outcome.event`, `arc.redaction.match` |
| **Tags on every signal** | `env:production`, `source:arc`, plus the per-metric tags (agent_id, effect, decision, …) |

Build dashboards on top of those metrics — the metric vocabulary is
documented in [`docs/concepts/telemetry.md`](../../docs/concepts/telemetry.md).
A starter set of dashboard JSONs ships in [`deploy/datadog/dashboards/`](../datadog/dashboards/).

---

## When you don't need this

If you're running on **ECS / Fargate / EKS** with the Datadog Agent
sidecar, or on **Lambda with the Datadog Lambda Extension layer**, you
don't need this forwarder. DogStatsD UDP from `DatadogTelemetry`
already reaches the Agent directly. This construct is specifically for
**Lambda-without-extension** deployments — the most common case for
teams that already have Datadog but don't want to add the extension to
every function.

You can run both at once safely; the metrics are deduplicated by
Datadog if you tag them consistently (the `env:` and `service:` tags do
the work).

---

## Cost notes

- The forwarder Lambda runs once per CloudWatch log batch (every few
  seconds when active). At default memory (1024 MB) and a 200 ms
  median execution, **~$0.50/month per agent** for typical email-triage
  volumes.
- **Don't** forward CloudWatch DEBUG logs unless you've reviewed the
  cost. The default `FilterPattern.all_events()` is permissive; tighten
  via `filter_pattern=` if your agents log heavily.
- Datadog log indexing is the bigger expense. Consider
  Datadog's **log archives + filters** to drop noisy lines before
  indexing.

---

## Troubleshooting

**Forwarder deploys but no logs in Datadog.** Check the forwarder's own
CloudWatch logs (`/aws/lambda/Arc-<agent>-DatadogForwarder`). Most
common causes:

1. API key secret has wrong format (must be the raw key, not JSON)
2. `DD_SITE` doesn't match your Datadog account region
3. Subscription filter wasn't created — confirm in the AWS console

**Metrics appear, logs don't (or vice versa).** The forwarder handles
both, but if the EMF parsing fails the metrics path silently drops
while logs continue. Check the forwarder log for `EMF parse error`.

**High forwarder error rate.** The forwarder Lambda has its own
CloudWatch alarm — wire to the `forwarder` CDK output ARN if you want
alerting on the forwarder itself.
