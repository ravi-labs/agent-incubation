/**
 * TypeScript mirrors of the Python dataclasses returned by arc.platform.api.
 *
 * Keep these aligned with arc/packages/arc-platform/src/arc/platform/common/data.py.
 * If we add OpenAPI generation later, this file becomes generated.
 */

export type LifecycleStage =
  | "DISCOVER"
  | "SHAPE"
  | "BUILD"
  | "VALIDATE"
  | "GOVERN"
  | "SCALE";

export type AgentStatus = "active" | "suspended" | "deprecated";

export type Decision = "ALLOW" | "ASK" | "DENY" | "UNKNOWN";

export type PromotionOutcome = "approved" | "rejected" | "deferred";

export interface AgentSummary {
  agent_id: string;
  version: string;
  owner: string;
  description: string;
  lifecycle_stage: LifecycleStage;
  status: AgentStatus;
  environment: string;
  allowed_effects: string[];
  tags: string[];
}

export interface AuditEvent {
  timestamp: string;
  agent_id: string;
  effect: string;
  decision: Decision;
  reason: string;
  tool: string;
}

export interface AuditSummary {
  total: number;
  ALLOW: number;
  ASK: number;
  DENY: number;
}

export type ApprovalStatus = "pending" | "approved" | "rejected";

export interface PendingApproval {
  approval_id: string;
  status: ApprovalStatus;
  agent_id: string;
  current_stage: LifecycleStage;
  target_stage: LifecycleStage;
  requester: string;
  justification: string;
  requested_at: string;
  decided_at: string;
  reason: string;
  resolved_at: string;
  resolved_by: string;
  resolution_reason: string;
}

export interface ResolveApprovalRequest {
  approve: boolean;
  reviewer: string;
  reason?: string;
}

export interface ResolveApprovalResponse {
  decision: Record<string, unknown>;     // server returns full PromotionDecision dict
  applied_to_manifest: boolean;
  agent_id: string;
  new_stage: string | null;
}

export interface PromotionSummary {
  total: number;
  APPROVED: number;
  REJECTED: number;
  DEFERRED: number;
}

export interface AgentsByStage {
  [stage: string]: AgentSummary[];
}
