import { api, useFetch } from "@arc/shared";

export default function Approvals() {
  const { data, error, loading } = useFetch(() => api.pendingApprovals());

  if (loading) return <><h2>Pending approvals</h2><div className="state-msg">Loading…</div></>;
  if (error)   return <><h2>Pending approvals</h2><div className="state-msg error">{error.message}</div></>;

  const approvals = data ?? [];

  if (approvals.length === 0) {
    return (
      <>
        <h2>Pending approvals</h2>
        <div className="state-msg">
          No pending approvals. Promotion decisions in the <code>DEFERRED</code> state
          (e.g. SCALE promotions awaiting human sign-off) appear here.
        </div>
      </>
    );
  }

  return (
    <>
      <h2>Pending approvals</h2>
      <p style={{ color: "var(--text-muted)", marginTop: "-12px", marginBottom: "24px" }}>
        Promotion decisions awaiting human review. The approve/reject form ships
        in the next release alongside the AsyncQueueApprover wiring.
      </p>
      <table className="table">
        <thead>
          <tr>
            <th>Agent</th>
            <th>Transition</th>
            <th>Requester</th>
            <th>Justification</th>
            <th>Submitted</th>
          </tr>
        </thead>
        <tbody>
          {approvals.map((a, i) => (
            <tr key={`${a.agent_id}-${i}`}>
              <td style={{ fontWeight: 600 }}>{a.agent_id}</td>
              <td>
                <span className="badge stage">{a.current_stage}</span>{" → "}
                <span className="badge stage">{a.target_stage}</span>
              </td>
              <td>{a.requester}</td>
              <td>{a.justification}</td>
              <td style={{ fontSize: "0.85rem", color: "var(--text-muted)" }}>
                {new Date(a.requested_at).toLocaleString()}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
