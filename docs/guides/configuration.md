# Configuration

How arc gets its runtime configuration — env vars, the `.env` file, and
the AWS-deploy carve-out. The short version: **`.env` for local dev,
shell env wins, production never sees `.env`.**

> **Public API:** `from arc.core import load_env_file`
> **Loaded by:** the `arc` CLI, `HarnessBuilder`, `RuntimeBuilder`

---

## The precedence stack

Higher wins:

```
shell env   >   .env (loaded by load_env_file)   >   defaults in code
```

Three concrete consequences:

1. **A value already set in your shell beats `.env`.** This is
   intentional — production deploys set env vars on the task /
   function config, and that should always win over any local file
   that might have leaked into the build.
2. **`.env` only fills in *missing* values.** Loading it twice is a
   no-op. Editing `.env` and re-running picks up new values for keys
   that weren't already in the shell.
3. **No `.env` file → the loader silently does nothing.** Production
   targets (Lambda, ECS, Bedrock Agents) never have a `.env` file;
   the loader is a no-op there. **You don't need to change any code
   for production.**

---

## Where the loader runs

`arc.core.load_env_file()` is called from three places — all idempotent,
all safe to call together:

| Surface | When it loads | Code |
|---|---|---|
| `arc` CLI                       | Top of `cli()` group, before any subcommand runs | `arc.cli.main` |
| `HarnessBuilder.__init__`       | First line of construction                        | `arc.harness.builder` |
| `RuntimeBuilder.__init__`       | First line of construction                        | `arc.runtime.builder` |

If your code path goes through any of those (and it usually does),
`.env` is already loaded by the time your agent runs. You don't call
the loader yourself in normal flows.

---

## Local + sandbox: setting up `.env`

Run `setup.sh` (Mac/Linux) or `setup.bat` (Windows) once — it copies
`.env.example` to `.env` automatically. Then edit `.env` to fill in
real values for the section(s) you need.

The template at the repo root ([.env.example](../../.env.example)) is
grouped by service — AWS, LLM, Outlook, Pega, ServiceNow, audit,
approver, watcher. Comment out anything you don't use; the loader skips
absent keys cleanly.

### Manual route (without `setup.sh`)

```bash
cp .env.example .env
$EDITOR .env
```

That's it. Re-running the CLI picks up the new values.

---

## What goes in `.env` vs what goes in the manifest

| Belongs in `.env` | Belongs in `manifest.yaml` |
|---|---|
| Connector credentials (Outlook secret, Pega API key, …) | The agent's `allowed_effects` |
| AWS region, AWS profile / access keys | The agent's `slo:` block |
| Platform-default LLM provider + model | Per-agent LLM override (`llm:` block) |
| Audit sink type + path | Stage / environment / status |
| Approver mode + queue URLs | Reviewer expectations + success metrics |

**Rule of thumb:** if it would change between dev / staging / prod
*for the same agent*, it's an env var. If it's a property *of* the
agent that doesn't change between deploys, it's manifest.

---

## Production: AWS deploy, no `.env`

In production you don't ship `.env`. Each AWS deploy target injects
env vars through its own mechanism:

| Target | Env vars come from | AWS creds come from |
|---|---|---|
| **Lambda**        | `Environment.Variables` on the function (Terraform / CloudFormation / Console) | **IAM role** attached to the function — no `AWS_ACCESS_KEY_ID` ever |
| **ECS / Fargate** | Task definition `environment` (non-sensitive) + `secrets` (pulls from Secrets Manager / Parameter Store at task start) | **Task IAM role** |
| **EC2**           | User data, Systems Manager, or the systemd unit | **Instance profile** |
| **Bedrock Agents (managed)** | Configured on the agent definition itself | The Bedrock service handles it |

Important: `RuntimeConfig.from_env()` reads `os.environ` regardless of
how the values got there. The same Python code runs in dev and prod —
only the *delivery mechanism* for env vars differs. `load_env_file()`
is a no-op in production because `.env` doesn't exist there, and the
shell-wins precedence rule means even if a `.env` accidentally shipped,
the platform-set vars would still win.

### The split that matters in production

```
AWS-native creds   → IAM role            (only AWS_REGION still in env, that's fine)
3rd-party secrets  → AWS Secrets Manager  (Outlook secret, Pega secret, …)
Configuration      → plain env vars       (ARC_LLM_PROVIDER, BEDROCK_MODEL_ID, TICKET_TARGET, …)
```

### Don't do these

- **Don't bake `.env` into a Docker image.** It's a security
  anti-pattern and the loader will pick it up at runtime.
- **Don't put real secrets in `.env.example`.** That file is checked
  in — the only values it should contain are placeholders + comments.
- **Don't write to `os.environ` from inside the agent.** Read-only.
  Configuration changes go through `.env` or the deployment substrate.

---

## Reference: every env var the platform reads

The full list lives in [.env.example](../../.env.example) with comments
explaining each var. Required vs optional is tagged there too.

Quick categorisation:

| Group | Examples | Required when… |
|---|---|---|
| AWS               | `AWS_REGION`, `AWS_ACCESS_KEY_ID`     | Any boto3 client (Bedrock, DynamoDB, S3) |
| LLM platform      | `ARC_LLM_PROVIDER`, `ARC_LLM_MODEL`    | An agent uses LLMs (skip otherwise) |
| Outlook           | `OUTLOOK_*` (4 vars)                   | Agent declares `email.read` |
| Pega              | `PEGA_*` (3 required + 2 optional)     | `TICKET_TARGET=pega` |
| ServiceNow        | `SNOW_*` (3 required + 1 optional)     | `TICKET_TARGET=servicenow` |
| Audit             | `ARC_AUDIT_SINK`, `ARC_AUDIT_PATH`     | Always (jsonl is the default) |
| Approver          | `ARC_APPROVER_MODE`, `ARC_SQS_QUEUE_URL` | Always (cli is the default) |
| Routing           | `TICKET_TARGET`                        | When agent creates tickets |
| AgentCore         | `AGENTCORE_AGENT_ID`                   | Deploying through Bedrock AgentCore |
| Watcher           | `ARC_AUTO_DEMOTE_DISABLED`             | Optional kill switch |

---

## Where to read next

- [Build an agent](build-an-agent.md) — uses `.env` in step 1
- [LLM clients](../concepts/llm-clients.md) — `LLMConfig.from_env()` deep-dive
- [Architecture](../architecture.md) — where this layer sits relative to the rest
