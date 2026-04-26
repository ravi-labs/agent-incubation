# arc-platform

Web platform for the arc agent incubation system. **Two React frontends + one FastAPI backend**, with a shared data layer over the rest of arc.

```
arc-platform/
├── src/arc/platform/
│   ├── common/data.py       Typed loaders over manifests, audit, promotions
│   └── api/                 FastAPI JSON backend (/api/*)
├── frontend/                npm workspace
│   ├── shared/              @arc/shared — TS types + API client + hooks
│   ├── ops/                 @arc/ops — business-user dashboard
│   └── dev/                 @arc/dev — engineer dashboard
└── tests/                   Backend tests (data + API)
```

## Why two frontends, one backend

The two audiences want different things:

- **Engineers** (`frontend/dev`) — recent decisions, audit trail, agent-by-agent debugging, eventually harness runs and eval results.
- **Business users** (`frontend/ops`) — the approval queue, agent inventory, lifecycle stages, compliance audit trail.

They consume the same domain data, so the backend is one FastAPI app exposing one JSON API. The frontends differ in *which* endpoints they hit and how they present the result.

## Run it

### 1. Backend

```bash
# From the repo root, after pip-installing arc-core + arc-platform editable
arc platform serve

# Or directly with uvicorn:
uvicorn arc.platform.api:create_app --factory --reload --port 8000
```

The backend reads three locations by default (override via flags or env):

| Source | Default path | Override |
|---|---|---|
| Per-agent manifests | `./arc/agents/` | `--manifest-root <DIR>` |
| Runtime audit log (JSONL) | `./audit.jsonl` | `--audit-log <FILE>` |
| Promotion-decision log (JSONL) | `./promotions.jsonl` | `--promotion-log <FILE>` |

Missing files are not errors — the dashboards stay viewable in a cold environment.

### 2. Frontends (dev)

From `arc/packages/arc-platform/frontend/`:

```bash
npm install
npm run dev:ops    # business-user dashboard → http://localhost:5173
npm run dev:dev    # engineer dashboard      → http://localhost:5174
```

Vite's dev server proxies `/api/*` to the backend on port 8000, so both frontends hit the live API with no extra config.

### 3. Frontends (production build)

```bash
cd arc/packages/arc-platform/frontend
npm run build
# → ops/dist/   and   dev/dist/   are deployable static bundles
```

## API endpoints

| Method | Path | Returns |
|---|---|---|
| GET | `/api/health` | `{ status: "ok" }` |
| GET | `/api/agents` | `AgentSummary[]` |
| GET | `/api/agents/{agent_id}` | `AgentSummary` |
| GET | `/api/agents/by-stage` | `{ [stage]: AgentSummary[] }` |
| GET | `/api/audit?limit=&agent_id=` | `AuditEvent[]` |
| GET | `/api/audit/summary` | counts (total / ALLOW / ASK / DENY) |
| GET | `/api/promotions` | `PromotionDecision[]` |
| GET | `/api/promotions/summary` | counts (total / APPROVED / REJECTED / DEFERRED) |
| GET | `/api/approvals` | `PendingApproval[]` (DEFERRED items only) |

TypeScript types for every response live in `frontend/shared/src/types.ts`.

## Pages today (Phase 1)

**ops dashboard** (`frontend/ops`):
- `/` — overview: agent count, pending-approval count, allow/ask/deny totals.
- `/agents` — agent inventory: one row per agent with stage, owner, status, environment.
- `/approvals` — pending DEFERRED promotions (read-only).

**dev dashboard** (`frontend/dev`):
- `/` — engineering overview: audit totals + 5 most recent decisions.
- `/agents` — engineering agent view: stage, environment, effect count, tags.

## Pages ahead (Phase 2+)

- `/approvals` becomes interactive — approve / reject form wired through Tollgate's `AsyncQueueApprover`. Closes the SCALE promotion path. **This is the next commit.**
- `/audit` — full audit search with filters (agent, effect, decision, time window).
- `/lifecycle` — promotion-decision history per agent + transition diagrams.
- Engineer-side: harness runs, eval results, manifest validation status, deploy status.

## Testing

Backend:

```bash
pytest arc/packages/arc-platform/tests/ -v
```

Frontend (typecheck only — no test runner yet):

```bash
cd arc/packages/arc-platform/frontend
npm run typecheck
```

## Architectural notes

- The data layer (`PlatformData`) is the seam between the dashboards and arc's domain code. Both frontends only ever see what `PlatformData` exposes — never raw manifests, never raw audit JSONL. Adds caching / a database backend in one place when needed.
- `build_app(data)` is a factory so tests inject their own `PlatformData`. Production callers use `create_app()`.
- CORS is permissive on `localhost:5173/5174` for local dev. Tighten for production deploys via an env var (TODO when there's a real prod target).
- No auth yet. The dashboards assume a trusted network. SSO + RBAC ships before any production deploy.
