"""SLO (Service Level Objectives) Monitoring & Alerting for Tollgate.

Provides real-time monitoring of key metrics with automatic alerting when
SLO thresholds are breached.

SLOs tracked:
- Availability: Percentage of successful executions
- Latency: P50, P95, P99 execution times
- Error Rate: Percentage of failed executions
- Approval Rate: Percentage of ASK decisions that get approved

Example:
    from tollgate.slo import SLOMonitor, SLOConfig, SLOAlert

    # Configure SLOs
    config = SLOConfig(
        availability_target=0.999,  # 99.9%
        latency_p99_ms=1000,        # 1 second
        error_rate_max=0.01,        # 1%
    )

    # Create monitor
    monitor = SLOMonitor(config)

    # Register alert handlers
    monitor.on_alert(lambda alert: print(f"SLO breach: {alert}"))

    # Record metrics (typically from audit events)
    monitor.record_execution(duration_ms=150, success=True)
    monitor.record_decision(decision_type="ALLOW")
"""

from __future__ import annotations

import logging
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger("tollgate.slo")


class SLOType(Enum):
    """Types of SLOs tracked."""

    AVAILABILITY = "availability"
    LATENCY_P50 = "latency_p50"
    LATENCY_P95 = "latency_p95"
    LATENCY_P99 = "latency_p99"
    ERROR_RATE = "error_rate"
    APPROVAL_RATE = "approval_rate"
    DENIAL_RATE = "denial_rate"


class AlertSeverity(Enum):
    """Alert severity levels."""

    WARNING = "warning"  # Approaching threshold
    CRITICAL = "critical"  # Threshold breached
    RECOVERED = "recovered"  # Returned to normal


@dataclass
class SLOConfig:
    """Configuration for SLO thresholds.

    All rate values are between 0.0 and 1.0 (percentages).
    Latency values are in milliseconds.
    """

    # Availability: success rate target (e.g., 0.999 = 99.9%)
    availability_target: float = 0.999

    # Latency thresholds in milliseconds
    latency_p50_ms: float = 100
    latency_p95_ms: float = 500
    latency_p99_ms: float = 1000

    # Error rate: maximum allowed (e.g., 0.01 = 1%)
    error_rate_max: float = 0.01

    # Approval rate: minimum expected (e.g., 0.8 = 80%)
    approval_rate_min: float = 0.5

    # Denial rate: maximum expected before alerting (e.g., 0.1 = 10%)
    denial_rate_max: float = 0.2

    # Warning threshold multiplier (alert when within this % of threshold)
    warning_threshold: float = 0.9  # 90% of threshold

    # Sliding window size for calculations
    window_size: int = 1000

    # Time window in seconds (0 = count-based window)
    time_window_seconds: float = 0

    # Minimum samples before alerting
    min_samples: int = 10


@dataclass
class SLOAlert:
    """Represents an SLO alert."""

    slo_type: SLOType
    severity: AlertSeverity
    current_value: float
    threshold: float
    message: str
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "slo_type": self.slo_type.value,
            "severity": self.severity.value,
            "current_value": self.current_value,
            "threshold": self.threshold,
            "message": self.message,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }


@dataclass
class SLOMetrics:
    """Current SLO metrics snapshot."""

    timestamp: float
    total_executions: int
    successful_executions: int
    failed_executions: int
    availability: float
    latency_p50_ms: float
    latency_p95_ms: float
    latency_p99_ms: float
    error_rate: float
    allow_count: int
    ask_count: int
    deny_count: int
    approval_rate: float
    denial_rate: float

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "timestamp": self.timestamp,
            "total_executions": self.total_executions,
            "successful_executions": self.successful_executions,
            "failed_executions": self.failed_executions,
            "availability": self.availability,
            "latency_p50_ms": self.latency_p50_ms,
            "latency_p95_ms": self.latency_p95_ms,
            "latency_p99_ms": self.latency_p99_ms,
            "error_rate": self.error_rate,
            "allow_count": self.allow_count,
            "ask_count": self.ask_count,
            "deny_count": self.deny_count,
            "approval_rate": self.approval_rate,
            "denial_rate": self.denial_rate,
        }


