import { api, useFetch } from "@arc/shared";

export default function Agents() {
  const { data, error, loading } = useFetch(() => api.listAgents());

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
              <td>{a.status}</td>
              <td>{a.environment}</td>
              <td>{a.allowed_effects.length}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}
