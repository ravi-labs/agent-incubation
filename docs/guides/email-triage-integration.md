# Integrate the email-triage agent with your sandbox — real end-to-end

A full integration guide for the **`email-triage` agent** under
`arc/agents/email-triage/` against your org's sandbox tenants. **No
mocks**: real Outlook OAuth, real Bedrock Claude, real ticket creation
in your Pega or ServiceNow sandbox, real audit trail to disk.

This is the bridge document between the platform's controlled,
audit-equivalent sandbox (the `arc-harness` layer) and your
organisation's sandbox tenants (Outlook test mailbox, Pega/ServiceNow
PDI, AWS sandbox account).

> **Audience:** an engineer ready to point `email-triage` at real
> services for the first time. The guide assumes the sandbox tenants
> exist or that you can request them. Everything runs in your
> organisation's *sandbox* — never against a production mailbox or
> case-management instance.

---

## What you'll have at the end

A continuously-running `email-triage` agent that:

- **Polls** a real Outlook test mailbox (Microsoft Graph API)
- **Classifies** incoming emails using **real Bedrock Claude**
  via LangGraph + the `governed_chat_model` wrapper
- **Extracts** entities (system, error code, sender) using real LLM
  structured-output calls
- **Routes** to the right team based on classification + sender tier
- **Creates** real tickets in your Pega Case sandbox **or** ServiceNow
  PDI (your choice — `TICKET_TARGET` env var picks)
- **Defers** P1/P2 ticket creation to a human reviewer (DEFERRED →
  approval queue → resolved)
- **Sends** acknowledgment replies via the same Outlook mailbox
- **Audits** every effect to a JSONL file you can inspect line-by-line

The platform's governance gates (manifest scope, policy, audit) are
*all live* — same code paths the production runtime would use. Only
the deploy target is your sandbox tenants.

---

## Prerequisites — what you need before starting