class SLOMonitor:
    """Real-time SLO monitoring with alerting.

    Tracks execution metrics in a sliding window and alerts when
    SLO thresholds are breached.
    """

    def __init__(self, config: SLOConfig | None = None):
        """Initialize the SLO monitor.

        Args:
            config: SLO configuration. Uses defaults if not provided.
        """
        self.config = config or SLOConfig()

        # Sliding windows for metrics
        self._executions: deque[tuple[float, bool]] = deque(
            maxlen=self.config.window_size
        )
        self._latencies: deque[tuple[float, float]] = deque(
            maxlen=self.config.window_size
        )
        self._decisions: deque[tuple[float, str]] = deque(
            maxlen=self.config.window_size
        )

        # Alert handlers
        self._alert_handlers: list[Callable[[SLOAlert], None]] = []

        # Track alert state to detect state changes
        self._alert_states: dict[SLOType, AlertSeverity | None] = dict.fromkeys(SLOType)

    def on_alert(self, handler: Callable[[SLOAlert], None]) -> None:
        """Register an alert handler.

        Args:
            handler: Callback function that receives SLOAlert objects.
        """
        self._alert_handlers.append(handler)

    def record_execution(
        self,
        duration_ms: float,
        success: bool,
        timestamp: float | None = None,
    ) -> None:
        """Record an execution result.

        Args:
            duration_ms: Execution duration in milliseconds.
            success: Whether the execution succeeded.
            timestamp: Optional timestamp (defaults to now).
        """
        ts = timestamp or time.time()

        self._executions.append((ts, success))
        self._latencies.append((ts, duration_ms))

        # Check SLOs after recording
        self._check_slos()

    def record_decision(
        self,
        decision_type: str,
        timestamp: float | None = None,
    ) -> None:
        """Record a policy decision.

        Args:
            decision_type: ALLOW, ASK, or DENY.
            timestamp: Optional timestamp (defaults to now).
        """
        ts = timestamp or time.time()
        self._decisions.append((ts, decision_type.upper()))

        # Check SLOs after recording
        self._check_slos()

    def get_metrics(self) -> SLOMetrics:
        """Get current SLO metrics snapshot."""
        now = time.time()

        # Filter by time window if configured
        if self.config.time_window_seconds > 0:
            cutoff = now - self.config.time_window_seconds
            executions = [(ts, s) for ts, s in self._executions if ts >= cutoff]
            latencies = [(ts, lat) for ts, lat in self._latencies if ts >= cutoff]
            decisions = [(ts, d) for ts, d in self._decisions if ts >= cutoff]
        else:
            executions = list(self._executions)
            latencies = list(self._latencies)
            decisions = list(self._decisions)

        # Calculate execution metrics
        total = len(executions)
        successful = sum(1 for _, s in executions if s)
        failed = total - successful

        availability = successful / total if total > 0 else 1.0
        error_rate = failed / total if total > 0 else 0.0

        # Calculate latency metrics
        latency_values = [lat for _, lat in latencies]
        if latency_values:
            sorted_latencies = sorted(latency_values)
            n = len(sorted_latencies)
            p50 = sorted_latencies[int(n * 0.50)]
            p95 = sorted_latencies[int(n * 0.95)] if n >= 2 else p50
            p99 = sorted_latencies[int(n * 0.99)] if n >= 2 else p50
        else:
            p50 = p95 = p99 = 0.0

        # Calculate decision metrics
        decision_types = [d for _, d in decisions]
        allow_count = sum(1 for d in decision_types if d == "ALLOW")
        ask_count = sum(1 for d in decision_types if d == "ASK")
        deny_count = sum(1 for d in decision_types if d == "DENY")
        decision_total = allow_count + ask_count + deny_count

        if decision_total > 0:
            approval_rate = (allow_count + ask_count) / decision_total
            denial_rate = deny_count / decision_total
        else:
            approval_rate = 1.0
            denial_rate = 0.0

        return SLOMetrics(
            timestamp=now,
            total_executions=total,
            successful_executions=successful,
            failed_executions=failed,
            availability=availability,
            latency_p50_ms=p50,
            latency_p95_ms=p95,
            latency_p99_ms=p99,
            error_rate=error_rate,
            allow_count=allow_count,
            ask_count=ask_count,
            deny_count=deny_count,
            approval_rate=approval_rate,
            denial_rate=denial_rate,
        )

    def check_slo(self, slo_type: SLOType) -> tuple[bool, float, float]:
        """Check a specific SLO.

        Returns:
            Tuple of (is_healthy, current_value, threshold)
        """
        metrics = self.get_metrics()

        if slo_type == SLOType.AVAILABILITY:
            return (
                metrics.availability >= self.config.availability_target,
                metrics.availability,
                self.config.availability_target,
            )
        if slo_type == SLOType.LATENCY_P50:
            return (
                metrics.latency_p50_ms <= self.config.latency_p50_ms,
                metrics.latency_p50_ms,
                self.config.latency_p50_ms,
            )
        if slo_type == SLOType.LATENCY_P95:
            return (
                metrics.latency_p95_ms <= self.config.latency_p95_ms,
                metrics.latency_p95_ms,
                self.config.latency_p95_ms,
            )
        if slo_type == SLOType.LATENCY_P99:
            return (
                metrics.latency_p99_ms <= self.config.latency_p99_ms,
                metrics.latency_p99_ms,
                self.config.latency_p99_ms,
            )
        if slo_type == SLOType.ERROR_RATE:
            return (
                metrics.error_rate <= self.config.error_rate_max,
                metrics.error_rate,
                self.config.error_rate_max,
            )
        if slo_type == SLOType.APPROVAL_RATE:
            return (
                metrics.approval_rate >= self.config.approval_rate_min,
                metrics.approval_rate,
                self.config.approval_rate_min,
            )
        if slo_type == SLOType.DENIAL_RATE:
            return (
                metrics.denial_rate <= self.config.denial_rate_max,
                metrics.denial_rate,
                self.config.denial_rate_max,
            )

        return True, 0.0, 0.0

    def _check_slos(self) -> None:
        """Check all SLOs and emit alerts if needed."""
        metrics = self.get_metrics()

        # Don't alert if we don't have enough samples
        if metrics.total_executions < self.config.min_samples:
            return

        # Check each SLO
        self._check_availability(metrics)
        self._check_latency(metrics)
        self._check_error_rate(metrics)
        self._check_decision_rates(metrics)

    def _check_availability(self, metrics: SLOMetrics) -> None:
        """Check availability SLO."""
        target = self.config.availability_target
        warning_threshold = target * self.config.warning_threshold

        if metrics.availability < target:
            self._emit_alert(
                SLOType.AVAILABILITY,
                AlertSeverity.CRITICAL,
                metrics.availability,
                target,
                f"Availability {metrics.availability:.2%} below target {target:.2%}",
            )
        elif metrics.availability < warning_threshold:
            self._emit_alert(
                SLOType.AVAILABILITY,
                AlertSeverity.WARNING,
                metrics.availability,
                target,
                f"Availability {metrics.availability:.2%} approaching target {target:.2%}",  # noqa: E501
            )
        else:
            self._maybe_emit_recovery(
                SLOType.AVAILABILITY, metrics.availability, target
            )

    def _check_latency(self, metrics: SLOMetrics) -> None:
        """Check latency SLOs."""
        # P50
        if metrics.latency_p50_ms > self.config.latency_p50_ms:
            self._emit_alert(
                SLOType.LATENCY_P50,
                AlertSeverity.CRITICAL,
                metrics.latency_p50_ms,
                self.config.latency_p50_ms,
                f"P50 latency {metrics.latency_p50_ms:.0f}ms exceeds {self.config.latency_p50_ms:.0f}ms",  # noqa: E501
            )
        else:
            self._maybe_emit_recovery(
                SLOType.LATENCY_P50, metrics.latency_p50_ms, self.config.latency_p50_ms
            )

        # P95
        if metrics.latency_p95_ms > self.config.latency_p95_ms:
            self._emit_alert(
                SLOType.LATENCY_P95,
                AlertSeverity.CRITICAL,
                metrics.latency_p95_ms,
                self.config.latency_p95_ms,
                f"P95 latency {metrics.latency_p95_ms:.0f}ms exceeds {self.config.latency_p95_ms:.0f}ms",  # noqa: E501
            )
        else:
            self._maybe_emit_recovery(
                SLOType.LATENCY_P95, metrics.latency_p95_ms, self.config.latency_p95_ms
            )

        # P99
        if metrics.latency_p99_ms > self.config.latency_p99_ms:
            self._emit_alert(
                SLOType.LATENCY_P99,
                AlertSeverity.CRITICAL,
                metrics.latency_p99_ms,
                self.config.latency_p99_ms,
                f"P99 latency {metrics.latency_p99_ms:.0f}ms exceeds {self.config.latency_p99_ms:.0f}ms",  # noqa: E501
            )
        else:
            self._maybe_emit_recovery(
                SLOType.LATENCY_P99, metrics.latency_p99_ms, self.config.latency_p99_ms
            )

    def _check_error_rate(self, metrics: SLOMetrics) -> None:
        """Check error rate SLO."""
        max_rate = self.config.error_rate_max
        warning_threshold = max_rate * self.config.warning_threshold

        if metrics.error_rate > max_rate:
            self._emit_alert(
                SLOType.ERROR_RATE,
                AlertSeverity.CRITICAL,
                metrics.error_rate,
                max_rate,
                f"Error rate {metrics.error_rate:.2%} exceeds {max_rate:.2%}",
            )
        elif metrics.error_rate > warning_threshold:
            self._emit_alert(
                SLOType.ERROR_RATE,
                AlertSeverity.WARNING,
                metrics.error_rate,
                max_rate,
                f"Error rate {metrics.error_rate:.2%} approaching {max_rate:.2%}",
            )
        else:
            self._maybe_emit_recovery(SLOType.ERROR_RATE, metrics.error_rate, max_rate)

    def _check_decision_rates(self, metrics: SLOMetrics) -> None:
        """Check decision rate SLOs."""
        # Approval rate
        if metrics.approval_rate < self.config.approval_rate_min:
            self._emit_alert(
                SLOType.APPROVAL_RATE,
                AlertSeverity.WARNING,
                metrics.approval_rate,
                self.config.approval_rate_min,
                f"Approval rate {metrics.approval_rate:.2%} below {self.config.approval_rate_min:.2%}",  # noqa: E501
            )
        else:
            self._maybe_emit_recovery(
                SLOType.APPROVAL_RATE,
                metrics.approval_rate,
                self.config.approval_rate_min,
            )

        # Denial rate
        if metrics.denial_rate > self.config.denial_rate_max:
            self._emit_alert(
                SLOType.DENIAL_RATE,
                AlertSeverity.WARNING,
                metrics.denial_rate,
                self.config.denial_rate_max,
                f"Denial rate {metrics.denial_rate:.2%} exceeds {self.config.denial_rate_max:.2%}",  # noqa: E501
            )
        else:
            self._maybe_emit_recovery(
                SLOType.DENIAL_RATE, metrics.denial_rate, self.config.denial_rate_max
            )

    def _emit_alert(
        self,
        slo_type: SLOType,
        severity: AlertSeverity,
        current_value: float,
        threshold: float,
        message: str,
    ) -> None:
        """Emit an alert to all handlers."""
        # Check if state changed
        prev_state = self._alert_states.get(slo_type)
        if prev_state == severity:
            return  # Already in this state, don't spam alerts

        self._alert_states[slo_type] = severity

        alert = SLOAlert(
            slo_type=slo_type,
            severity=severity,
            current_value=current_value,
            threshold=threshold,
            message=message,
        )

        for handler in self._alert_handlers:
            try:
                handler(alert)
            except Exception:
                logger.exception("Alert handler failed")

    def _maybe_emit_recovery(
        self,
        slo_type: SLOType,
        current_value: float,
        threshold: float,
    ) -> None:
        """Emit recovery alert if previously in alert state."""
        prev_state = self._alert_states.get(slo_type)
        if prev_state in (AlertSeverity.WARNING, AlertSeverity.CRITICAL):
            self._alert_states[slo_type] = None

            alert = SLOAlert(
                slo_type=slo_type,
                severity=AlertSeverity.RECOVERED,
                current_value=current_value,
                threshold=threshold,
                message=f"{slo_type.value} recovered to healthy state",
            )

            for handler in self._alert_handlers:
                try:
                    handler(alert)
                except Exception:
                    logger.exception("Alert handler failed")

    def reset(self) -> None:
        """Reset all metrics and alert states."""
        self._executions.clear()
        self._latencies.clear()
        self._decisions.clear()
        self._alert_states = dict.fromkeys(SLOType)


