"""
DecisionReport — human-readable output from a harness run.

Shows every decision the agent made, flagging:
  - Actions that would require human approval in production (ASK)
  - Actions that were blocked (DENY)
  - Hard denies that fired
  - Overall accuracy metrics against expected outcomes (if provided)

Usage:
    report = DecisionReport(audit=shadow_sink, approver=sandbox_approver)
    report.print()          # coloured terminal output
    report.to_dict()        # structured dict for JSON export
    report.to_html()        # simple HTML for a browser viewer
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from foundry.tollgate.types import DecisionType


# ── ANSI colours for terminal output ─────────────────────────────────────────
_GREEN  = "\033[92m"
_YELLOW = "\033[93m"
_RED    = "\033[91m"
_CYAN   = "\033[96m"
_BOLD   = "\033[1m"
_DIM    = "\033[2m"
_RESET  = "\033[0m"


@dataclass
class DecisionReport:
    """
    Wraps a ShadowAuditSink and SandboxApprover to produce a
    structured harness run report.
    """

    audit:    Any   # ShadowAuditSink
    approver: Any   # SandboxApprover
    agent_id: str = "unknown"
    run_at:   str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    # ── Core data ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        """Structured dict — suitable for JSON export or further processing."""
        events = []
        for e in self.audit.events:
            d = e.decision.decision.value
            events.append({
                "resource_type": e.tool_request.resource_type,
                "effect":        e.tool_request.effect.value,
                "decision":      d,
                "reason":        e.decision.reason,
                "outcome":       e.outcome.value,
                "agent_id":      e.agent_id,
            })

        return {
            "agent_id":  self.agent_id,
            "run_at":    self.run_at,
            "summary":   self.audit.summary(),
            "ask_log":   self.approver.ask_log,
            "events":    events,
        }

    # ── Terminal output ───────────────────────────────────────────────────

    def print(self, *, colour: bool = True) -> None:
        """Print a human-readable report to stdout."""
        c = lambda code, text: (code + text + _RESET) if colour else text

        s = self.audit.summary()
        width = 60

        print()
        print(c(_BOLD, "─" * width))
        print(c(_BOLD, f"  HARNESS RUN REPORT  ·  {self.agent_id}"))
        print(c(_DIM,  f"  {self.run_at}"))
        print(c(_BOLD, "─" * width))
        print()

        # Summary row
        print(c(_BOLD, "  Summary"))
        print(f"  {'Total decisions':<28} {s['total']}")
        print(f"  {c(_GREEN, 'ALLOW'):<38} {s['allow']}")
        print(f"  {c(_YELLOW, 'ASK  (human approval in prod)'):<38} {s['ask']}")
        print(f"  {c(_RED, 'DENY'):<38} {s['deny']}")
        print(f"  {'Successful executions':<28} {s['success']}")
        print(f"  {'Errors':<28} {s['errors']}")
        print()

        # Decision log
        print(c(_BOLD, "  Decision log"))
        print(c(_DIM,  f"  {'Effect':<40} {'Decision':<10} {'Outcome'}"))
        print(c(_DIM,  "  " + "·" * 58))

        for e in self.audit.events:
            d = e.decision.decision
            if d == DecisionType.ALLOW:
                dec_str = c(_GREEN,  "ALLOW ")
            elif d == DecisionType.ASK:
                dec_str = c(_YELLOW, "ASK   ")
            else:
                dec_str = c(_RED,    "DENY  ")

            outcome = e.outcome.value.upper()
            resource = e.tool_request.resource_type
            if len(resource) > 38:
                resource = resource[:35] + "..."

            print(f"  {resource:<40} {dec_str}  {outcome}")

        # ASK callout
        if self.approver.ask_count > 0:
            print()
            print(c(_YELLOW, f"  ⚠  {self.approver.ask_count} action(s) would require human approval in production:"))
            for ask in self.approver.ask_log:
                print(c(_DIM, f"     • {ask['resource_type']}  ({ask['intent_action']})"))

        # Hard denies
        denied = self.audit.by_decision(DecisionType.DENY)
        if denied:
            print()
            print(c(_RED, f"  ✗  {len(denied)} action(s) were DENIED:"))
            for e in denied:
                print(c(_DIM, f"     • {e.tool_request.resource_type}  — {e.decision.reason}"))

        print()
        print(c(_BOLD, "─" * width))
        print()

    # ── HTML output ───────────────────────────────────────────────────────

    def to_html(self) -> str:
        """Simple HTML decision log — suitable for a browser-based viewer."""
        data = self.to_dict()
        s    = data["summary"]

        rows = ""
        for ev in data["events"]:
            d = ev["decision"]
            colour = {"ALLOW": "#059669", "ASK": "#d97706", "DENY": "#dc2626"}.get(d, "#475569")
            rows += (
                f'<tr>'
                f'<td style="font-family:monospace;font-size:12px">{ev["resource_type"]}</td>'
                f'<td><span style="color:{colour};font-weight:700">{d}</span></td>'
                f'<td style="color:#475569;font-size:12px">{ev["outcome"]}</td>'
                f'<td style="color:#94a3b8;font-size:11px">{ev["reason"]}</td>'
                f'</tr>\n'
            )

        return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>Harness Report · {self.agent_id}</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background:#f8fafc; color:#1e293b; padding:32px; }}
    h1   {{ font-size:22px; color:#1E2761; margin-bottom:4px; }}
    .sub {{ font-size:13px; color:#94a3b8; margin-bottom:24px; }}
    .stats {{ display:flex; gap:16px; margin-bottom:24px; }}
    .stat  {{ background:#fff; border:1.5px solid #e0e0e8; border-radius:8px;
               padding:12px 18px; min-width:90px; }}
    .stat-val {{ font-size:28px; font-weight:700; }}
    .stat-lbl {{ font-size:11px; color:#94a3b8; text-transform:uppercase; letter-spacing:.05em; }}
    table {{ width:100%; border-collapse:collapse; background:#fff;
              border-radius:10px; overflow:hidden;
              border:1.5px solid #e0e0e8; }}
    th    {{ background:#1E2761; color:#fff; text-align:left;
              padding:8px 14px; font-size:11px; text-transform:uppercase; letter-spacing:.05em; }}
    td    {{ padding:8px 14px; border-bottom:1px solid #f0f0f8; }}
  </style>
</head>
<body>
  <h1>Harness Run Report · {self.agent_id}</h1>
  <div class="sub">{self.run_at}</div>
  <div class="stats">
    <div class="stat"><div class="stat-val">{s["total"]}</div><div class="stat-lbl">Total</div></div>
    <div class="stat"><div class="stat-val" style="color:#059669">{s["allow"]}</div><div class="stat-lbl">Allow</div></div>
    <div class="stat"><div class="stat-val" style="color:#d97706">{s["ask"]}</div><div class="stat-lbl">ASK</div></div>
    <div class="stat"><div class="stat-val" style="color:#dc2626">{s["deny"]}</div><div class="stat-lbl">Deny</div></div>
    <div class="stat"><div class="stat-val" style="color:#7c3aed">{s["errors"]}</div><div class="stat-lbl">Errors</div></div>
  </div>
  <table>
    <tr><th>Effect</th><th>Decision</th><th>Outcome</th><th>Reason</th></tr>
    {rows}
  </table>
</body>
</html>"""

    # ── JSON export ───────────────────────────────────────────────────────

    def to_json(self, indent: int = 2) -> str:
        """Export the full report as a JSON string."""
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)
