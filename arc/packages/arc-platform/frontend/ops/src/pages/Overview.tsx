import { api, useFetch } from "@arc/shared";

export default function Overview() {
  const audit       = useFetch(() => api.auditSummary());
  const promotions  = useFetch(() => api.promotionSummary());
  const agentsQuery = useFetch(() => api.listAgents());

  if (audit.loading || promotions.loading || agentsQuery.loading) {
    return (
      <>
        <h2>Overview</h2>
        <div className="state-msg">Loading…</div>
      </>
    );
  }

  if (audit.error || promotions.error || agentsQuery.error) {
    return (
      <>
        <h2>Overview</h2>
        <div className="state-msg error">
          {(audit.error ?? promotions.error ?? agentsQuery.error)?.message}
        </div>
      </>
    );
  }

  const a = audit.data!;
  const p = promotions.data!;
  const totalAgents = agentsQuery.data!.length;

  return (
    <>
      <h2>Overview</h2>
      <div className="cards">
        <div className="card">
          <div className="value">{totalAgents}</div>
          <div className="label">Agents tracked</div>
        </div>
        <div className="card warn">
          <div className="value">{p.DEFERRED}</div>
          <div className="label">Pending approvals</div>
        </div>
        <div className="card ok">
          <div className="value">{a.ALLOW}</div>
          <div className="label">Allowed actions</div>
        </div>
        <div className="card warn">
          <div className="value">{a.ASK}</div>
          <div className="label">Reviewed actions</div>
        </div>
        <div className="card bad">
          <div className="value">{a.DENY}</div>
          <div className="label">Denied actions</div>
        </div>
      </div>

      <p style={{ color: "var(--text-muted)" }}>
        {a.total} total runtime decisions audited · {p.total} promotion attempts on record.
      </p>
    </>
  );
}
