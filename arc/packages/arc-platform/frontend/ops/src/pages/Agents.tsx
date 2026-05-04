import { useState } from "react";
import { Link } from "react-router-dom";
import { api, useFetch, ApiError } from "@arc/shared";
import type { AgentSummary } from "@arc/shared";

export default function Agents() {
  const { data, error, loading, refetch } = useFetch(() => api.listAgents());
  const [target, setTarget] = useState<AgentSummary | null>(null);

  if (loading) return <><h2>Agent inventory</h2><div className="state-msg">Loading…</div></>;
  if (error)   return <><h2>Agent inventory</h2><div className="state-msg error">{error.message}</div></>;

  const agents = data ?? [];

  if (agents.length === 0) {
    return (
      <>
        <h2>Agent inventory</h2>
        <div className="state-msg">
          No agents found. Configure a manifest root via{" "}
          <code>PlatformDataConfig.manifest_root</code>.
        </div>
      </>
    );
  }

  return (
    <>
      <h2>Agent inventory</h2>
      <table className="table">
        <thead>
          <tr>
            <th>Agent</th>
            <th>Owner</th>
            <th>Stage</th>
            <th>Status</th>
            <th>Environment</th>
            <th>Effects</th>
            <th>Controls</th>
          </tr>
        </thead>
        <tbody>
          {agents.map((a) => (
            <tr key={a.agent_id}>
              <td>
                <div style={{ fontWeight: 600 }}>{a.agent_id}</div>
                <div style={{ fontSize: "0.85rem", color: "var(--text-muted)" }}>
                  v{a.version} — {a.description}
                </div>
              </td>
              <td>{a.owner}</td>
              <td><span className="badge stage">{a.lifecycle_stage}</span></td>
              <td>
                {a.status === "suspended"
                  ? <span className="badge" style={{ background: "var(--danger, #c33)", color: "white" }}>SUSPENDED</span>
                  : a.status}
              </td>
              <td>{a.environment}</td>
              <td>{a.allowed_effects.length}</td>
              <td style={{ whiteSpace: "nowrap" }}>
                <Link
                  to={`/agents/${encodeURIComponent(a.agent_id)}/live`}
                  style={{
                    display: "inline-block",
                    padding: "4px 10px",
                    marginRight: "0.5rem",
                    borderRadius: "4px",
                    border: "1px solid var(--border, #ccc)",
                    color: "var(--text, #333)",
                    background: "transparent",
                    textDecoration: "none",
                    fontSize: "0.85rem",
                  }}
                >
                  View live
                </Link>
                <button
                  className="btn"
                  onClick={() => setTarget(a)}
                  style={{
                    background: a.status === "suspended" ? "var(--success, #0a8)" : "var(--danger, #c33)",
                    color: "white",
                    border: "none",
                    padding: "4px 10px",
                    borderRadius: "4px",
                    cursor: "pointer",
                    fontSize: "0.85rem",
                  }}
                >
                  {a.status === "suspended" ? "Resume" : "⚠ Suspend"}
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {target && (
        <ControlModal
          agent={target}
          onClose={() => setTarget(null)}
          onSuccess={() => {
            setTarget(null);
            refetch();
          }}
        />
      )}
    </>
  );
}


// ── Suspend / Resume modal ────────────────────────────────────────────────


interface ControlModalProps {
  agent: AgentSummary;
  onClose: () => void;
  onSuccess: () => void;
}

function ControlModal({ agent, onClose, onSuccess }: ControlModalProps) {
  const isSuspended = agent.status === "suspended";
  const action      = isSuspended ? "Resume" : "Suspend";
  const [reviewer, setReviewer] = useState("");
  const [reason, setReason]     = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr]           = useState<string | null>(null);

  async function submit() {
    setErr(null);
    if (!reviewer.trim()) {
      setErr("Reviewer is required (no anonymous actions).");
      return;
    }
    if (!isSuspended && !reason.trim()) {
      setErr("Reason is required for suspend.");
      return;
    }
    setSubmitting(true);
    try {
      if (isSuspended) {
        await api.resumeAgent(agent.agent_id, { reviewer: reviewer.trim(), reason: reason.trim() || "resumed" });
      } else {
        await api.suspendAgent(agent.agent_id, { reviewer: reviewer.trim(), reason: reason.trim() });
      }
      onSuccess();
    } catch (e) {
      setErr(e instanceof ApiError ? e.message : "Unknown error");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      style={{
        position: "fixed", top: 0, left: 0, right: 0, bottom: 0,
        background: "rgba(0,0,0,0.5)", display: "flex",
        alignItems: "center", justifyContent: "center", zIndex: 50,
      }}
      onClick={onClose}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        style={{
          background: "var(--bg, white)",
          padding: "1.5rem",
          borderRadius: "6px",
          minWidth: "420px",
          maxWidth: "560px",
          boxShadow: "0 10px 40px rgba(0,0,0,0.3)",
        }}
      >
        <h3 style={{ margin: 0 }}>
          {action} <code>{agent.agent_id}</code>
        </h3>
        <p style={{ color: "var(--text-muted)", fontSize: "0.9rem", marginTop: "0.5rem" }}>
          {isSuspended
            ? "Resume execution. New requests will start being processed; in-flight pending approvals are unaffected."
            : "This will halt all execution. In-flight runs finish; new requests are blocked. Pending approvals stay in the queue."}
        </p>

        <div style={{ marginTop: "1rem" }}>
          <label style={{ display: "block", fontSize: "0.85rem", fontWeight: 600 }}>
            Reviewer (your username)
          </label>
          <input
            type="text"
            value={reviewer}
            onChange={(e) => setReviewer(e.target.value)}
            placeholder="alice@compliance"
            style={{ width: "100%", padding: "6px 8px", marginTop: "4px" }}
            autoFocus
          />
        </div>

        <div style={{ marginTop: "0.75rem" }}>
          <label style={{ display: "block", fontSize: "0.85rem", fontWeight: 600 }}>
            Reason {!isSuspended && <span style={{ color: "var(--danger, #c33)" }}>*</span>}
          </label>
          <textarea
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder={isSuspended ? "(optional)" : "incident-1234, classifier returning wrong case_type"}
            rows={3}
            style={{ width: "100%", padding: "6px 8px", marginTop: "4px", resize: "vertical" }}
          />
        </div>

        {err && (
          <div className="state-msg error" style={{ marginTop: "0.75rem" }}>
            {err}
          </div>
        )}

        <div style={{ marginTop: "1.25rem", display: "flex", justifyContent: "flex-end", gap: "0.5rem" }}>
          <button
            onClick={onClose}
            disabled={submitting}
            style={{ padding: "6px 14px", border: "1px solid var(--border, #ccc)", background: "transparent", borderRadius: "4px", cursor: "pointer" }}
          >
            Cancel
          </button>
          <button
            onClick={submit}
            disabled={submitting}
            style={{
              padding: "6px 14px",
              background: isSuspended ? "var(--success, #0a8)" : "var(--danger, #c33)",
              color: "white",
              border: "none",
              borderRadius: "4px",
              cursor: submitting ? "wait" : "pointer",
            }}
          >
            {submitting ? "..." : (isSuspended ? "Resume agent" : "Suspend agent")}
          </button>
        </div>
      </div>
    </div>
  );
}
