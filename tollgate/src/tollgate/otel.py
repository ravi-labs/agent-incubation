"""OpenTelemetry metrics and tracing integration for Tollgate.

This module provides AuditSink implementations that export metrics and traces
to any OTLP-compatible backend (Prometheus, Jaeger, Grafana, Tempo, etc.).

Requires: pip install tollgate[otel]

Example:
    from tollgate.otel import OTelMetricsAuditSink, OTelTracingAuditSink

    # Using global meter provider
    metrics_sink = OTelMetricsAuditSink()

    # Enable distributed tracing
    tracing_sink = OTelTracingAuditSink()

    # Or with custom OTLP endpoint (creates both metrics and tracing)
    from tollgate.otel import create_otel_sinks
    metrics_sink, tracing_sink = create_otel_sinks("localhost:4317")

    # Add to ControlTower audit pipeline
    from tollgate import CompositeAuditSink, JsonlAuditSink
    audit = CompositeAuditSink([
        JsonlAuditSink("audit.jsonl"), metrics_sink, tracing_sink
    ])
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from .types import AuditEvent, DecisionType

if TYPE_CHECKING:
    from opentelemetry.metrics import Counter, Histogram, Meter, MeterProvider
    from opentelemetry.trace import Span, Tracer, TracerProvider

logger = logging.getLogger("tollgate.otel")

# Metric names following OpenTelemetry semantic conventions
METRIC_PREFIX = "tollgate"


class OTelMetricsAuditSink:
    """AuditSink that exports metrics to OpenTelemetry.

    Metrics exported:
    - tollgate.decisions.total (Counter): Total decisions by type, tool, agent
    - tollgate.outcomes.total (Counter): Total outcomes by type, tool, agent
    - tollgate.execution.duration_ms (Histogram): Execution duration distribution
    - tollgate.grants.used.total (Counter): Grant usage count
    - tollgate.approvals.requested.total (Counter): Approval requests

    Attributes (labels):
    - decision_type: ALLOW, ASK, DENY
    - outcome: executed, blocked, failed, timeout, approval_denied
    - tool: Tool name
    - agent_id: Agent identifier
    - org_id: Organization ID (if present in agent metadata)
    - effect: read, write, delete, notify

    Example:
        from tollgate.otel import OTelMetricsAuditSink
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
            OTLPMetricExporter
        )

        # Setup OTLP exporter
        exporter = OTLPMetricExporter(endpoint="localhost:4317")
        provider = MeterProvider(metric_readers=[...])

        # Create sink with custom meter
        sink = OTelMetricsAuditSink(meter_provider=provider)

        # Or use the global meter provider
        sink = OTelMetricsAuditSink()
    """

    def __init__(
        self,
        meter_provider: MeterProvider | None = None,
        meter_name: str = "tollgate",
        meter_version: str | None = None,
    ):
        """Initialize the OpenTelemetry metrics sink.

        Args:
            meter_provider: Custom MeterProvider. If None, uses global provider.
            meter_name: Name for the meter instrument.
            meter_version: Version string for the meter.
        """
        try:
            from opentelemetry.metrics import get_meter_provider
        except ImportError as e:
            raise ImportError(
                "OpenTelemetry packages not installed. "
                "Install with: pip install tollgate[otel]"
            ) from e

        # Get version from package if not provided
        if meter_version is None:
            try:
                from . import __version__

                meter_version = __version__
            except ImportError:
                meter_version = "unknown"

        # Get meter from provider
        provider = meter_provider or get_meter_provider()
        self._meter: Meter = provider.get_meter(meter_name, meter_version)

        # Create instruments
        self._decisions_counter: Counter = self._meter.create_counter(
            name=f"{METRIC_PREFIX}.decisions.total",
            description="Total number of policy decisions",
            unit="1",
        )

        self._outcomes_counter: Counter = self._meter.create_counter(
            name=f"{METRIC_PREFIX}.outcomes.total",
            description="Total number of execution outcomes",
            unit="1",
        )

        self._duration_histogram: Histogram = self._meter.create_histogram(
            name=f"{METRIC_PREFIX}.execution.duration_ms",
            description="Execution duration in milliseconds",
            unit="ms",
        )

        self._grants_counter: Counter = self._meter.create_counter(
            name=f"{METRIC_PREFIX}.grants.used.total",
            description="Total number of grants used",
            unit="1",
        )

        self._approvals_counter: Counter = self._meter.create_counter(
            name=f"{METRIC_PREFIX}.approvals.requested.total",
            description="Total number of approval requests",
            unit="1",
        )

        # Track execution start times by correlation_id
        self._start_times: dict[str, float] = {}

    def record_start(self, correlation_id: str) -> None:
        """Record the start time for duration tracking.

        Call this before execute_async to enable duration metrics.
        """
        self._start_times[correlation_id] = time.monotonic()

    def emit(self, event: AuditEvent) -> None:
        """Emit metrics based on the audit event."""
        try:
            self._emit_metrics(event)
        except Exception:
            # Never let metrics failure affect the main flow
            logger.exception("Failed to emit OTel metrics")

    def _emit_metrics(self, event: AuditEvent) -> None:
        """Internal method to emit all metrics for an event."""
        # Common attributes
        base_attrs = {
            "agent_id": event.agent.agent_id,
            "tool": event.tool_request.tool,
            "effect": event.tool_request.effect.value,
        }

        # Add org_id if present in agent metadata
        org_id = event.agent.org_id
        if org_id:
            base_attrs["org_id"] = str(org_id)

        # Decision counter
        decision_attrs = {
            **base_attrs,
            "decision_type": event.decision.decision.value,
        }
        self._decisions_counter.add(1, decision_attrs)

        # Outcome counter
        outcome_attrs = {
            **base_attrs,
            "outcome": event.outcome.value,
        }
        self._outcomes_counter.add(1, outcome_attrs)

        # Grant usage
        if event.grant_id:
            self._grants_counter.add(1, base_attrs)

        # Approval tracking (ASK decisions that weren't granted)
        if event.decision.decision == DecisionType.ASK and not event.grant_id:
            self._approvals_counter.add(1, base_attrs)

        # Duration tracking
        start_time = self._start_times.pop(event.correlation_id, None)
        if start_time is not None:
            duration_ms = (time.monotonic() - start_time) * 1000
            self._duration_histogram.record(duration_ms, base_attrs)


class OTelTracingAuditSink:
    """AuditSink that creates distributed traces for Tollgate operations.

    Creates spans for each tool execution with detailed attributes about
    the policy decision, grant usage, and execution outcome.

    Span Names:
    - tollgate.execute: Root span for each tool execution
    - tollgate.policy.evaluate: Child span for policy evaluation
    - tollgate.grant.check: Child span for grant lookup
    - tollgate.approval.request: Child span for approval flow

    Attributes:
    - tollgate.agent.id: Agent identifier
    - tollgate.agent.version: Agent version
    - tollgate.agent.owner: Agent owner
    - tollgate.org.id: Organization ID (if present)
    - tollgate.tool.name: Tool being invoked
    - tollgate.tool.action: Action being performed
    - tollgate.tool.effect: Effect type (read, write, delete, notify)
    - tollgate.decision.type: Policy decision (ALLOW, ASK, DENY)
    - tollgate.decision.reason: Reason for the decision
    - tollgate.decision.policy_id: ID of the matching policy rule
    - tollgate.grant.id: Grant ID if a grant was used
    - tollgate.outcome: Execution outcome
    - tollgate.correlation.id: Correlation ID for tracking

    Example:
        from tollgate.otel import OTelTracingAuditSink
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter
        )

        # Setup OTLP exporter
        exporter = OTLPSpanExporter(endpoint="localhost:4317")
        provider = TracerProvider()
        provider.add_span_processor(...)

        # Create sink with custom tracer
        sink = OTelTracingAuditSink(tracer_provider=provider)

        # Or use the global tracer provider
        sink = OTelTracingAuditSink()
    """

    def __init__(
        self,
        tracer_provider: TracerProvider | None = None,
        tracer_name: str = "tollgate",
        tracer_version: str | None = None,
    ):
        """Initialize the OpenTelemetry tracing sink.

        Args:
            tracer_provider: Custom TracerProvider. If None, uses global provider.
            tracer_name: Name for the tracer instrument.
            tracer_version: Version string for the tracer.
        """
        try:
            from opentelemetry.trace import get_tracer_provider
        except ImportError as e:
            raise ImportError(
                "OpenTelemetry packages not installed. "
                "Install with: pip install tollgate[otel]"
            ) from e

        # Get version from package if not provided
        if tracer_version is None:
            try:
                from . import __version__

                tracer_version = __version__
            except ImportError:
                tracer_version = "unknown"

        # Get tracer from provider
        provider = tracer_provider or get_tracer_provider()
        self._tracer: Tracer = provider.get_tracer(tracer_name, tracer_version)

        # Track active spans by correlation_id for nesting
        self._active_spans: dict[str, Span] = {}

    def start_span(self, correlation_id: str, name: str = "tollgate.execute") -> Span:
        """Start a new span for a tool execution.

        Call this at the start of execute_async to create the parent span.
        Child spans will be created automatically based on audit events.

        Args:
            correlation_id: Unique ID for this execution.
            name: Span name. Default is "tollgate.execute".

        Returns:
            The created span. Use as context manager or call end() when done.
        """
        from opentelemetry.trace import SpanKind

        span = self._tracer.start_span(
            name=name,
            kind=SpanKind.INTERNAL,
            attributes={
                "tollgate.correlation.id": correlation_id,
            },
        )
        self._active_spans[correlation_id] = span
        return span

    def end_span(self, correlation_id: str) -> None:
        """End the span for a tool execution.

        Call this at the end of execute_async.

        Args:
            correlation_id: The correlation ID used when starting the span.
        """
        span = self._active_spans.pop(correlation_id, None)
        if span:
            span.end()

    def emit(self, event: AuditEvent) -> None:
        """Emit trace data based on the audit event."""
        try:
            self._emit_trace(event)
        except Exception:
            # Never let tracing failure affect the main flow
            logger.exception("Failed to emit OTel trace")

    def _emit_trace(self, event: AuditEvent) -> None:
        """Internal method to emit trace data for an event."""
        from opentelemetry.trace import Status, StatusCode

        # Get or create the parent span
        parent_span = self._active_spans.get(event.correlation_id)
        if parent_span is None:
            # No parent span exists, create one implicitly
            parent_span = self.start_span(event.correlation_id)

        # Add common attributes to the parent span
        parent_span.set_attribute("tollgate.agent.id", event.agent.agent_id)
        parent_span.set_attribute("tollgate.agent.version", event.agent.version)
        parent_span.set_attribute("tollgate.agent.owner", event.agent.owner)

        org_id = event.agent.org_id
        if org_id:
            parent_span.set_attribute("tollgate.org.id", str(org_id))

        parent_span.set_attribute("tollgate.tool.name", event.tool_request.tool)
        parent_span.set_attribute("tollgate.tool.action", event.tool_request.action)
        parent_span.set_attribute(
            "tollgate.tool.effect", event.tool_request.effect.value
        )

        # Add decision attributes
        parent_span.set_attribute(
            "tollgate.decision.type", event.decision.decision.value
        )
        if event.decision.reason:
            parent_span.set_attribute("tollgate.decision.reason", event.decision.reason)
        if event.decision.policy_id:
            parent_span.set_attribute(
                "tollgate.decision.policy_id", event.decision.policy_id
            )

        # Add grant information if present
        if event.grant_id:
            parent_span.set_attribute("tollgate.grant.id", event.grant_id)

        # Add outcome
        parent_span.set_attribute("tollgate.outcome", event.outcome.value)

        # Create child spans for specific phases
        self._create_policy_span(event, parent_span)

        if event.grant_id:
            self._create_grant_span(event, parent_span)

        if event.decision.decision == DecisionType.ASK and not event.grant_id:
            self._create_approval_span(event, parent_span)

        # Set span status based on outcome
        if event.outcome.value in ("executed",):
            parent_span.set_status(Status(StatusCode.OK))
        elif event.outcome.value in ("blocked", "failed", "timeout", "approval_denied"):
            parent_span.set_status(
                Status(StatusCode.ERROR, f"Outcome: {event.outcome.value}")
            )

    def _create_policy_span(self, event: AuditEvent, parent_span: Span) -> None:
        """Create a child span for policy evaluation."""
        from opentelemetry.trace import SpanKind, use_span

        with (
            use_span(parent_span, end_on_exit=False),
            self._tracer.start_as_current_span(
                "tollgate.policy.evaluate",
                kind=SpanKind.INTERNAL,
            ) as span,
        ):
            span.set_attribute(
                "tollgate.decision.type", event.decision.decision.value
            )
            if event.decision.policy_id:
                span.set_attribute(
                    "tollgate.decision.policy_id", event.decision.policy_id
                )
            if event.decision.reason:
                span.set_attribute(
                    "tollgate.decision.reason", event.decision.reason
                )

    def _create_grant_span(self, event: AuditEvent, parent_span: Span) -> None:
        """Create a child span for grant lookup/usage."""
        from opentelemetry.trace import SpanKind, use_span

        with (
            use_span(parent_span, end_on_exit=False),
            self._tracer.start_as_current_span(
                "tollgate.grant.check",
                kind=SpanKind.INTERNAL,
            ) as span,
        ):
            span.set_attribute("tollgate.grant.id", event.grant_id)
            span.set_attribute("tollgate.grant.found", True)

    def _create_approval_span(self, event: AuditEvent, parent_span: Span) -> None:
        """Create a child span for approval requests."""
        from opentelemetry.trace import SpanKind, Status, StatusCode, use_span

        with (
            use_span(parent_span, end_on_exit=False),
            self._tracer.start_as_current_span(
                "tollgate.approval.request",
                kind=SpanKind.INTERNAL,
            ) as span,
        ):
            span.set_attribute("tollgate.approval.required", True)

            if event.outcome.value == "approval_denied":
                span.set_status(Status(StatusCode.ERROR, "Approval denied"))
            elif event.outcome.value == "timeout":
                span.set_status(Status(StatusCode.ERROR, "Approval timeout"))


class TracingContextManager:
    """Context manager for automatic span lifecycle management.

    Use this to automatically start and end spans around tool executions.

    Example:
        tracing_sink = OTelTracingAuditSink()

        async def execute_tool(request):
            correlation_id = str(uuid.uuid4())
            with tracing_sink.trace(correlation_id) as span:
                span.set_attribute("custom.attr", "value")
                result = await tower.execute_async(ctx, intent, request)
            return result
    """

    def __init__(self, sink: OTelTracingAuditSink, correlation_id: str):
        self._sink = sink
        self._correlation_id = correlation_id
        self._span: Span | None = None

    def __enter__(self) -> Span:
        self._span = self._sink.start_span(self._correlation_id)
        return self._span

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if exc_type is not None and self._span is not None:
            # Record exception on span
            from opentelemetry.trace import Status, StatusCode

            self._span.record_exception(exc_val)
            self._span.set_status(Status(StatusCode.ERROR, str(exc_val)))
        self._sink.end_span(self._correlation_id)
        # Don't suppress exceptions (return None implicitly)


# Add trace() method to OTelTracingAuditSink
OTelTracingAuditSink.trace = lambda self, correlation_id: TracingContextManager(
    self, correlation_id
)


def create_otel_sink(
    endpoint: str | None = None,
    service_name: str = "tollgate",
    insecure: bool = True,
    **exporter_kwargs,
) -> OTelMetricsAuditSink:
    """Convenience factory to create an OTel sink with OTLP exporter.

    Args:
        endpoint: OTLP endpoint (e.g., "localhost:4317"). Uses env var if not set.
        service_name: Service name for resource attributes.
        insecure: Use insecure connection (no TLS). Default True for local dev.
        **exporter_kwargs: Additional arguments for OTLPMetricExporter.

    Returns:
        Configured OTelMetricsAuditSink

    Example:
        sink = create_otel_sink("localhost:4317")
    """
    from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
        OTLPMetricExporter,
    )
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import SERVICE_NAME, Resource

    resource = Resource.create({SERVICE_NAME: service_name})

    exporter = OTLPMetricExporter(
        endpoint=endpoint, insecure=insecure, **exporter_kwargs
    )
    reader = PeriodicExportingMetricReader(exporter)
    provider = MeterProvider(resource=resource, metric_readers=[reader])

    return OTelMetricsAuditSink(meter_provider=provider)


def create_otel_tracing_sink(
    endpoint: str | None = None,
    service_name: str = "tollgate",
    insecure: bool = True,
    **exporter_kwargs,
) -> OTelTracingAuditSink:
    """Convenience factory to create an OTel tracing sink with OTLP exporter.

    Args:
        endpoint: OTLP endpoint (e.g., "localhost:4317"). Uses env var if not set.
        service_name: Service name for resource attributes.
        insecure: Use insecure connection (no TLS). Default True for local dev.
        **exporter_kwargs: Additional arguments for OTLPSpanExporter.

    Returns:
        Configured OTelTracingAuditSink

    Example:
        sink = create_otel_tracing_sink("localhost:4317")
    """
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import SERVICE_NAME, Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    resource = Resource.create({SERVICE_NAME: service_name})

    exporter = OTLPSpanExporter(
        endpoint=endpoint, insecure=insecure, **exporter_kwargs
    )
    processor = BatchSpanProcessor(exporter)

    provider = TracerProvider(resource=resource)
    provider.add_span_processor(processor)

    return OTelTracingAuditSink(tracer_provider=provider)


def create_otel_sinks(
    endpoint: str | None = None,
    service_name: str = "tollgate",
    insecure: bool = True,
    **exporter_kwargs,
) -> tuple[OTelMetricsAuditSink, OTelTracingAuditSink]:
    """Convenience factory to create both metrics and tracing sinks.

    Args:
        endpoint: OTLP endpoint (e.g., "localhost:4317"). Uses env var if not set.
        service_name: Service name for resource attributes.
        insecure: Use insecure connection (no TLS). Default True for local dev.
        **exporter_kwargs: Additional arguments for exporters.

    Returns:
        Tuple of (OTelMetricsAuditSink, OTelTracingAuditSink)

    Example:
        metrics_sink, tracing_sink = create_otel_sinks("localhost:4317")

        # Add to ControlTower
        audit = CompositeAuditSink([metrics_sink, tracing_sink])
    """
    metrics_sink = create_otel_sink(
        endpoint=endpoint,
        service_name=service_name,
        insecure=insecure,
        **exporter_kwargs,
    )
    tracing_sink = create_otel_tracing_sink(
        endpoint=endpoint,
        service_name=service_name,
        insecure=insecure,
        **exporter_kwargs,
    )
    return metrics_sink, tracing_sink
