/**
 * Typed API client for the arc.platform.api FastAPI backend.
 *
 * Both ops/ and dev/ React apps construct an ApiClient pointed at the
 * backend's base URL (http://localhost:8000 in dev). Vite's dev server
 * proxies /api/* to the backend automatically — see vite.config.ts.
 */

import type {
  AgentSummary,
  AgentsByStage,
  AuditEvent,
  AuditSummary,
  PendingApproval,
  PromotionSummary,
} from "./types";

export interface ApiClientOptions {
  /** Base URL for the API. Defaults to "" so requests use the same origin
   *  (which Vite then proxies to the backend in dev). */
  baseUrl?: string;
}

export class ApiError extends Error {
  constructor(public readonly status: number, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

export class ApiClient {
  private baseUrl: string;

  constructor(opts: ApiClientOptions = {}) {
    this.baseUrl = opts.baseUrl ?? "";
  }

  private async get<T>(path: string): Promise<T> {
    const res = await fetch(`${this.baseUrl}${path}`, {
      headers: { Accept: "application/json" },
    });
    if (!res.ok) {
      let detail = res.statusText;
      try {
        const body = await res.json();
        if (body?.detail) detail = body.detail;
      } catch {
        /* keep statusText */
      }
      throw new ApiError(res.status, detail);
    }
    return (await res.json()) as T;
  }

  // ── Health ────────────────────────────────────────────────────────────

  health(): Promise<{ status: string }> {
    return this.get("/api/health");
  }

  // ── Agents ────────────────────────────────────────────────────────────

  listAgents(): Promise<AgentSummary[]> {
    return this.get("/api/agents");
  }

  agentsByStage(): Promise<AgentsByStage> {
    return this.get("/api/agents/by-stage");
  }

  getAgent(agentId: string): Promise<AgentSummary> {
    return this.get(`/api/agents/${encodeURIComponent(agentId)}`);
  }

  // ── Audit ─────────────────────────────────────────────────────────────

  listAudit(opts: { limit?: number; agentId?: string } = {}): Promise<AuditEvent[]> {
    const params = new URLSearchParams();
    if (opts.limit !== undefined) params.set("limit", String(opts.limit));
    if (opts.agentId) params.set("agent_id", opts.agentId);
    const qs = params.toString();
    return this.get(`/api/audit${qs ? "?" + qs : ""}`);
  }

  auditSummary(): Promise<AuditSummary> {
    return this.get("/api/audit/summary");
  }

  // ── Promotions + approvals ────────────────────────────────────────────

  promotionSummary(): Promise<PromotionSummary> {
    return this.get("/api/promotions/summary");
  }

  pendingApprovals(): Promise<PendingApproval[]> {
    return this.get("/api/approvals");
  }
}

/** Default singleton — most pages just import this. */
export const api = new ApiClient();
