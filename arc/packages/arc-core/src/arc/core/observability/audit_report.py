"""
foundry.observability.audit_report
────────────────────────────────────
Generate a self-contained HTML audit dashboard from a Foundry JSONL audit log.

The report provides:
  - Summary counts (total decisions, ALLOW / ASK / DENY breakdown)
  - Filterable decision timeline table (by agent, effect, decision)
  - Per-agent effect usage breakdown
  - Hard-deny and ASK events highlighted for compliance review

Usage (CLI):
    python -m arc.core.observability.audit_report audit.jsonl
    python -m arc.core.observability.audit_report audit.jsonl --out report.html

Usage (programmatic):
    from arc.core.observability import generate_report
    html = generate_report("audit.jsonl")
    Path("report.html").write_text(html)
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ── Data loading ───────────────────────────────────────────────────────────────

def _load_events(path: str | Path) -> list[dict]:
    events = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return events


def _fmt_ts(ts: Any) -> str:
    if ts is None:
        return "—"
    try:
        dt = datetime.fromtimestamp(float(ts), tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return str(ts)


def _decision_badge(decision: str) -> str:
    colours = {
        "ALLOW": ("#16a34a", "#dcfce7"),
        "ASK":   ("#d97706", "#fef3c7"),
        "DENY":  ("#dc2626", "#fee2e2"),
    }
    bg, text_bg = colours.get(decision.upper(), ("#6b7280", "#f3f4f6"))
    return (
        f'<span style="background:{text_bg};color:{bg};padding:2px 8px;'
        f'border-radius:9999px;font-size:0.75rem;font-weight:700;'
        f'border:1px solid {bg}">{decision.upper()}</span>'
    )


# ── HTML generation ────────────────────────────────────────────────────────────

def generate_report(audit_path: str | Path, title: str = "Agent Foundry — Audit Report") -> str:
    path   = Path(audit_path)
    events = _load_events(path)

    if not events:
        return _empty_report(title, str(path))

    # ── Aggregate stats ──────────────────────────────────────────────────────
    total    = len(events)
    by_dec: dict[str, int]    = {}
    by_agent: dict[str, dict] = {}
    by_effect: dict[str, int] = {}

    rows = []
    for ev in events:
        decision  = str(ev.get("decision", "UNKNOWN")).upper()
        agent_id  = ev.get("agent_id", "unknown")
        effect    = ev.get("resource_type") or ev.get("effect") or "—"
        tool      = ev.get("tool", "—")
        intent    = ev.get("intent_reason", "—")
        timestamp = ev.get("timestamp") or ev.get("ts")

        by_dec[decision] = by_dec.get(decision, 0) + 1
        by_effect[effect] = by_effect.get(effect, 0) + 1

        if agent_id not in by_agent:
            by_agent[agent_id] = {"ALLOW": 0, "ASK": 0, "DENY": 0}
        by_agent[agent_id][decision] = by_agent[agent_id].get(decision, 0) + 1

        rows.append({
            "ts":       _fmt_ts(timestamp),
            "agent":    agent_id,
            "effect":   effect,
            "tool":     tool,
            "decision": decision,
            "intent":   intent,
        })

    allow_count = by_dec.get("ALLOW", 0)
    ask_count   = by_dec.get("ASK",   0)
    deny_count  = by_dec.get("DENY",  0)

    # ── Summary cards ────────────────────────────────────────────────────────
    def stat_card(label: str, value: int, colour: str) -> str:
        return f"""
        <div style="background:#fff;border:1px solid #e5e7eb;border-radius:8px;
                    padding:20px 24px;text-align:center;box-shadow:0 1px 3px rgba(0,0,0,.06)">
          <div style="font-size:2rem;font-weight:800;color:{colour}">{value}</div>
          <div style="font-size:0.85rem;color:#6b7280;margin-top:4px">{label}</div>
        </div>"""

    cards = f"""
    <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:16px;margin-bottom:32px">
      {stat_card("Total Decisions", total,       "#1d4ed8")}
      {stat_card("ALLOW",           allow_count, "#16a34a")}
      {stat_card("ASK (Deferred)",  ask_count,   "#d97706")}
      {stat_card("DENY",            deny_count,  "#dc2626")}
    </div>"""

    # ── Agent breakdown table ────────────────────────────────────────────────
    agent_rows = ""
    for agent_id, counts in sorted(by_agent.items()):
        a = counts.get("ALLOW", 0)
        s = counts.get("ASK",   0)
        d = counts.get("DENY",  0)
        agent_rows += f"""
        <tr>
          <td style="font-weight:500">{agent_id}</td>
          <td style="color:#16a34a;text-align:right">{a}</td>
          <td style="color:#d97706;text-align:right">{s}</td>
          <td style="color:#dc2626;text-align:right">{d}</td>
          <td style="text-align:right">{a+s+d}</td>
        </tr>"""

    agent_table = f"""
    <div style="margin-bottom:32px">
      <h2 style="font-size:1rem;font-weight:700;margin-bottom:12px;color:#111827">
        Per-Agent Summary
      </h2>
      <table style="width:100%;border-collapse:collapse;font-size:0.875rem">
        <thead>
          <tr style="border-bottom:2px solid #e5e7eb;color:#6b7280;text-align:left">
            <th style="padding:8px 0">Agent ID</th>
            <th style="padding:8px;text-align:right">ALLOW</th>
            <th style="padding:8px;text-align:right">ASK</th>
            <th style="padding:8px;text-align:right">DENY</th>
            <th style="padding:8px;text-align:right">Total</th>
          </tr>
        </thead>
        <tbody>{agent_rows}</tbody>
      </table>
    </div>"""

    # ── Decision timeline table ──────────────────────────────────────────────
    decision_rows = ""
    for row in reversed(rows):   # most recent first
        highlight = ""
        if row["decision"] == "DENY":
            highlight = "background:#fff5f5"
        elif row["decision"] == "ASK":
            highlight = "background:#fffbeb"

        decision_rows += f"""
        <tr class="evt-row" data-decision="{row['decision']}" data-agent="{row['agent']}"
            style="{highlight}">
          <td style="white-space:nowrap;color:#6b7280;font-size:0.8rem">{row['ts']}</td>
          <td style="font-weight:500">{row['agent']}</td>
          <td><code style="font-size:0.8rem;background:#f3f4f6;padding:1px 5px;
              border-radius:4px">{row['effect']}</code></td>
          <td style="color:#6b7280;font-size:0.85rem">{row['tool']}</td>
          <td>{_decision_badge(row['decision'])}</td>
          <td style="color:#6b7280;font-size:0.85rem;max-width:300px;
              overflow:hidden;text-overflow:ellipsis;white-space:nowrap"
              title="{row['intent']}">{row['intent']}</td>
        </tr>"""

    timeline_table = f"""
    <div>
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
        <h2 style="font-size:1rem;font-weight:700;color:#111827">Decision Timeline</h2>
        <div style="display:flex;gap:8px">
          <select id="filter-decision" onchange="filterRows()"
              style="font-size:0.8rem;padding:4px 8px;border:1px solid #e5e7eb;border-radius:6px">
            <option value="">All decisions</option>
            <option value="ALLOW">ALLOW</option>
            <option value="ASK">ASK</option>
            <option value="DENY">DENY</option>
          </select>
          <select id="filter-agent" onchange="filterRows()"
              style="font-size:0.8rem;padding:4px 8px;border:1px solid #e5e7eb;border-radius:6px">
            <option value="">All agents</option>
            {''.join(f'<option value="{a}">{a}</option>' for a in sorted(by_agent))}
          </select>
          <button onclick="clearFilters()"
              style="font-size:0.8rem;padding:4px 10px;border:1px solid #e5e7eb;
                     border-radius:6px;background:#f9fafb;cursor:pointer">
            Clear
          </button>
        </div>
      </div>
      <div style="overflow-x:auto">
        <table style="width:100%;border-collapse:collapse;font-size:0.875rem">
          <thead>
            <tr style="border-bottom:2px solid #e5e7eb;color:#6b7280;text-align:left">
              <th style="padding:8px 12px 8px 0">Timestamp</th>
              <th style="padding:8px 12px">Agent</th>
              <th style="padding:8px 12px">Effect</th>
              <th style="padding:8px 12px">Tool</th>
              <th style="padding:8px 12px">Decision</th>
              <th style="padding:8px 12px">Intent Reason</th>
            </tr>
          </thead>
          <tbody id="events-body">
            {decision_rows}
          </tbody>
        </table>
      </div>
      <div id="no-results" style="display:none;text-align:center;
          padding:32px;color:#9ca3af">No events match the current filter.</div>
    </div>"""

    # ── JavaScript for filtering ──────────────────────────────────────────────
    js = """
    <script>
    function filterRows() {
      const dec   = document.getElementById('filter-decision').value;
      const agent = document.getElementById('filter-agent').value;
      const rows  = document.querySelectorAll('.evt-row');
      let visible = 0;
      rows.forEach(row => {
        const matchDec   = !dec   || row.dataset.decision === dec;
        const matchAgent = !agent || row.dataset.agent    === agent;
        const show = matchDec && matchAgent;
        row.style.display = show ? '' : 'none';
        if (show) visible++;
      });
      document.getElementById('no-results').style.display = visible === 0 ? '' : 'none';
    }
    function clearFilters() {
      document.getElementById('filter-decision').value = '';
      document.getElementById('filter-agent').value    = '';
      filterRows();
    }
    </script>"""

    # ── Assemble full page ───────────────────────────────────────────────────
    generated_at = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{title}</title>
  <style>
    *, *::before, *::after {{ box-sizing: border-box; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            margin: 0; background: #f9fafb; color: #111827; }}
    table tbody tr {{ border-bottom: 1px solid #f3f4f6; }}
    table tbody tr:hover {{ background: #f9fafb !important; }}
    td, th {{ padding: 10px 12px; vertical-align: top; }}
    a {{ color: #2563eb; }}
  </style>
</head>
<body>
  <div style="max-width:1200px;margin:0 auto;padding:32px 24px">

    <!-- Header -->
    <div style="margin-bottom:28px;border-bottom:1px solid #e5e7eb;padding-bottom:20px">
      <h1 style="font-size:1.5rem;font-weight:800;margin:0 0 4px">
        🔍 {title}
      </h1>
      <p style="margin:0;color:#6b7280;font-size:0.875rem">
        Source: <code>{path.name}</code> &nbsp;·&nbsp;
        {total} events &nbsp;·&nbsp;
        Generated: {generated_at}
      </p>
    </div>

    <!-- Summary cards -->
    {cards}

    <!-- Per-agent summary -->
    {agent_table}

    <!-- Decision timeline -->
    {timeline_table}

  </div>
  {js}
</body>
</html>"""


def _empty_report(title: str, path: str) -> str:
    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>{title}</title></head>
<body style="font-family:sans-serif;padding:40px;color:#374151">
  <h1>{title}</h1>
  <p>No audit events found in <code>{path}</code>.</p>
</body></html>"""


# ── CLI entrypoint ─────────────────────────────────────────────────────────────

def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Generate an HTML audit report from a Foundry JSONL audit log.")
    parser.add_argument("audit_log", help="Path to the JSONL audit log file")
    parser.add_argument("--out", default=None, help="Output HTML file (default: <audit_log>.html)")
    parser.add_argument("--title", default="Agent Foundry — Audit Report", help="Report title")
    args = parser.parse_args()

    out_path = args.out or str(Path(args.audit_log).with_suffix(".html"))
    html     = generate_report(args.audit_log, title=args.title)
    Path(out_path).write_text(html, encoding="utf-8")
    print(f"Report written to: {out_path}")


if __name__ == "__main__":
    main()
