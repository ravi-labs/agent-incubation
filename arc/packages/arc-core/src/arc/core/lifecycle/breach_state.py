"""arc.core.lifecycle.breach_state — hysteresis state for the auto-demotion watcher.

The watcher needs to remember "agent X has now breached its SLO N runs in
a row" across CLI invocations so it can require N consecutive breaches
before firing a demotion. This module owns that state and nothing else.

Two implementations:

  - InMemoryBreachStateStore  — for tests + harness.
  - JsonlBreachStateStore     — file-backed, append-only. Each watcher run
                                appends one line; readers reconstruct the
                                latest state per agent (latest line wins).

The store keeps one record per agent. ``record(agent_id, breached)``
either bumps or resets the consecutive-breach counter and returns the
new state, so the watcher's call site is a one-liner.

State per agent:
  - consecutive_breaches  int     (0 means the last evaluation passed)
  - last_eval_at          ISO     when the last evaluation ran
  - last_breached_at      ISO     when the most recent breach was observed,
                                  empty string if never breached
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol


@dataclass
class BreachState:
    """Per-agent hysteresis counter."""
    agent_id: str
    consecutive_breaches: int = 0
    last_eval_at: str = ""
    last_breached_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "BreachState":
        return cls(
            agent_id             = d["agent_id"],
            consecutive_breaches = int(d.get("consecutive_breaches", 0)),
            last_eval_at         = d.get("last_eval_at", ""),
            last_breached_at     = d.get("last_breached_at", ""),
        )


class BreachStateStore(Protocol):
    """Persistent counter store for the watcher."""

    def get(self, agent_id: str) -> BreachState: ...
    def record(self, agent_id: str, *, breached: bool) -> BreachState: ...
    def reset(self, agent_id: str) -> None: ...


# ── In-memory ───────────────────────────────────────────────────────────────


class InMemoryBreachStateStore:
    """Default in-memory store. Loses state on process exit."""

    def __init__(self) -> None:
        self._states: dict[str, BreachState] = {}

    def get(self, agent_id: str) -> BreachState:
        return self._states.get(agent_id) or BreachState(agent_id=agent_id)

    def record(self, agent_id: str, *, breached: bool) -> BreachState:
        state = self.get(agent_id)
        now = _now()
        if breached:
            state.consecutive_breaches += 1
            state.last_breached_at = now
        else:
            state.consecutive_breaches = 0
        state.last_eval_at = now
        self._states[agent_id] = state
        return state

    def reset(self, agent_id: str) -> None:
        if agent_id in self._states:
            self._states[agent_id].consecutive_breaches = 0
            self._states[agent_id].last_eval_at = _now()


# ── JSONL ──────────────────────────────────────────────────────────────────


class JsonlBreachStateStore:
    """Append-only JSONL store. Latest line per agent_id wins on reload.

    The watcher runs once per cron tick — append-only is the simplest
    crash-safe shape. A reader rebuilds current state by walking the file
    and keeping the last line for each agent_id.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _read_all(self) -> dict[str, BreachState]:
        if not self.path.exists():
            return {}
        out: dict[str, BreachState] = {}
        with self.path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    raw = json.loads(line)
                    state = BreachState.from_dict(raw)
                except (json.JSONDecodeError, KeyError, ValueError):
                    continue
                out[state.agent_id] = state
        return out

    def _append(self, state: BreachState) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(state.to_dict(), ensure_ascii=False))
            f.write("\n")

    def get(self, agent_id: str) -> BreachState:
        return self._read_all().get(agent_id) or BreachState(agent_id=agent_id)

    def record(self, agent_id: str, *, breached: bool) -> BreachState:
        state = self.get(agent_id)
        now = _now()
        if breached:
            state.consecutive_breaches += 1
            state.last_breached_at = now
        else:
            state.consecutive_breaches = 0
        state.last_eval_at = now
        self._append(state)
        return state

    def reset(self, agent_id: str) -> None:
        state = self.get(agent_id)
        state.consecutive_breaches = 0
        state.last_eval_at = _now()
        self._append(state)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
