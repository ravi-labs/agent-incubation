import { api, useFetch } from "@arc/shared";

export default function Overview() {
  const audit       = useFetch(() => api.auditSummary());
  const recent      = useFetch(() => api.listAudit({ limit: 5 }));
  const agentsQuery = useFetch(() => api.listAgents());

  if (audit.loading || recent.loading || agentsQuery.loading) {
    return <><h2>Engineering overview</h2><div className="state-msg">Loading…</div></>;
  }
  if (audit.error || recent.error || agentsQuery.error) {
    return (
      <>
        <h2>Engineering overview</h2>
        <div className="state-msg error">
          {(audit.error ?? recent.error ?? agentsQuery.error)?.message}
        </div>
      </>
    );
  }

  const a = audit.data!;
  const totalAgents = agentsQuery.data!.length;
  const recentEvents = recent.data!;

  return (
    <>
      <h2>Engineering overview</h2>
      <div className="cards">
        <div className="card">
          <div className="value">{totalAgents}</div>
          <div className="label">Agents in tree</div>
        </div>
        <div className="card">
          <div className="value">{a.total}</div>
          <div className="label">Audit rows</div>
        </div>
        <div className="card ok">
          <div className="value">{a.ALLOW}</div>
          <div className="label">ALLOW</div>
        </div>
        <div className="card warn">
          <div className="value">{a.ASK}</div>
          <div className="label">ASK</div>
        </div>
        <div className="card bad">
          <div className="value">{a.DENY}</div>
          <div className="label">DENY</div>
        </div>
      </div>

      <h3 style={{ marginTop: 32, marginBottom: 16 }}>Recent decisions</h3>
      {recentEvents.length === 0 ? (
        <div className="state-msg">
          No audit events yet. Run an agent against the harness to populate{" "}
          <code>audit.jsonl</code>.
        </div>
      ) : (
        <table className="table">
          <thead>
            <tr>
              <th>Time</th>
              <th>Agent</th>
              <th>Effect</th>
              <th>Decision</th>
              <th>Reason</th>
            </tr>
          </thead>
          <tbody>
            {recentEvents.map((e, i) => (
              <tr key={i}>
                <td style={{ fontSize: "0.85rem", color: "var(--text-muted)" }}>
                  {e.timestamp}
                </td>
                <td>{e.agent_id}</td>
                <td>{e.effect}</td>
                <td>
                  <span className={`badge ${e.decision.toLowerCase()}`}>
                    {e.decision}
                  </span>
                </td>
                <td style={{ color: "var(--text-muted)" }}>{e.reason}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