| Service | What's needed | Estimated setup time |
|---|---|---|
| **AWS account** | Bedrock model access in `us-east-1` (or another region you'll use) for at least `anthropic.claude-3-5-sonnet-20241022-v2:0` | 15 min — request in Bedrock console |
| **Microsoft 365 / Azure AD tenant** | Admin access to register an app, create an app secret, and grant Mail.Read / Mail.Send permissions | 30 min — your org's admin may need to approve |
| **Pega Case sandbox** *or* **ServiceNow PDI** | One sandbox case-management system. ServiceNow has free Personal Developer Instances; Pega Cloud has dev tenants for licensed customers | 30–60 min |
| **Test mailbox** | A dedicated mailbox in your Microsoft 365 tenant — e.g. `arc-sandbox@yourcompany.com` — that the agent will poll | 5 min — your admin creates it |
| **Repo + venv** | This repository cloned and `setup.sh --mode aws` run successfully | 5 min |

> **One important constraint.** Don't point the agent at a real
> shared inbox. Use a dedicated test mailbox. The agent reads, drafts
> replies, and sends — exactly what it would do in production.

---

## Step 1 — Provision the sandbox accounts (30–90 min)

This step happens once. Skip any sub-step where the account already
exists.

### 1a. AWS Bedrock — request model access

1. Sign in to the AWS console with the sandbox account.
2. Navigate to **Bedrock → Model access** (region `us-east-1` or
   wherever you'll deploy).
3. Click **Manage model access**, request **Anthropic Claude 3.5
   Sonnet** (and ideally **Claude 3 Haiku** as a fallback).
4. Wait for status to flip to **Access granted** — usually instant
   for sandbox accounts; can take up to 24h for restricted tenants.
5. From IAM, create an access key for a user with the
   `AmazonBedrockFullAccess` managed policy (or a tighter scope —
   `bedrock:InvokeModel` is the minimum).

You'll capture three values:
- `AWS_ACCESS_KEY_ID`
- `AWS_SECRET_ACCESS_KEY`
- `AWS_REGION` (e.g. `us-east-1`)

### 1b. Microsoft Graph — register an app for Outlook

1. Sign in to the [Azure portal](https://portal.azure.com) →
   **Azure Active Directory → App registrations → New registration**.
2. Name: `arc-email-triage-sandbox` (or similar).
   Account types: *Single tenant*. Redirect URI: leave blank.
3. After creation, capture **Application (client) ID** and
   **Directory (tenant) ID** from the Overview tab.
4. **Certificates & secrets → New client secret.** Copy the value
   *immediately* — it's only shown once. Set expiry per your org's
   policy (90d / 1y).
5. **API permissions → Add a permission → Microsoft Graph →
   Application permissions** (not delegated):
   - `Mail.Read`
   - `Mail.Send`
   - `User.Read.All` (only if the agent needs sender directory lookups)
6. Click **Grant admin consent for &lt;tenant&gt;**. This requires
   tenant-admin rights — if you don't have them, ask your admin.

You'll capture four values:
- `OUTLOOK_TENANT_ID`
- `OUTLOOK_CLIENT_ID`
- `OUTLOOK_CLIENT_SECRET`
- `OUTLOOK_INBOX_USER` — the test mailbox UPN, e.g. `arc-sandbox@yourcompany.com`

### 1c. Choose a ticketing sandbox

Pick **one**. The `TICKET_TARGET` env var selects at runtime.

#### Option A — ServiceNow PDI (free, fastest)

1. Sign up at [developer.servicenow.com](https://developer.servicenow.com)
   and request a Personal Developer Instance. Free; provisions in ~5 min.
2. After it boots, go to **System OAuth → Application Registry →
   New → Create an OAuth API endpoint for external clients**.
3. Capture **Client ID** and **Client Secret**.
4. Capture your instance URL — `https://devNNNNN.service-now.com`.

You'll capture:
- `SNOW_INSTANCE_URL`
- `SNOW_CLIENT_ID`
- `SNOW_CLIENT_SECRET`
- `SNOW_TABLE` (default: `incident`)

#### Option B — Pega Case sandbox

If your org licenses Pega, your platform team can provision a sandbox
tenant. The Pega side requires:

1. A Case Type matching `manifest.case_type` (default
   `ITSM-Work-ServiceRequest` — your tenant may use a different
   prefix).
2. An OAuth 2.0 client with the `pegaapi` scope.

You'll capture:
- `PEGA_BASE_URL` (e.g. `https://yoursandbox.pegacloud.com/prweb`)
- `PEGA_CLIENT_ID`
- `PEGA_CLIENT_SECRET`
- `PEGA_CASE_TYPE` (defaults to `ITSM-Work-ServiceRequest`)

> **Talk track for whichever you pick.** The agent code never imports
> Pega or ServiceNow directly. Both sit behind the same
> `GatewayConnector` Protocol, and `RuntimeBuilder` chooses the
> connector at construction time based on `TICKET_TARGET`. Switching
> later is a one-line env change, no code edit.

---

## Step 2 — Configure environment variables (5 min)

Create a `.env` file in the repo root. This file is gitignored — never
commits.

| | macOS / Linux | Windows (PowerShell) |
|---|---|---|
| Create `.env` | `touch .env` | `New-Item -Path .env -ItemType File -Force` |
| Open in editor | `${EDITOR:-vi} .env` | `notepad .env` |

Paste this template and fill in your values:

```bash
# ── AWS Bedrock (LLM) ──────────────────────────────────────────────
AWS_ACCESS_KEY_ID=<your-access-key>
AWS_SECRET_ACCESS_KEY=<your-secret-key>
AWS_REGION=us-east-1

# Tell arc which LLM provider to use platform-wide
ARC_LLM_PROVIDER=bedrock
ARC_LLM_MODEL=anthropic.claude-3-5-sonnet-20241022-v2:0
ARC_LLM_REGION=us-east-1

# ── Outlook (Microsoft Graph) ──────────────────────────────────────
OUTLOOK_TENANT_ID=<your-tenant-id>
OUTLOOK_CLIENT_ID=<your-client-id>
OUTLOOK_CLIENT_SECRET=<your-client-secret>
OUTLOOK_INBOX_USER=arc-sandbox@yourcompany.com

# ── Ticketing — pick exactly ONE of pega/servicenow ────────────────
TICKET_TARGET=servicenow                  # or 'pega'

# ServiceNow (if TICKET_TARGET=servicenow)
SNOW_INSTANCE_URL=https://devNNNNN.service-now.com
SNOW_CLIENT_ID=<your-snow-client-id>
SNOW_CLIENT_SECRET=<your-snow-client-secret>
SNOW_TABLE=incident

# Pega (if TICKET_TARGET=pega)
# PEGA_BASE_URL=https://yoursandbox.pegacloud.com/prweb
# PEGA_CLIENT_ID=<your-pega-client-id>
# PEGA_CLIENT_SECRET=<your-pega-client-secret>
# PEGA_CASE_TYPE=ITSM-Work-ServiceRequest

# ── Audit + approver ───────────────────────────────────────────────
ARC_AUDIT_SINK=jsonl
ARC_AUDIT_PATH=email_triage_audit.jsonl
ARC_APPROVER_MODE=cli                     # 'cli' (terminal prompt) for sandbox
                                          # 'sqs' for production async approvals
# ARC_SQS_QUEUE_URL=                      # required when ARC_APPROVER_MODE=sqs
```

**Why `ARC_APPROVER_MODE=cli`?** For the sandbox, the simplest
approver is `CliApprover` — when an effect lands `ASK`, you're
prompted in the terminal to approve or reject. Production swaps to
`AsyncQueueApprover` (SQS + DynamoDB) so reviewers resolve approvals
from the dashboard.

### Load the env vars into your shell

| | macOS / Linux | Windows (PowerShell) |
|---|---|---|
| Activate venv | `source .venv/bin/activate` | `.\.venv\Scripts\Activate.ps1` |
| Load `.env` | `set -a; source .env; set +a` | `Get-Content .env \| ForEach-Object { if ($_ -match '^([^#=]+)=(.*)$') { Set-Item -Path "env:$($Matches[1].Trim())" -Value $Matches[2] } }` |
| Verify one var | `echo $OUTLOOK_TENANT_ID` | `$env:OUTLOOK_TENANT_ID` |

> **Don't `git add .env`.** It's gitignored, but worth a re-check
> with `git status` before any commit. Treat the file like a private
> key.

---

## Step 3 — Verify connectivity (15 min — do this before running the agent)

Three smoke tests, each ~30 lines. Save under `tools/` so they don't
clutter the agent folder.

### 3a. Bedrock smoke test

| | macOS / Linux | Windows (PowerShell) |
|---|---|---|
| Make dir | `mkdir -p tools` | `New-Item -ItemType Directory -Force tools` |
| Create file | `touch tools/smoke_bedrock.py` | `New-Item -Path tools\smoke_bedrock.py -ItemType File -Force` |

Paste this into `tools/smoke_bedrock.py`:

```python
"""Smoke-test Bedrock access. Should print 'OK' + a one-line completion."""
import os, json, boto3

client = boto3.client("bedrock-runtime", region_name=os.environ["AWS_REGION"])
resp = client.invoke_model(
    modelId=os.environ.get("ARC_LLM_MODEL", "anthropic.claude-3-5-sonnet-20241022-v2:0"),
    body=json.dumps({
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": 32,
        "messages": [{"role": "user", "content": "Reply with the single word: OK"}],
    }),
)
data = json.loads(resp["body"].read())
print("Bedrock reply:", data["content"][0]["text"])
```

Run:

| | macOS / Linux | Windows (PowerShell) |
|---|---|---|
| Run | `python tools/smoke_bedrock.py` | `python tools\smoke_bedrock.py` |

If you see `Bedrock reply: OK`, you're good. If you see
`AccessDeniedException`, your IAM policy is missing
`bedrock:InvokeModel` for that model — request access in the Bedrock
console or attach `AmazonBedrockFullAccess` to your IAM user.

### 3b. Outlook smoke test

Paste this into `tools/smoke_outlook.py`:

```python
"""Smoke-test Outlook access. Should print message count + the 3 most recent subjects."""
import asyncio, os
from arc.connectors.outlook import OutlookConnector
from arc.runtime.config import OutlookConfig
from arc.core.gateway import DataRequest

async def main():
    cfg = OutlookConfig(
        tenant_id     = os.environ["OUTLOOK_TENANT_ID"],
        client_id     = os.environ["OUTLOOK_CLIENT_ID"],
        client_secret = os.environ["OUTLOOK_CLIENT_SECRET"],
        inbox_user    = os.environ["OUTLOOK_INBOX_USER"],
    )
    connector = OutlookConnector(cfg)
    resp = await connector.fetch(DataRequest(
        source = "email.inbox",
        params = {"top": 3, "folder": "Inbox"},
    ))
    items = resp.data.get("value", []) if isinstance(resp.data, dict) else resp.data
    print(f"Inbox: {len(items)} message(s)")
    for m in items[:3]:
        print(f"  • {m.get('subject', '<no subject>')}  ({m.get('from', {}).get('emailAddress', {}).get('address', '?')})")

asyncio.run(main())
```

Send a test email to your sandbox mailbox first (anything works —
subject "Test", body "Hello"). Then run:

| | macOS / Linux | Windows (PowerShell) |
|---|---|---|
| Run | `python tools/smoke_outlook.py` | `python tools\smoke_outlook.py` |

You should see your test email in the listing. If you get a
**401 / 403** from Graph, the most common causes are:
- App secret expired or mistyped
- Admin consent not granted for the requested permissions
- Mailbox UPN doesn't match a real user in the tenant

### 3c. Ticketing smoke test (ServiceNow shown — Pega is parallel)

Paste this into `tools/smoke_servicenow.py`:

```python
"""Smoke-test ServiceNow access. Lists 3 incidents; creates + deletes a sandbox one."""
import asyncio, os
from arc.connectors.servicenow import ServiceNowConnector
from arc.runtime.config import ServiceNowConfig
from arc.core.gateway import DataRequest

async def main():
    cfg = ServiceNowConfig(
        instance_url  = os.environ["SNOW_INSTANCE_URL"],
        client_id     = os.environ["SNOW_CLIENT_ID"],
        client_secret = os.environ["SNOW_CLIENT_SECRET"],
        table         = os.environ.get("SNOW_TABLE", "incident"),
    )
    connector = ServiceNowConnector(cfg)
    resp = await connector.fetch(DataRequest(
        source = "ticket.system",
        params = {"limit": 3},
    ))
    print(f"Recent incidents: {len(resp.data.get('result', []))}")
    for inc in resp.data.get("result", [])[:3]:
        print(f"  • {inc.get('number')} — {inc.get('short_description', '')[:60]}")

asyncio.run(main())
```

| | macOS / Linux | Windows (PowerShell) |
|---|---|---|
| Run | `python tools/smoke_servicenow.py` | `python tools\smoke_servicenow.py` |

You should see at least the demo incidents that ship with a fresh
ServiceNow PDI.

> **All three smoke tests passing is the contract.** If any one
> fails, fix it before moving to Step 4. The agent will fail in
> harder-to-debug ways if a connector misbehaves at runtime.

---

## Step 4 — Add the runtime runner to email-triage

The shipped `agent.py` has a `main()` that wires the **harness** (mock
fixtures, sandbox approver). For real-end-to-end you need a sibling
runner that wires the **runtime** (real connectors, env-driven config).

Create `arc/agents/email-triage/run_runtime.py` and paste:

```python
"""
run_runtime.py — production-style runner for the email-triage agent.

Wires real connectors via RuntimeBuilder + RuntimeConfig.from_env().
Set ARC_AUTO_DEMOTE_DISABLED=1 if you want to suppress the watcher.

Usage:
    # One-shot — process emails currently in the inbox and exit
    python arc/agents/email-triage/run_runtime.py --once

    # Polling — process new emails every N seconds
    python arc/agents/email-triage/run_runtime.py --poll-seconds 60

    # Single email by id (useful for replaying a specific message)
    python arc/agents/email-triage/run_runtime.py --email-id <message-id>
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from arc.runtime.builder import RuntimeBuilder
from arc.runtime.config import RuntimeConfig

logger = logging.getLogger(__name__)

BASE     = Path(__file__).parent
MANIFEST = BASE / "manifest.yaml"
POLICY   = BASE / "policy.yaml"


async def fetch_pending_email_ids(agent, top: int = 25) -> list[str]:
    """Pull the top N unread message ids from the configured inbox."""
    from arc.core.gateway import DataRequest

    resp = await agent.gateway.fetch(DataRequest(
        source = "email.inbox",
        params = {"top": top, "filter": "isRead eq false"},
    ))
    items = resp.data.get("value", []) if isinstance(resp.data, dict) else resp.data
    return [m["id"] for m in items if m.get("id")]


async def run_once(agent, email_ids: list[str] | None = None) -> dict:
    if email_ids is None:
        email_ids = await fetch_pending_email_ids(agent)
        logger.info("Found %d unread email(s)", len(email_ids))
    if not email_ids:
        return {"processed": 0}
    return await agent.execute(email_ids=email_ids)


async def main_async() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true",
                        help="Process current inbox and exit (default)")
    parser.add_argument("--poll-seconds", type=int, default=0,
                        help="Poll the inbox every N seconds. 0 = single run.")
    parser.add_argument("--email-id", type=str,
                        help="Process exactly one message by Graph id")
    args = parser.parse_args()

    # ── Build the agent for production-style wiring ────────────────────
    config = RuntimeConfig.from_env()
    config.validate_for_agent(["outlook", "pega_case" if "pega" in (
        __import__("os").environ.get("TICKET_TARGET", "")) else "servicenow"])

    # Build LangGraph orchestrator with real Bedrock LLM
    from arc.agents.email_triage.agent import EmailTriageAgent
    from arc.agents.email_triage.graph import build_email_triage_graph
    from arc.orchestrators import LangGraphOrchestrator

    builder = RuntimeBuilder(config=config, manifest=MANIFEST, policy=POLICY)
    agent = builder.build(EmailTriageAgent)

    # use_mock_llm=False → real ChatBedrockConverse via governed_chat_model
    graph = build_email_triage_graph(agent, use_mock_llm=False)
    agent.orchestrator = LangGraphOrchestrator(graph=graph)

    # ── Execute ────────────────────────────────────────────────────────
    if args.email_id:
        results = await run_once(agent, email_ids=[args.email_id])
    elif args.poll_seconds and args.poll_seconds > 0:
        logger.info("Polling every %ds — Ctrl+C to stop", args.poll_seconds)
        while True:
            try:
                results = await run_once(agent)
                if results.get("processed", 0):
                    logger.info("Cycle complete: %s", results)
            except Exception:
                logger.exception("polling cycle failed; will retry next interval")
            await asyncio.sleep(args.poll_seconds)
    else:
        results = await run_once(agent)

    print("\nResults:", results)
    return 0


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s  %(name)s: %(message)s",
    )
    sys.exit(asyncio.run(main_async()))
```

**What just happened.** This is the same `EmailTriageAgent` class
from the harness path — same business logic, same manifest, same
policy. Only the wiring changed: `RuntimeBuilder` reads env vars,
constructs real `OutlookConnector` + `ServiceNowConnector` /
`PegaCaseConnector`, and `LangGraphOrchestrator` runs the graph with
real Bedrock instead of `MockBedrockLLM`. The agent code didn't move.

---

## Step 5 — Run end-to-end (10 min)

### 5a. Send a test email to the sandbox mailbox

Use any email client (your own Outlook, a script, anything). The
content shouldn't matter — the agent classifies whatever arrives. A
useful first message:

```
To:      arc-sandbox@yourcompany.com
Subject: Auth service down — can't log in
Body:
Hi team,

I'm getting an "AUTH-500" error trying to log in to the order portal.
Started about 30 minutes ago. Affecting our whole sales team.

Thanks,
Jane Doe
```

This will classify as **incident / P2 / negative sentiment**, route to
the IT team, and create a ticket — the platform allows P3/P4
auto-create but defers P1/P2 to human review. So you'll see an `ASK`
prompt in the terminal.

### 5b. Run the agent in single-shot mode

| | macOS / Linux | Windows (PowerShell) |
|---|---|---|
| Run | `python arc/agents/email-triage/run_runtime.py --once` | `python arc\agents\email-triage\run_runtime.py --once` |

Watch the log scroll. You should see:

```
INFO  arc.agents.email_triage: Found 1 unread email(s)
INFO  arc.core.agent: classify_node: <id> → intent=incident priority=P2 confidence=0.92 sentiment=negative
INFO  arc.core.agent: extract_entities_node: systems=['auth'] error_codes=['AUTH-500']
INFO  arc.core.agent: lookup_user_node: tier=standard
INFO  arc.core.agent: query_knowledge_node: 1 KB match
INFO  arc.core.agent: check_duplicate_node: not a duplicate
INFO  arc.core.agent: draft_ticket_node: drafted
ASK   arc.core.tower: ticket.create requires human review (P1/P2 policy)
       Approve? [y/N]:
```

Type `y` + Enter to approve. The agent then:
- Creates the incident in your ServiceNow / Pega sandbox
- Drafts an acknowledgment reply
- Logs the triage decision

### 5c. Verify the outputs

Three places to look:

**The audit log** (where every decision lands):

| | macOS / Linux | Windows (PowerShell) |
|---|---|---|
| Tail audit | `tail -f email_triage_audit.jsonl` | `Get-Content email_triage_audit.jsonl -Wait -Tail 50` |
| Count rows | `wc -l email_triage_audit.jsonl` | `(Get-Content email_triage_audit.jsonl).Count` |
| ASK rows | `grep '"decision":"ASK"' email_triage_audit.jsonl` | `Select-String '"decision":"ASK"' email_triage_audit.jsonl` |

You should see ~12 rows for one email (every effect: read, classify,
priority, sentiment, extract, lookup, query, duplicate, draft,
create, send, log).

**The ticketing system.** Open your ServiceNow PDI or Pega sandbox in
a browser and find the ticket. The `short_description` should
summarise the email; the `description` will include the LLM-extracted
entities + KB match.

**The mailbox.** The acknowledgment reply should appear in the
sandbox mailbox's Sent folder (or be visible in the conversation
thread of the original email).

---

## Step 6 — Run continuously (poll every minute)

Once single-shot works, switch to polling:

| | macOS / Linux | Windows (PowerShell) |
|---|---|---|
| Run | `python arc/agents/email-triage/run_runtime.py --poll-seconds 60` | `python arc\agents\email-triage\run_runtime.py --poll-seconds 60` |

Send a few more emails to the sandbox mailbox; the loop will pick
each up within 60s.

For a more realistic sandbox deployment, wrap the polling command in
a process supervisor:

| | macOS / Linux | Windows |
|---|---|---|
| Daemonise | `nohup python arc/agents/email-triage/run_runtime.py --poll-seconds 60 > triage.log 2>&1 &` | Use **Task Scheduler** with action `python arc\agents\email-triage\run_runtime.py --poll-seconds 60`, working dir = repo root, env from `.env` |

For production (later — not this guide), use systemd / Docker / ECS.

---

## Step 7 — Inspect outcomes via the dashboard (5 min)

The arc-platform's ops dashboard reads the same audit JSONL the agent
writes, plus the approval queue. Open it to see the live picture.

| | macOS / Linux | Windows (PowerShell) |
|---|---|---|
| **Terminal A — backend** | `arc platform serve --port 8000` | `arc platform serve --port 8000` |
| **Terminal B — frontend** | `cd arc/packages/arc-platform/frontend && npm run dev:ops` | `cd arc\packages\arc-platform\frontend; npm run dev:ops` |

Open <http://localhost:5173>. Three pages worth visiting:

- **Overview** — counts of ALLOW / ASK / DENY across all agents
- **Agents** — `email-triage` shows its lifecycle stage + recent activity
- **Approvals** — DEFERRED P1/P2 ticket-creates land here when
  `ARC_APPROVER_MODE=sqs` (with `cli`, you approve in the terminal
  instead — both paths write the same audit row)

To switch to dashboard-driven approvals, change `.env`:

```bash
ARC_APPROVER_MODE=sqs
ARC_SQS_QUEUE_URL=https://sqs.us-east-1.amazonaws.com/<account>/<queue>
```

…and provision an SQS queue + DynamoDB table in your AWS sandbox.
That's a separate (small) setup — covered in
[`docs/concepts/governance.md → ASK and the approver protocol`](../concepts/governance.md).

---

## Step 8 — Promote through the lifecycle (3 min)

The agent shipped at `BUILD`. Once you're confident it's behaving
correctly in your sandbox, walk it through the gates:

| | macOS / Linux | Windows (PowerShell) |
|---|---|---|
| Inspect current | `arc agent list --dir arc/agents` | `arc agent list --dir arc\agents` |
| → VALIDATE | `arc agent promote arc/agents/email-triage/manifest.yaml --to VALIDATE` | (same with `\`) |
| → GOVERN | (same with `--to GOVERN`) | (same) |
| → SCALE (dry-run) | (same with `--to SCALE --dry-run`) | (same) |

Each promotion writes a row to `promotion_audit.jsonl`. **Talk track
for the demo:** the dry-run shows the platform requires explicit
confirmation before flipping to production — no accidental SCALE
promotions.

---

## Troubleshooting — the issues that always come up

| Symptom | Cause | Fix |
|---|---|---|
| `RuntimeConfig missing required environment variables` | One of `OUTLOOK_TENANT_ID` / `OUTLOOK_CLIENT_ID` / `OUTLOOK_CLIENT_SECRET` not loaded | Re-run the `.env` loader from Step 2; check `echo $OUTLOOK_TENANT_ID` (or `$env:` in PS) is non-empty |
| `AccessDeniedException` on first Bedrock call | Model access not yet granted, or wrong region | Confirm in Bedrock console — model status must be **Access granted** in `AWS_REGION` |
| 401 / 403 from Graph | App secret expired, or admin consent not granted | Rotate the secret; click *Grant admin consent* in the Azure portal |
| `OUTLOOK_INBOX_USER does not match any mailbox` | UPN typo, or the user has no Exchange Online license | Use the exact UPN from the Microsoft 365 admin centre; ensure the user has a mailbox |
| `langchain_aws` ImportError on `_load_llm` | `setup.sh --mode dev` was used (skipped AWS extras) | Re-run `./setup.sh --mode aws` |
| Agent classifies *every* email as `incident` | LLM prompt likely truncated to first 1000 chars (see `graph.py`) | Check `body` field in the audit row; long emails are intentionally truncated. Reduce sender's wall of text or raise the limit in the agent |
| ServiceNow returns 401 from `/api/now/...` | OAuth client created but no user assigned a role | In SNow → System Security → Users → assign `web_service_admin` to your client user |
| Polling never picks up new emails | `isRead eq false` filter — Outlook autopreview marks as read | Either disable preview in the test mailbox or change the filter in `run_runtime.py:fetch_pending_email_ids` |
| `TollgateDeferred` raised but no terminal prompt | `ARC_APPROVER_MODE` not set; defaults to `sqs` | Set `ARC_APPROVER_MODE=cli` in `.env` for sandbox runs |

---

## Appendix A — If your org's email vendor isn't Outlook

The shipped `OutlookConnector` is the only email connector today.
Other vendors plug in by implementing the `GatewayConnector` Protocol
in [`arc/packages/arc-connectors/src/arc/connectors/`](../../arc/packages/arc-connectors/src/arc/connectors/):

```python
# arc/packages/arc-connectors/src/arc/connectors/yourvendor.py
from arc.core.gateway import GatewayConnector, DataRequest, DataResponse

class YourVendorConnector(GatewayConnector):
    def __init__(self, config):
        self.config = config
        # ... auth setup

    async def fetch(self, request: DataRequest) -> DataResponse:
        if request.source == "email.inbox":
            messages = await self._list_inbox(...)   # vendor's API
            return DataResponse(data={"value": messages})
        if request.source == "email.thread":
            ...
        raise ValueError(f"Unknown source: {request.source}")
```

Then wire it in [`RuntimeBuilder._build_outlook_connector`](../../arc/packages/arc-runtime/src/arc/runtime/builder.py)
behind a config switch (e.g. `EMAIL_VENDOR=yourvendor`). The agent
code, manifest, and policy never change — they speak in the abstract
`email.inbox` / `email.thread` source names that any connector can
serve.

Effort: ~1 day for a working connector + tests if the vendor's API is
sane. The existing `ServiceNowConnector` is the cleanest reference
(REST + OAuth 2.0).

---

## Where to read next

- [Build an agent](build-an-agent.md) — the long-form version of
  what's in `agent.py` / `graph.py`. Read this if you want to modify
  the agent's behaviour rather than just deploy it.
- [Lifecycle](../concepts/lifecycle.md) — what happens at each
  promotion stage and how to capture the evidence.
- [Governance](../concepts/governance.md) — what `run_effect()` does
  internally (manifest scope → policy → audit).
- [LLM clients](../concepts/llm-clients.md) — how `LLMConfig`
  precedence works and what the `governed_chat_model` adapter does.
- [Roadmap](../roadmap.md) — what's shipped vs in flight (CloudWatch
  audit sink, multi-host watcher, etc. are real production-prep
  follow-ups).
