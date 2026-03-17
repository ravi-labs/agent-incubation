"""Audit Log Viewer CLI for Tollgate.

Provides a command-line interface for viewing, filtering, and analyzing
Tollgate audit logs in JSONL format.

Usage:

    # View recent events
    tollgate audit logs audit.jsonl

    # Filter by agent
    tollgate audit logs audit.jsonl --agent agent-1

    # Filter by outcome
    tollgate audit logs audit.jsonl --outcome blocked

    # Filter by time range
    tollgate audit logs audit.jsonl --since 2024-01-01 --until 2024-01-02

    # Show statistics
    tollgate audit stats audit.jsonl

    # Watch live (tail -f style)
    tollgate audit tail audit.jsonl
"""

import json
import sys
import time
from collections import Counter
from collections.abc import Generator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class AuditFilter:
    """Filters for audit log queries."""

    agent_id: str | None = None
    tool: str | None = None
    outcome: str | None = None
    decision: str | None = None
    since: datetime | None = None
    until: datetime | None = None
    correlation_id: str | None = None
    org_id: str | None = None

    def matches(self, event: dict[str, Any]) -> bool:
        """Check if an event matches this filter."""
        if self.agent_id:
            agent = event.get("agent", {})
            if agent.get("agent_id") != self.agent_id:
                return False

        if self.tool:
            req = event.get("tool_request", {})
            if not req.get("tool", "").startswith(self.tool):
                return False

        if self.outcome and event.get("outcome") != self.outcome:
            return False

        if self.decision:
            dec = event.get("decision", {})
            if dec.get("decision") != self.decision:
                return False

        if self.correlation_id and event.get("correlation_id") != self.correlation_id:
            return False

        if self.org_id:
            agent = event.get("agent", {})
            metadata = agent.get("metadata", {})
            if metadata.get("org_id") != self.org_id:
                return False

        # Time filters
        if self.since or self.until:
            ts_str = event.get("timestamp", "")
            try:
                # Parse ISO format timestamp
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                ts = ts.replace(tzinfo=None)  # Make naive for comparison

                if self.since and ts < self.since:
                    return False
                if self.until and ts > self.until:
                    return False
            except (ValueError, AttributeError):
                return False

        return True


