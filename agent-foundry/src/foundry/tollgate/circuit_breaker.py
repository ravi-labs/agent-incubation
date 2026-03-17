"""Circuit breaker for AI agent tool calls.

Tracks consecutive failures per (tool, action) pair. After a configured
threshold, the circuit "opens" and all subsequent calls are auto-denied
for a cooldown period.

States:
  CLOSED   → normal operation, failures counted
  OPEN     → blocking all calls, waiting for cooldown
  HALF_OPEN → cooldown expired, next call is a probe
               - if probe succeeds → CLOSED
               - if probe fails   → OPEN (cooldown resets)
"""

import asyncio
import time
from enum import Enum
from typing import Any, Protocol, runtime_checkable


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class _CircuitEntry:
    """Internal state for a single circuit."""

    __slots__ = ("state", "failure_count", "last_failure_at", "opened_at")

    def __init__(self):
        self.state: CircuitState = CircuitState.CLOSED
        self.failure_count: int = 0
        self.last_failure_at: float = 0.0
        self.opened_at: float = 0.0


@runtime_checkable
class CircuitBreaker(Protocol):
    """Protocol for circuit breaker backends."""

    async def before_call(self, tool: str, action: str) -> tuple[bool, str | None]:
        """Check if a call is allowed.

        Returns (allowed, reason). If not allowed, reason explains why.
        """
        ...

    async def record_success(self, tool: str, action: str) -> None:
        """Record a successful tool execution."""
        ...

    async def record_failure(self, tool: str, action: str) -> None:
        """Record a failed tool execution."""
        ...


class InMemoryCircuitBreaker:
    """In-memory circuit breaker with configurable thresholds.

    Args:
        failure_threshold: Number of consecutive failures before opening.
        cooldown_seconds: Seconds to wait before attempting a probe (HALF_OPEN).
        half_open_max_calls: Number of successful probes needed to close.

    Usage:
        breaker = InMemoryCircuitBreaker(failure_threshold=5, cooldown_seconds=60)

        tower = ControlTower(
            ...,
            circuit_breaker=breaker,
        )
    """

    def __init__(
        self,
        *,
        failure_threshold: int = 5,
        cooldown_seconds: float = 60.0,
        half_open_max_calls: int = 1,
    ):
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        if cooldown_seconds <= 0:
            raise ValueError("cooldown_seconds must be > 0")

        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self.half_open_max_calls = half_open_max_calls
        self._circuits: dict[str, _CircuitEntry] = {}
        self._lock = asyncio.Lock()

    @staticmethod
    def _key(tool: str, action: str) -> str:
        return f"{tool}:{action}"

    async def before_call(self, tool: str, action: str) -> tuple[bool, str | None]:
        """Check if the circuit allows a call through."""
        now = time.time()
        key = self._key(tool, action)

        async with self._lock:
            entry = self._circuits.get(key)
            if entry is None:
                return True, None

            if entry.state == CircuitState.CLOSED:
                return True, None

            if entry.state == CircuitState.OPEN:
                # Check if cooldown has elapsed
                elapsed = now - entry.opened_at
                if elapsed >= self.cooldown_seconds:
                    # Transition to HALF_OPEN — allow a probe
                    entry.state = CircuitState.HALF_OPEN
                    entry.failure_count = 0
                    return True, None
                remaining = self.cooldown_seconds - elapsed
                return False, (
                    f"Circuit OPEN for {tool}.{action}: "
                    f"{remaining:.1f}s remaining in cooldown "
                    f"(opened after {self.failure_threshold} consecutive failures)"
                )

            if entry.state == CircuitState.HALF_OPEN:
                # Allow the probe call through
                return True, None

        return True, None

    async def record_success(self, tool: str, action: str) -> None:
        """Record a success — close the circuit if in HALF_OPEN."""
        key = self._key(tool, action)

        async with self._lock:
            entry = self._circuits.get(key)
            if entry is None:
                return

            if entry.state == CircuitState.HALF_OPEN:
                # Probe succeeded — close the circuit
                entry.state = CircuitState.CLOSED
                entry.failure_count = 0

            elif entry.state == CircuitState.CLOSED:
                # Reset failure count on success
                entry.failure_count = 0

    async def record_failure(self, tool: str, action: str) -> None:
        """Record a failure — may open the circuit."""
        now = time.time()
        key = self._key(tool, action)

        async with self._lock:
            entry = self._circuits.get(key)
            if entry is None:
                entry = _CircuitEntry()
                self._circuits[key] = entry

            entry.failure_count += 1
            entry.last_failure_at = now

            if entry.state == CircuitState.HALF_OPEN:
                # Probe failed — back to OPEN
                entry.state = CircuitState.OPEN
                entry.opened_at = now

            elif entry.state == CircuitState.CLOSED:
                if entry.failure_count >= self.failure_threshold:
                    entry.state = CircuitState.OPEN
                    entry.opened_at = now

    async def get_state(self, tool: str, action: str) -> CircuitState:
        """Get the current circuit state (for monitoring/testing)."""
        key = self._key(tool, action)
        async with self._lock:
            entry = self._circuits.get(key)
            if entry is None:
                return CircuitState.CLOSED
            return entry.state

    async def reset(self, tool: str | None = None, action: str | None = None) -> None:
        """Reset circuit state. If tool/action given, reset only that circuit."""
        async with self._lock:
            if tool is not None and action is not None:
                key = self._key(tool, action)
                self._circuits.pop(key, None)
            else:
                self._circuits.clear()

    async def get_all_states(self) -> dict[str, dict[str, Any]]:
        """Get all circuit states (for monitoring dashboards)."""
        async with self._lock:
            return {
                key: {
                    "state": entry.state.value,
                    "failure_count": entry.failure_count,
                    "last_failure_at": entry.last_failure_at,
                    "opened_at": entry.opened_at,
                }
                for key, entry in self._circuits.items()
            }