class SLOAuditSink:
    """AuditSink that feeds metrics to an SLO monitor.

    Use this to automatically track SLO metrics from audit events.

    Example:
        monitor = SLOMonitor(config)
        sink = SLOAuditSink(monitor)

        # Add to ControlTower
        tower = ControlTower(
            ...,
            audit=CompositeAuditSink([JsonlAuditSink("audit.jsonl"), sink]),
        )
    """

    def __init__(self, monitor: SLOMonitor):
        """Initialize the sink.

        Args:
            monitor: SLO monitor to feed metrics to.
        """
        self._monitor = monitor
        self._start_times: dict[str, float] = {}

    def record_start(self, correlation_id: str) -> None:
        """Record execution start time.

        Call this before execute_async to enable duration tracking.
        """
        self._start_times[correlation_id] = time.monotonic()

    def emit(self, event) -> None:
        """Process an audit event and update SLO metrics."""
        try:
            self._process_event(event)
        except Exception:
            logger.exception("Failed to process event for SLO")

    def _process_event(self, event) -> None:
        """Internal method to process an event."""
        # Record decision
        self._monitor.record_decision(event.decision.decision.value)

        # Determine success based on outcome
        outcome = event.outcome.value
        success = outcome in ("executed",)

        # Calculate duration if start time was recorded
        start_time = self._start_times.pop(event.correlation_id, None)
        if start_time is not None:
            duration_ms = (time.monotonic() - start_time) * 1000
        else:
            # Default to 0 if no start time
            duration_ms = 0

        self._monitor.record_execution(duration_ms=duration_ms, success=success)
