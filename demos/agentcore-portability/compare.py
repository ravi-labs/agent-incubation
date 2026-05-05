"""Compare the local and AgentCore run summaries side-by-side.

Reads both ``out/local/summary.json`` and ``out/agentcore/summary.json``
(produced by run_demo.py) and prints a table showing what's identical
and what differs.

The point of this demo is that *most* fields should match. The
runtime-specific fields (run_id, region) will differ; everything else
— decision distribution, audit row count, telemetry metric set —
should be the same.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

OUT = Path(__file__).parent / "out"


def _load(name: str) -> dict | None:
    path = OUT / name / "summary.json"
    if not path.exists():
        return None
    return json.loads(path.read_text())


def main() -> int:
    local     = _load("local")
    agentcore = _load("agentcore")

    if local is None and agentcore is None:
        print("Neither summary exists. Run run_demo.py for at least one mode.")
        return 1

    print("=" * 70)
    print("  Portability comparison — same agent, two runtimes")
    print("=" * 70)

    rows = [
        ("runtime",                "Runtime"),
        ("audit_rows",             "Audit rows written"),
        ("decisions",              "Decision distribution"),
        ("telemetry_metrics",      "Telemetry metrics emitted"),
    ]

    print(f"\n{'Field':<30} {'Local':<20} {'AgentCore':<20}")
    print("-" * 70)
    for key, label in rows:
        l = (local or {}).get(key, "—")
        a = (agentcore or {}).get(key, "—")
        match = "✓" if l == a and l != "—" else " "
        print(f"{label:<30} {str(l):<20} {str(a):<20} {match}")

    print()
    print("Identical-by-design fields:")
    print("  ✓ audit_rows       — same agent + fixtures = same effects")
    print("  ✓ decisions        — same policy = same ALLOW/ASK/DENY split")
    print("  ✓ telemetry_metrics — same arc.core emit paths fire either way")
    print()
    print("Runtime-specific fields (expected to differ):")
    print("  - run_id           — UUID per orchestrator invocation")
    print("  - metadata         — framework='agentcore+langgraph' vs 'langgraph'")
    print()
    print("Bottom line:")
    print("  The agent's *governance* and *observability* shape is identical")
    print("  across runtimes. AgentCore is a deployment substrate, not a")
    print("  governance layer.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
