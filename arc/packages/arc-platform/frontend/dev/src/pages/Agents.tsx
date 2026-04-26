import { api, useFetch } from "@arc/shared";

export default function Agents() {
  const { data, error, loading } = useFetch(() => api.listAgents());

  if (loading) return <><h2>Agents</h2><div className="state-msg">Loading…</div></>;
  if (error)   return <><h2>Agents</h2><div className="state-msg error">{error.message}</div></>;

  const agents = data ?? [];
  if (agents.length === 0) {
    return (
      <>
        <h2>Agents</h2>
        <div className="state-msg">No agents found.</div>
      </>
    );
  }

  return (
    <>
      <h2>Agents</h2>
      <p style={{ color: "var(--text-muted)", marginTop: "-12px", marginBottom: "24px" }}>
        Engineering view: full effect declarations and tags. Harness-run links and
        eval-result drilldowns ship in a follow-up release.
      </p>
      <table className="table">
        <thead>
          <tr>
            <th>Agent</th>
            <th>Stage</th>
            <th>Env</th>
            <th>Effects (count)</th>
            <th>Tags</th>
          </tr>
        </thead>
        <tbody>
          {agents.map((a) => (
            <tr key={a.agent_id}>
              <td>
                <div style={{ fontWeight: 600 }}>{a.agent_id}</div>
                <div style={{ fontSize: "0.85rem", color: "var(--text-muted)" }}>
                  v{a.version} · {a.owner}
                </div>
              </td>
              <td><span className="badge stage">{a.lifecycle_stage}</span></td>
              <td>{a.environment}</td>
              <td>{a.allowed_effects.length}</td>
              <td style={{ fontSize: "0.85rem", color: "var(--text-muted)" }}>
                {a.tags.join(", ") || "—"}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
