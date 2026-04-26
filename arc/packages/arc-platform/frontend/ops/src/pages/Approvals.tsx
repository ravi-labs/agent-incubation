import { useState } from "react";
import {
  ApiError,
  api,
  useFetch,
  type PendingApproval,
  type ResolveApprovalResponse,
} from "@arc/shared";

export default function Approvals() {
  const { data, error, loading, refetch } = useFetch(() => api.pendingApprovals());

  if (loading) return <><h2>Pending approvals</h2><div className="state-msg">Loading…</div></>;
  if (error)   return <><h2>Pending approvals</h2><div className="state-msg error">{error.message}</div></>;

  const approvals = data ?? [];

  return (
    <>
      <h2>Pending approvals</h2>
      <p style={{ color: "var(--text-muted)", marginTop: "-12px", marginBottom: "24px" }}>
        Promotion decisions awaiting human review. Approving an item also writes
        the new lifecycle stage to the agent's manifest.
      </p>

      {approvals.length === 0 ? (
        <div className="state-msg">
          No pending approvals. Promotion decisions in the <code>DEFERRED</code> state
          (e.g. SCALE promotions awaiting human sign-off) appear here.
        </div>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          {approvals.map((a) => (
            <ApprovalCard key={a.approval_id} approval={a} onResolved={refetch} />
          ))}
        </div>
      )}
    </>
  );
}

interface ApprovalCardProps {
  approval: PendingApproval;
  onResolved: () => void;
}

function ApprovalCard({ approval, onResolved }: ApprovalCardProps) {
  const [reviewer, setReviewer]   = useState("");
  const [reason, setReason]       = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [feedback, setFeedback]   = useState<{ kind: "ok" | "err"; msg: string } | null>(null);

  async function decide(approve: boolean) {
    if (!reviewer.trim()) {
      setFeedback({ kind: "err", msg: "Reviewer is required." });
      return;
    }
    setSubmitting(true);
    setFeedback(null);
    try {
      const result: ResolveApprovalResponse = await api.resolveApproval(
        approval.approval_id,
        { approve, reviewer: reviewer.trim(), reason: reason.trim() },
      );
      const verb = approve ? "approved" : "rejected";
      const tail = result.applied_to_manifest
        ? ` — manifest now at ${result.new_stage}`
        : "";
      setFeedback({ kind: "ok", msg: `${approval.agent_id} ${verb}${tail}.` });
      // Refetch the list after a brief pause so the user sees the success state
      setTimeout(onResolved, 600);
    } catch (e) {
      const msg = e instanceof ApiError
        ? `${e.status}: ${e.message}`
        : (e as Error).message;
      setFeedback({ kind: "err", msg });
      setSubmitting(false);
    }
  }

  return (
    <div className="card" style={{ padding: 20 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 12 }}>
        <div>
          <div style={{ fontWeight: 700, fontSize: "1.1rem" }}>{approval.agent_id}</div>
          <div style={{ marginTop: 4 }}>
            <span className="badge stage">{approval.current_stage}</span>
            {" → "}
            <span className="badge stage">{approval.target_stage}</span>
          </div>
        </div>
        <div style={{ fontSize: "0.85rem", color: "var(--text-muted)", textAlign: "right" }}>
          <div>requested by {approval.requester}</div>
          <div>{new Date(approval.requested_at).toLocaleString()}</div>
        </div>
      </div>

      <div style={{ marginBottom: 16, fontSize: "0.92rem" }}>
        <div style={{ color: "var(--text-muted)", fontSize: "0.78rem", textTransform: "uppercase", letterSpacing: "0.06em", marginBottom: 4 }}>
          Justification
        </div>
        <div>{approval.justification || "—"}</div>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 2fr", gap: 12, marginBottom: 12 }}>
        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <span style={{ fontSize: "0.78rem", color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.06em" }}>
            Reviewer
          </span>
          <input
            type="text"
            value={reviewer}
            onChange={(e) => setReviewer(e.target.value)}
            placeholder="you@compliance"
            disabled={submitting}
            style={inputStyle}
          />
        </label>
        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <span style={{ fontSize: "0.78rem", color: "var(--text-muted)", textTransform: "uppercase", letterSpacing: "0.06em" }}>
            Reason (optional)
          </span>
          <input
            type="text"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="ROI verified, ready for production"
            disabled={submitting}
            style={inputStyle}
          />
        </label>
      </div>

      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <button
          onClick={() => decide(true)}
          disabled={submitting}
          style={{ ...btnStyle, background: "var(--ok)", color: "white" }}
        >
          {submitting ? "Working…" : "Approve"}
        </button>
        <button
          onClick={() => decide(false)}
          disabled={submitting}
          style={{ ...btnStyle, background: "var(--bad)", color: "white" }}
        >
          Reject
        </button>
        {feedback && (
          <span style={{
            marginLeft: 12,
            fontSize: "0.9rem",
            color: feedback.kind === "ok" ? "var(--ok)" : "var(--bad)",
          }}>
            {feedback.msg}
          </span>
        )}
      </div>
    </div>
  );
}

const inputStyle: React.CSSProperties = {
  padding: "8px 10px",
  border: "1px solid var(--border)",
  borderRadius: 6,
  fontSize: "0.92rem",
  fontFamily: "inherit",
};

const btnStyle: React.CSSProperties = {
  padding: "8px 18px",
  border: "none",
  borderRadius: 6,
  fontSize: "0.92rem",
  fontWeight: 600,
  cursor: "pointer",
};
