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
  Correction,
  PendingApproval,
  PromotionSummary,
  ResolveApprovalRequest,
  ResolveApprovalResponse,
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
    return this.request<T>("GET", path);
  }

  private async post<T>(path: string, body: unknown): Promise<T> {
    return this.request<T>("POST", path, body);
  }

  private async request<T>(method: string, path: string, body?: unknown): Promise<T> {
    const res = await fetch(`${this.baseUrl}${path}`, {
      method,
      headers: {
        Accept: "application/json",
        ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) {
      let detail = res.statusText;
      try {
        const errBody = await res.json();
        if (errBody?.detail) detail = errBody.detail;
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

  // ── Suspend / Resume (kill switch) ────────────────────────────────────

  suspendAgent(
    agentId: string,
    body: { reviewer: string; reason: string },
  ): Promise<{ agent_id: string; status: string; actor: string; at: string; reason: string }> {
    return this.post(`/api/agents/${encodeURIComponent(agentId)}/suspend`, body);
  }

  resumeAgent(
    agentId: string,
    body: { reviewer: string; reason?: string },
  ): Promise<{ agent_id: string; status: string; actor: string; at: string; reason: string }> {
    return this.post(`/api/agents/${encodeURIComponent(agentId)}/resume`, body);
  }

  // ── Live stats ────────────────────────────────────────────────────────

  agentStats(
    agentId: string,
    windowMinutes: number = 60 * 24,
  ): Promise<{
    agent_id: string;
    window_minutes: number;
    total: number;
    decisions: { ALLOW: number; ASK: number; DENY: number };
    decision_pct: { ALLOW: number; ASK: number; DENY: number };
    case_types: Record<string, number>;
    top_case_type: string;
    pending_approvals: number;
  }> {
    return this.get(
      `/api/agents/${encodeURIComponent(agentId)}/stats?window_minutes=${windowMinutes}`,
    );
  }

  // ── Corrections (feedback loop) ───────────────────────────────────────

  recordCorrection(
    agentId: string,
    body: {
      audit_row_id: string;
      reviewer: string;
      severity: "minor" | "moderate" | "critical";
      reason: string;
      original_decision: Record<string, unknown>;
      corrected_decision: Record<string, unknown>;
      schema_version?: string;
      metadata?: Record<string, unknown>;
    },
  ): Promise<Correction> {
    return this.post(`/api/agents/${encodeURIComponent(agentId)}/corrections`, body);
  }

  listCorrections(
    agentId: string,
    opts: { limit?: number; since?: string } = {},
  ): Promise<Correction[]> {
    const params = new URLSearchParams();
    if (opts.limit !== undefined) params.set("limit", String(opts.limit));
    if (opts.since) params.set("since", opts.since);
    const qs = params.toString();
    return this.get(
      `/api/agents/${encodeURIComponent(agentId)}/corrections${qs ? "?" + qs : ""}`,
    );
  }

  correctionsSummary(
    agentId: string,
    since?: string,
  ): Promise<{
    total: number;
    by_severity: Record<string, number>;
    by_reviewer: Record<string, number>;
    top_patterns: Array<{ pattern: string; count: number }>;
  }> {
    const qs = since ? `?since=${encodeURIComponent(since)}` : "";
    return this.get(
      `/api/agents/${encodeURIComponent(agentId)}/corrections/summary${qs}`,
    );
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

  allApprovals(): Promise<PendingApproval[]> {
    return this.get("/api/approvals/all");
  }

  resolveApproval(
    approvalId: string,
    body: ResolveApprovalRequest,
  ): Promise<ResolveApprovalResponse> {
    return this.post(
      `/api/approvals/${encodeURIComponent(approvalId)}/decide`,
      body,
    );
  }
}

/** Default singleton — most pages just import this. */
export const api = new ApiClient();
