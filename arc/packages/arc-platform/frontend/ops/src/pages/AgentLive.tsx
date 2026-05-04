/**
 * AgentLive — per-agent live operations console.
 *
 * Three panes on one page:
 *   1. Header — agent identity + status + always-visible Suspend/Resume
 *   2. Stats card — rolling-window counts; polled every 5s
 *   3. Activity feed — recent audit rows; polled every 5s with
 *      verbosity-driven client-side filtering
 *
 * Feedback capture (the "Flag as wrong" button + corrections panel) is
 * a deferred roadmap item — the backend already supports it via the
 * /api/agents/{id}/corrections endpoints (see PR #13), but the UI
 * ships in a separate follow-up PR. Until then the live console is
 * purely monitoring + kill switch.
 */

import { useMemo, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { api, useFetch, usePolling, ApiError } from "@arc/shared";
import type { AgentSummary, AuditEvent } from "@arc/shared";

const POLL_INTERVAL_MS = 5_000;
const STATS_WINDOW_MIN = 60 * 24;     // 24h rolling window for the header card
const ACTIVITY_LIMIT   = 50;          // most recent N audit rows shown

type Verbosity = "quiet" | "normal" | "verbose";


// ── Top-level page component ─────────────────────────────────────────────


export default function AgentLive() {
  const { agentId = "" } = useParams<{ agentId: string }>();

  // Fetch agent identity once (refresh after suspend/resume).
  const agent = useFetch<AgentSummary>(() => api.getAgent(agentId), [agentId]);

  // Pause polling while a modal is open so numbers don't shift under the user.
  const [paused, setPaused] = useState(false);

  const stats = usePolling(
    () => api.agentStats(agentId, STATS_WINDOW_MIN),
    POLL_INTERVAL_MS,
    { paused, deps: [agentId] },
  );

  const activity = usePolling(
    () => api.listAudit({ agentId, limit: ACTIVITY_LIMIT }),
    POLL_INTERVAL_MS,
    { paused, deps: [agentId] },
  );

  const [controlOpen, setControlOpen] = useState(false);
  if (controlOpen !== paused) setPaused(controlOpen);

  const [verbosity, setVerbosity] = useState<Verbosity>("normal");

  if (agent.loading) {
    return <PageShell title="Loading…"><div className="state-msg">Loading agent…</div></PageShell>;
  }
  if (agent.error) {
    return (
      <PageShell title="Error">
        <div className="state-msg error">
          {agent.error.message}
          <div style={{ marginTop: "0.5rem" }}>
            <Link to="/agents">← Back to inventory</Link>
          </div>
        </div>
      </PageShell>
    );
  }

  const a = agent.data!;
  const isSuspended = a.status === "suspended";

  return (
    <>
      {/* Top header — kill switch is permanently visible at the right. */}
      <header style={headerStyle}>
        <div>
          <h2 style={{ margin: 0 }}>
            {a.agent_id}{" "}
            <span style={{ color: "var(--text-muted)", fontSize: "0.85rem", fontWeight: 400 }}>
              · LIVE
            </span>
          </h2>
          <div style={{ fontSize: "0.85rem", color: "var(--text-muted)", marginTop: 4 }}>
            <Link to="/agents">← Inventory</Link> · v{a.version} · {a.environment} · stage {a.lifecycle_stage}
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
          <StatusBadge status={a.status} />
          <button
            onClick={() => setControlOpen(true)}
            style={isSuspended ? primaryBtnStyle("var(--success, #0a8)") : primaryBtnStyle("var(--danger, #c33)")}
          >
            {isSuspended ? "Resume" : "⚠ Suspend"}
          </button>
        </div>
      </header>

      <StatsCard stats={stats.data} loading={stats.loading} />

      <div style={{ display: "flex", alignItems: "center", gap: "0.75rem", margin: "1rem 0 0.5rem" }}>
        <span style={{ fontSize: "0.85rem", color: "var(--text-muted)" }}>Verbosity</span>
        {(["quiet", "normal", "verbose"] as Verbosity[]).map((v) => (
          <button key={v} onClick={() => setVerbosity(v)} style={pillStyle(v === verbosity)}>
            {v}
          </button>
        ))}
        <span style={{ fontSize: "0.8rem", color: "var(--text-muted)", marginLeft: "auto" }}>
          {activity.isPolling ? "● live" : "○ paused"} · refreshing every {POLL_INTERVAL_MS / 1000}s
        </span>
      </div>

      <ActivityFeed events={activity.data ?? []} verbosity={verbosity} />

      {controlOpen && (
        <ControlModal
          agent={a}
          onClose={() => setControlOpen(false)}
          onSuccess={() => {
            setControlOpen(false);
            agent.refetch();
            stats.refetch();
          }}
        />
      )}
    </>
  );
}


// ── Sub-components ───────────────────────────────────────────────────────


function PageShell({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <>
      <h2>{title}</h2>
      {children}
    </>
  );
}


function StatusBadge({ status }: { status: string }) {
  const isSuspended = status === "suspended";
  return (
    <span
      className="badge"
      style={{
        background: isSuspended ? "var(--danger, #c33)" : "var(--success, #0a8)",
        color: "white",
        padding: "4px 10px",
        borderRadius: 4,
        fontSize: "0.85rem",
        fontWeight: 600,
      }}
    >
      {isSuspended ? "✕ SUSPENDED" : "● ACTIVE"}
    </span>
  );
}


interface StatsData {
  total: number;
  decisions: { ALLOW: number; ASK: number; DENY: number };
  decision_pct: { ALLOW: number; ASK: number; DENY: number };
  top_case_type: string;
  pending_approvals: number;
}

function StatsCard({ stats, loading }: { stats: StatsData | null; loading: boolean }) {
  if (loading && !stats) return <div className="state-msg">Loading stats…</div>;
  if (!stats) return null;

  return (
    <section style={cardStyle}>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(5, 1fr)", gap: "1rem" }}>
        <Stat label="Decisions / 24h"   value={String(stats.total)} />
        <Stat label="Pending approvals" value={String(stats.pending_approvals)} accent={stats.pending_approvals > 0 ? "var(--warning, #b80)" : undefined} />
        <Stat label="ALLOW"             value={`${stats.decision_pct.ALLOW}%`} />
        <Stat label="ASK"               value={`${stats.decision_pct.ASK}%`} />
        <Stat label="Top case-type"     value={stats.top_case_type || "—"} />
      </div>
      <RatioBar pct={stats.decision_pct} />
    </section>
  );
}

function Stat({ label, value, accent }: { label: string; value: string; accent?: string }) {
  return (
    <div>
      <div style={{ fontSize: "0.75rem", color: "var(--text-muted)", textTransform: "uppercase" }}>{label}</div>
      <div style={{ fontSize: "1.5rem", fontWeight: 700, color: accent }}>{value}</div>
    </div>
  );
}

function RatioBar({ pct }: { pct: { ALLOW: number; ASK: number; DENY: number } }) {
  return (
    <div
      role="img"
      aria-label={`Decisions: ALLOW ${pct.ALLOW}%, ASK ${pct.ASK}%, DENY ${pct.DENY}%`}
      style={{ display: "flex", height: 8, marginTop: "0.75rem", borderRadius: 4, overflow: "hidden", background: "var(--bg-subtle, #eee)" }}
    >
      <div style={{ width: `${pct.ALLOW}%`, background: "var(--success, #0a8)" }} title={`ALLOW ${pct.ALLOW}%`} />
      <div style={{ width: `${pct.ASK}%`,   background: "var(--warning, #b80)" }} title={`ASK ${pct.ASK}%`} />
      <div style={{ width: `${pct.DENY}%`,  background: "var(--danger, #c33)" }} title={`DENY ${pct.DENY}%`} />
    </div>
  );
}


function ActivityFeed({ events, verbosity }: { events: AuditEvent[]; verbosity: Verbosity }) {
  const filtered = useMemo(() => filterByVerbosity(events, verbosity), [events, verbosity]);

  if (filtered.length === 0) {
    return (
      <section style={cardStyle}>
        <div className="state-msg">
          No activity in the current window. {verbosity !== "verbose" && "Try Verbose to see all effects."}
        </div>
      </section>
    );
  }

  return (
    <section style={cardStyle}>
      <table className="table" style={{ width: "100%" }}>
        <thead>
          <tr>
            <th style={{ width: 90 }}>Time</th>
            <th style={{ width: 90 }}>Decision</th>
            <th>Effect</th>
            <th>Reason</th>
          </tr>
        </thead>
        <tbody>
          {filtered.map((ev, i) => (
            <tr key={`${ev.timestamp}-${i}`}>
              <td style={{ fontFamily: "monospace", fontSize: "0.8rem", color: "var(--text-muted)" }}>
                {formatTimeShort(ev.timestamp)}
              </td>
              <td><DecisionBadge decision={ev.decision} /></td>
              <td>
                <code style={{ fontSize: "0.85rem" }}>{ev.effect}</code>
                {ev.tool && (
                  <span style={{ color: "var(--text-muted)", fontSize: "0.8rem", marginLeft: 6 }}>· {ev.tool}</span>
                )}
              </td>
              <td style={{ fontSize: "0.85rem", color: "var(--text-muted)" }}>
                {truncate(ev.reason, 100)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}

function DecisionBadge({ decision }: { decision: string }) {
  const map: Record<string, { color: string; bg: string; icon: string }> = {
    ALLOW: { color: "white", bg: "var(--success, #0a8)", icon: "✓" },
    ASK:   { color: "white", bg: "var(--warning, #b80)", icon: "⏸" },
    DENY:  { color: "white", bg: "var(--danger, #c33)",  icon: "✕" },
  };
  const m = map[decision] ?? { color: "white", bg: "var(--text-muted, #888)", icon: "?" };
  return (
    <span style={{ background: m.bg, color: m.color, padding: "2px 8px", borderRadius: 3, fontSize: "0.75rem", fontWeight: 600 }}>
      {m.icon} {decision}
    </span>
  );
}


// ── Suspend / Resume modal — same shape as the inventory page's ────────


function ControlModal({
  agent, onClose, onSuccess,
}: { agent: AgentSummary; onClose: () => void; onSuccess: () => void }) {
  const isSuspended = agent.status === "suspended";
  const [reviewer, setReviewer] = useState("");
  const [reason, setReason]     = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [err, setErr]           = useState<string | null>(null);

  async function submit() {
    setErr(null);
    if (!reviewer.trim()) return setErr("Reviewer is required.");
    if (!isSuspended && !reason.trim()) return setErr("Reason is required for suspend.");
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
    <div role="dialog" aria-modal="true" onClick={onClose} style={overlayStyle}>
      <div onClick={(e) => e.stopPropagation()} style={modalStyle}>
        <h3 style={{ margin: 0 }}>{isSuspended ? "Resume" : "Suspend"} <code>{agent.agent_id}</code></h3>
        <p style={subtleStyle}>
          {isSuspended
            ? "Resume execution. New requests start being processed."
            : "Halt execution. In-flight runs finish; new requests blocked. Pending approvals stay queued."}
        </p>
        <div style={{ marginTop: "0.75rem" }}>
          <label style={fieldLabelStyle}>Reviewer (your username)</label>
          <input type="text" value={reviewer} onChange={(e) => setReviewer(e.target.value)} placeholder="alice@compliance" autoFocus style={inputStyle} />
        </div>
        <div style={{ marginTop: "0.75rem" }}>
          <label style={fieldLabelStyle}>Reason {!isSuspended && <span style={{ color: "var(--danger, #c33)" }}>*</span>}</label>
          <textarea value={reason} onChange={(e) => setReason(e.target.value)} rows={3} placeholder={isSuspended ? "(optional)" : "incident-1234, classifier returning wrong case_type"} style={{ ...inputStyle, resize: "vertical" }} />
        </div>
        {err && <div className="state-msg error" style={{ marginTop: "0.5rem" }}>{err}</div>}
        <div style={{ marginTop: "1.25rem", display: "flex", justifyContent: "flex-end", gap: "0.5rem" }}>
          <button onClick={onClose} disabled={submitting} style={cancelBtnStyle}>Cancel</button>
          <button
            onClick={submit}
            disabled={submitting}
            style={primaryBtnStyle(isSuspended ? "var(--success, #0a8)" : "var(--danger, #c33)")}
          >
            {submitting ? "..." : (isSuspended ? "Resume agent" : "Suspend agent")}
          </button>
        </div>
      </div>
    </div>
  );
}


// ── Helpers + styles ─────────────────────────────────────────────────────


function filterByVerbosity(events: AuditEvent[], v: Verbosity): AuditEvent[] {
  if (v === "verbose") return events;
  if (v === "quiet") {
    return events.filter((e) => e.decision === "ASK" || e.decision === "DENY");
  }
  return events.filter((e) => {
    if (e.decision !== "ALLOW") return true;
    return /\b(create|send|suspend|resume|notify|update)\b/.test(e.effect.toLowerCase());
  });
}

function formatTimeShort(ts: string): string {
  if (!ts) return "—";
  return ts.slice(11, 19) || ts;
}

function truncate(s: string, n: number): string {
  if (!s) return "";
  return s.length <= n ? s : s.slice(0, n - 1) + "…";
}

const headerStyle: React.CSSProperties = {
  display: "flex", alignItems: "center", justifyContent: "space-between",
  padding: "0.75rem 0 1rem",
  borderBottom: "1px solid var(--border, #ddd)",
  marginBottom: "1rem",
};
const cardStyle: React.CSSProperties = {
  background: "var(--bg-card, white)",
  border: "1px solid var(--border, #ddd)",
  borderRadius: 6, padding: "1rem",
};
const overlayStyle: React.CSSProperties = {
  position: "fixed", top: 0, left: 0, right: 0, bottom: 0,
  background: "rgba(0,0,0,0.5)",
  display: "flex", alignItems: "center", justifyContent: "center",
  zIndex: 50,
};
const modalStyle: React.CSSProperties = {
  background: "var(--bg, white)", padding: "1.5rem", borderRadius: 6,
  minWidth: 460, maxWidth: 600,
  boxShadow: "0 10px 40px rgba(0,0,0,0.3)",
};
const inputStyle: React.CSSProperties = {
  width: "100%", padding: "6px 8px",
  border: "1px solid var(--border, #ccc)", borderRadius: 4,
};
const subtleStyle: React.CSSProperties = {
  color: "var(--text-muted)", fontSize: "0.9rem", marginTop: "0.5rem",
};
const fieldLabelStyle: React.CSSProperties = {
  display: "block", fontSize: "0.85rem", fontWeight: 600, marginBottom: 4,
};
const cancelBtnStyle: React.CSSProperties = {
  padding: "6px 14px", background: "transparent",
  border: "1px solid var(--border, #ccc)", borderRadius: 4, cursor: "pointer",
};
function primaryBtnStyle(color: string): React.CSSProperties {
  return {
    padding: "6px 14px", background: color, color: "white",
    border: "none", borderRadius: 4, cursor: "pointer", fontWeight: 500,
  };
}
function pillStyle(active: boolean): React.CSSProperties {
  return {
    padding: "4px 12px",
    background: active ? "var(--accent, #06c)" : "transparent",
    color: active ? "white" : "var(--text, #333)",
    border: `1px solid ${active ? "var(--accent, #06c)" : "var(--border, #ccc)"}`,
    borderRadius: 16, cursor: "pointer", fontSize: "0.85rem",
    textTransform: "capitalize",
  };
}