class AuditLogReader:
    """Read and parse JSONL audit logs."""

    def __init__(self, log_path: str | Path):
        self.log_path = Path(log_path)
        if not self.log_path.exists():
            raise FileNotFoundError(f"Audit log not found: {self.log_path}")

    def read_events(
        self,
        filter_: AuditFilter | None = None,
        limit: int | None = None,
        reverse: bool = False,
    ) -> Generator[dict[str, Any], None, None]:
        """Read events from the log file.

        Args:
            filter_: Optional filter to apply.
            limit: Maximum number of events to return.
            reverse: If True, read from end (most recent first).

        Yields:
            Parsed event dictionaries.
        """
        events = []

        with self.log_path.open("r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue

                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if filter_ is None or filter_.matches(event):
                    events.append(event)

        if reverse:
            events = list(reversed(events))

        for i, event in enumerate(events):
            if limit and i >= limit:
                break
            yield event

    def tail(
        self,
        filter_: AuditFilter | None = None,
        follow: bool = True,
    ) -> Generator[dict[str, Any], None, None]:
        """Tail the log file (like tail -f).

        Args:
            filter_: Optional filter to apply.
            follow: If True, keep watching for new events.

        Yields:
            Parsed event dictionaries as they appear.
        """
        with self.log_path.open("r") as f:
            # Go to end of file
            f.seek(0, 2)

            while True:
                line = f.readline()

                if line:
                    line = line.strip()
                    if line:
                        try:
                            event = json.loads(line)
                            if filter_ is None or filter_.matches(event):
                                yield event
                        except json.JSONDecodeError:
                            pass
                elif follow:
                    time.sleep(0.1)
                else:
                    break

    def get_stats(
        self,
        filter_: AuditFilter | None = None,
    ) -> dict[str, Any]:
        """Compute statistics for the audit log.

        Args:
            filter_: Optional filter to apply.

        Returns:
            Dictionary with statistics.
        """
        total = 0
        outcomes: Counter[str] = Counter()
        decisions: Counter[str] = Counter()
        tools: Counter[str] = Counter()
        agents: Counter[str] = Counter()
        effects: Counter[str] = Counter()

        first_ts: str | None = None
        last_ts: str | None = None

        for event in self.read_events(filter_=filter_):
            total += 1

            outcomes[event.get("outcome", "unknown")] += 1

            dec = event.get("decision", {})
            decisions[dec.get("decision", "unknown")] += 1

            req = event.get("tool_request", {})
            tools[req.get("tool", "unknown")] += 1
            effects[req.get("effect", "unknown")] += 1

            agent = event.get("agent", {})
            agents[agent.get("agent_id", "unknown")] += 1

            ts = event.get("timestamp")
            if ts:
                if first_ts is None:
                    first_ts = ts
                last_ts = ts

        return {
            "total_events": total,
            "time_range": {
                "first": first_ts,
                "last": last_ts,
            },
            "outcomes": dict(outcomes.most_common()),
            "decisions": dict(decisions.most_common()),
            "top_tools": dict(tools.most_common(10)),
            "top_agents": dict(agents.most_common(10)),
            "effects": dict(effects.most_common()),
        }


def format_event(event: dict[str, Any], verbose: bool = False) -> str:
    """Format an event for display."""
    ts = event.get("timestamp", "?")
    outcome = event.get("outcome", "?")
    decision = event.get("decision", {}).get("decision", "?")

    agent = event.get("agent", {})
    agent_id = agent.get("agent_id", "?")

    req = event.get("tool_request", {})
    tool = req.get("tool", "?")
    action = req.get("action", "?")

    # Color based on outcome
    colors = {
        "executed": "\033[92m",  # Green
        "blocked": "\033[91m",  # Red
        "approval_denied": "\033[91m",  # Red
        "failed": "\033[93m",  # Yellow
        "timeout": "\033[93m",  # Yellow
    }
    color = colors.get(outcome, "")
    reset = "\033[0m" if color else ""

    line = f"{ts}  {color}{outcome:15}{reset}  {agent_id:20}  {tool}:{action}"

    if verbose:
        corr_id = event.get("correlation_id", "")
        reason = event.get("decision", {}).get("reason", "")
        lines = [line]
        lines.append(f"    Correlation ID: {corr_id}")
        lines.append(f"    Decision: {decision} - {reason}")
        if event.get("grant_id"):
            lines.append(f"    Grant ID: {event['grant_id']}")
        if event.get("approval_id"):
            lines.append(f"    Approval ID: {event['approval_id']}")
        return "\n".join(lines)

    return line


def format_stats(stats: dict[str, Any]) -> str:
    """Format statistics for display."""
    lines = [
        "",
        "=" * 70,
        "  AUDIT LOG STATISTICS",
        "=" * 70,
        "",
        f"  Total Events: {stats['total_events']}",
        f"  Time Range: {stats['time_range']['first'] or 'N/A'} to "
        f"{stats['time_range']['last'] or 'N/A'}",
        "",
        "  OUTCOMES",
        "  --------",
    ]

    for outcome, count in stats["outcomes"].items():
        pct = count / stats["total_events"] * 100 if stats["total_events"] else 0
        lines.append(f"    {outcome:20} {count:6} ({pct:5.1f}%)")

    lines.extend(
        [
            "",
            "  DECISIONS",
            "  ---------",
        ]
    )

    for decision, count in stats["decisions"].items():
        pct = count / stats["total_events"] * 100 if stats["total_events"] else 0
        lines.append(f"    {decision:20} {count:6} ({pct:5.1f}%)")

    lines.extend(
        [
            "",
            "  EFFECTS",
            "  -------",
        ]
    )

    for effect, count in stats["effects"].items():
        pct = count / stats["total_events"] * 100 if stats["total_events"] else 0
        lines.append(f"    {effect:20} {count:6} ({pct:5.1f}%)")

    lines.extend(
        [
            "",
            "  TOP TOOLS (10)",
            "  ---------------",
        ]
    )

    for tool, count in stats["top_tools"].items():
        lines.append(f"    {tool:30} {count:6}")

    lines.extend(
        [
            "",
            "  TOP AGENTS (10)",
            "  ----------------",
        ]
    )

    for agent, count in stats["top_agents"].items():
        lines.append(f"    {agent:30} {count:6}")

    lines.append("=" * 70)
    return "\n".join(lines)


def cli_main(args: list[str] | None = None) -> int:
    """CLI entry point for ``tollgate audit``.

    Usage:
        tollgate audit logs <file> [options]
        tollgate audit stats <file> [options]
        tollgate audit tail <file> [options]

    Returns exit code 0 on success, 1 on failure.
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="tollgate audit",
        description="View and analyze Tollgate audit logs.",
    )

    subparsers = parser.add_subparsers(dest="command", help="Command")

    # logs command
    logs_parser = subparsers.add_parser("logs", help="View audit log events")
    logs_parser.add_argument("log_file", help="Path to JSONL audit log")
    logs_parser.add_argument("--agent", "-a", help="Filter by agent ID")
    logs_parser.add_argument("--tool", "-t", help="Filter by tool (prefix match)")
    logs_parser.add_argument(
        "--outcome",
        "-o",
        choices=["executed", "blocked", "approval_denied", "failed", "timeout"],
        help="Filter by outcome",
    )
    logs_parser.add_argument(
        "--decision",
        "-d",
        choices=["ALLOW", "ASK", "DENY"],
        help="Filter by decision",
    )
    logs_parser.add_argument("--since", help="Filter events after this time (ISO)")
    logs_parser.add_argument("--until", help="Filter events before this time (ISO)")
    logs_parser.add_argument("--correlation-id", "-c", help="Filter by correlation ID")
    logs_parser.add_argument("--org", help="Filter by organization ID")
    logs_parser.add_argument(
        "--limit", "-n", type=int, default=100, help="Max events to show (default: 100)"
    )
    logs_parser.add_argument(
        "--reverse", "-r", action="store_true", help="Show most recent first"
    )
    logs_parser.add_argument(
        "--verbose", "-v", action="store_true", help="Show detailed event info"
    )
    logs_parser.add_argument(
        "--json", action="store_true", help="Output as JSON"
    )

    # stats command
    stats_parser = subparsers.add_parser("stats", help="Show audit log statistics")
    stats_parser.add_argument("log_file", help="Path to JSONL audit log")
    stats_parser.add_argument("--agent", "-a", help="Filter by agent ID")
    stats_parser.add_argument("--tool", "-t", help="Filter by tool (prefix match)")
    stats_parser.add_argument("--since", help="Filter events after this time")
    stats_parser.add_argument("--until", help="Filter events before this time")
    stats_parser.add_argument("--org", help="Filter by organization ID")
    stats_parser.add_argument("--json", action="store_true", help="Output as JSON")

    # tail command
    tail_parser = subparsers.add_parser("tail", help="Follow audit log in real-time")
    tail_parser.add_argument("log_file", help="Path to JSONL audit log")
    tail_parser.add_argument("--agent", "-a", help="Filter by agent ID")
    tail_parser.add_argument("--tool", "-t", help="Filter by tool (prefix match)")
    tail_parser.add_argument("--outcome", "-o", help="Filter by outcome")
    tail_parser.add_argument("--org", help="Filter by organization ID")
    tail_parser.add_argument(
        "--verbose", "-v", action="store_true", help="Show detailed event info"
    )

    parsed = parser.parse_args(args)

    if not parsed.command:
        parser.print_help()
        return 1

    # Build filter
    filter_ = AuditFilter(
        agent_id=getattr(parsed, "agent", None),
        tool=getattr(parsed, "tool", None),
        outcome=getattr(parsed, "outcome", None),
        decision=getattr(parsed, "decision", None),
        correlation_id=getattr(parsed, "correlation_id", None),
        org_id=getattr(parsed, "org", None),
    )

    # Parse time filters
    if hasattr(parsed, "since") and parsed.since:
        try:
            filter_.since = datetime.fromisoformat(parsed.since)
        except ValueError:
            print(f"Invalid --since format: {parsed.since}", file=sys.stderr)
            return 1

    if hasattr(parsed, "until") and parsed.until:
        try:
            filter_.until = datetime.fromisoformat(parsed.until)
        except ValueError:
            print(f"Invalid --until format: {parsed.until}", file=sys.stderr)
            return 1

    try:
        reader = AuditLogReader(parsed.log_file)
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    try:
        if parsed.command == "logs":
            events = list(
                reader.read_events(
                    filter_=filter_,
                    limit=parsed.limit,
                    reverse=parsed.reverse,
                )
            )

            if parsed.json:
                print(json.dumps(events, indent=2))
            else:
                if not events:
                    print("No events found matching filter.")
                else:
                    for event in events:
                        print(format_event(event, verbose=parsed.verbose))

        elif parsed.command == "stats":
            stats = reader.get_stats(filter_=filter_)

            if parsed.json:
                print(json.dumps(stats, indent=2))
            else:
                print(format_stats(stats))

        elif parsed.command == "tail":
            print("Watching for new events... (Ctrl+C to stop)")
            print("-" * 70)

            try:
                for event in reader.tail(filter_=filter_):
                    print(format_event(event, verbose=parsed.verbose))
            except KeyboardInterrupt:
                print("\nStopped.")

        return 0

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
