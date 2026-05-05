"""
arc.core.telemetry — operational metrics for arc agents.

The single emission point that arc-* modules call when they want to
record an operational metric (effect outcome, latency, token cost, SLO
breach, redaction match). Two production targets ship in-the-box:

  * **CloudWatch (EMF)** — structured JSON to stdout. CloudWatch
    automatically extracts metrics from Embedded Metric Format logs in
    Lambda + ECS Fargate. No SDK calls, no extra latency, no extra cost
    beyond the log line itself.

  * **Datadog (DogStatsD)** — UDP packets to ``127.0.0.1:8125`` when a
    Datadog Agent / Lambda Extension is reachable. Falls back to no-op
    silently if not. No `datadog` Python SDK dependency required.

Both can run together via ``MultiTelemetry``. The default is
``NoOpTelemetry`` — zero overhead in tests, sandboxes, and any code
path that doesn't opt in.

Design rules:

  1. **Never raise.** Telemetry that crashes business logic is worse
     than telemetry that silently misses. Every emit path swallows
     exceptions and logs at DEBUG.
  2. **Never block.** All emits are O(1) and synchronous-safe. UDP is
     fire-and-forget; stdout writes are buffered.
  3. **Cardinality discipline.** Tag values must be bounded. Don't
     emit ``run_id`` or ``user_id`` as a tag — those go in the audit
     log, not the metric stream.
  4. **Single vocabulary.** All metrics use the ``arc.`` prefix. Tag
     keys are snake_case. Decision values are upper-case
     (``ALLOW`` / ``ASK`` / ``DENY``).

Standard arc metrics:

  ``arc.effect.outcome``        counter   tags: agent_id, effect, decision
  ``arc.effect.latency_ms``     timing    tags: agent_id, effect
  ``arc.llm.tokens_in``         counter   tags: agent_id, model, provider
  ``arc.llm.tokens_out``        counter   tags: agent_id, model, provider
  ``arc.llm.cost_usd``          counter   tags: agent_id, model, provider
  ``arc.outcome.event``         counter   tags: agent_id, event_type
  ``arc.slo.breach``            counter   tags: agent_id, slo, severity
  ``arc.redaction.match``       counter   tags: pattern
  ``arc.approval.duration_ms``  timing    tags: agent_id, effect

Configure from environment:

    export ARC_TELEMETRY=cloudwatch+datadog
    export ARC_TELEMETRY_NAMESPACE=Arc
    export DD_AGENT_HOST=127.0.0.1   # optional; default 127.0.0.1
    export DD_DOGSTATSD_PORT=8125    # optional

Then in code:

    from arc.core import telemetry_from_env, BaseAgent
    agent = MyAgent(..., telemetry=telemetry_from_env())
"""

from __future__ import annotations

import json
import logging
import os
import socket
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

# Default metric namespace for CloudWatch + Datadog metric names.
_DEFAULT_NAMESPACE = "Arc"

# Tag values are stringified to keep emit paths safe.
Tags = Mapping[str, Any]


# ── Protocol ────────────────────────────────────────────────────────────────


@runtime_checkable
class Telemetry(Protocol):
    """The contract every emitter implements.

    Three primitives cover everything arc needs. Histograms are modelled
    as ``timing`` since most arc histograms are durations.
    """

    def count(self, name: str, value: float = 1.0, tags: Tags | None = None) -> None:
        """Increment a counter (e.g. ``arc.effect.outcome``)."""

    def gauge(self, name: str, value: float, tags: Tags | None = None) -> None:
        """Record an instantaneous gauge value (e.g. queue depth)."""

    def timing(self, name: str, value_ms: float, tags: Tags | None = None) -> None:
        """Record a duration in milliseconds (e.g. ``arc.effect.latency_ms``)."""


# ── NoOp (default) ──────────────────────────────────────────────────────────


class NoOpTelemetry:
    """Zero-cost emitter. Used in tests, sandbox, and when nothing is wired."""

    def count(self, name: str, value: float = 1.0, tags: Tags | None = None) -> None:
        return None

    def gauge(self, name: str, value: float, tags: Tags | None = None) -> None:
        return None

    def timing(self, name: str, value_ms: float, tags: Tags | None = None) -> None:
        return None


# ── CloudWatch (Embedded Metric Format) ─────────────────────────────────────


@dataclass
class CloudWatchEMFTelemetry:
    """Emit metrics as CloudWatch Embedded Metric Format JSON to stdout.

    CloudWatch Logs auto-extracts metrics from log lines that contain
    the ``_aws.CloudWatchMetrics`` envelope. Works in Lambda, ECS
    Fargate (with the awslogs driver), EKS, and anywhere else that
    forwards stdout to CloudWatch Logs.

    Reference:
      https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch_Embedded_Metric_Format_Specification.html

    Why EMF instead of the boto3 ``put_metric_data`` API:

      * Zero SDK dependency on the hot path.
      * No extra network call (and no rate-limit per-account ceiling).
      * No additional cost — you already pay for the log line.
      * Works identically locally (writes to stdout, easy to inspect).

    Tags become CloudWatch dimensions. Keep cardinality bounded; CW
    bills per unique dimension combination.
    """

    namespace: str = _DEFAULT_NAMESPACE
    stream: Any = field(default=None)  # writable; defaults to sys.stdout

    def _write(self, name: str, value: float, unit: str, tags: Tags | None) -> None:
        try:
            tag_dict = {k: str(v) for k, v in (tags or {}).items()}
            payload = {
                "_aws": {
                    "Timestamp": int(time.time() * 1000),
                    "CloudWatchMetrics": [
                        {
                            "Namespace": self.namespace,
                            "Dimensions": [list(tag_dict.keys())] if tag_dict else [[]],
                            "Metrics": [{"Name": name, "Unit": unit}],
                        }
                    ],
                },
                **tag_dict,
                name: value,
            }
            (self.stream or sys.stdout).write(json.dumps(payload) + "\n")
        except Exception as exc:  # noqa: BLE001 — telemetry MUST NOT raise
            logger.debug("cloudwatch_emf_emit_failed name=%s err=%s", name, exc)

    def count(self, name: str, value: float = 1.0, tags: Tags | None = None) -> None:
        self._write(name, float(value), "Count", tags)

    def gauge(self, name: str, value: float, tags: Tags | None = None) -> None:
        self._write(name, float(value), "None", tags)

    def timing(self, name: str, value_ms: float, tags: Tags | None = None) -> None:
        self._write(name, float(value_ms), "Milliseconds", tags)


# ── Datadog (DogStatsD UDP) ─────────────────────────────────────────────────


@dataclass
class DatadogTelemetry:
    """Emit metrics over DogStatsD UDP.

    DogStatsD is the wire protocol the Datadog Agent + Lambda Extension
    listen on. Sending a UDP packet to ``host:port`` is fire-and-forget;
    if no listener is present the packet is dropped, which is the
    desired behaviour (telemetry must never block business logic).

    Use this when:
      * Running on ECS/EKS with the ``datadog-agent`` sidecar.
      * Running on Lambda with the Datadog Lambda Extension layer.
      * Local dev with the Datadog Agent installed.

    For Lambda *without* the extension, prefer ``CloudWatchEMFTelemetry``
    plus the standard CloudWatch → Datadog forwarder (cheaper and
    simpler).

    Wire format (subset we use):

        arc.effect.outcome:1|c|#agent_id:email-triage,decision:ALLOW
        arc.effect.latency_ms:42.7|ms|#agent_id:email-triage,effect:ticket.create

    Reference:
      https://docs.datadoghq.com/developers/dogstatsd/datagram_shell/
    """

    host: str = "127.0.0.1"
    port: int = 8125
    namespace: str = "arc"
    constant_tags: Tags = field(default_factory=dict)
    _sock: Any = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setblocking(False)
        except Exception as exc:  # noqa: BLE001
            logger.debug("dogstatsd_socket_init_failed err=%s", exc)
            self._sock = None

    def _send(self, payload: str) -> None:
        if not self._sock:
            return
        try:
            self._sock.sendto(payload.encode("utf-8"), (self.host, self.port))
        except Exception as exc:  # noqa: BLE001 — UDP send must not raise
            logger.debug("dogstatsd_send_failed err=%s", exc)

    def _format(self, name: str, value: float, mtype: str, tags: Tags | None) -> str:
        merged = {**self.constant_tags, **(tags or {})}
        tag_str = ",".join(f"{k}:{v}" for k, v in merged.items())
        # namespace prefix — DogStatsD convention is dot-separated.
        full = f"{self.namespace}.{name}" if self.namespace else name
        suffix = f"|#{tag_str}" if tag_str else ""
        return f"{full}:{value}|{mtype}{suffix}"

    def count(self, name: str, value: float = 1.0, tags: Tags | None = None) -> None:
        self._send(self._format(name, float(value), "c", tags))

    def gauge(self, name: str, value: float, tags: Tags | None = None) -> None:
        self._send(self._format(name, float(value), "g", tags))

    def timing(self, name: str, value_ms: float, tags: Tags | None = None) -> None:
        self._send(self._format(name, float(value_ms), "ms", tags))


# ── Multi (fan-out) ─────────────────────────────────────────────────────────


@dataclass
class MultiTelemetry:
    """Fan-out to multiple emitters; one failure never affects the others.

    The arc-recommended production setup is:

        MultiTelemetry([
            CloudWatchEMFTelemetry(),   # always-on AWS substrate
            DatadogTelemetry(),         # the lens humans look at
        ])
    """

    emitters: list[Telemetry] = field(default_factory=list)

    def _safe(self, fn_name: str, *args: Any, **kwargs: Any) -> None:
        for em in self.emitters:
            try:
                getattr(em, fn_name)(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001
                logger.debug("multi_telemetry_%s_failed err=%s", fn_name, exc)

    def count(self, name: str, value: float = 1.0, tags: Tags | None = None) -> None:
        self._safe("count", name, value, tags)

    def gauge(self, name: str, value: float, tags: Tags | None = None) -> None:
        self._safe("gauge", name, value, tags)

    def timing(self, name: str, value_ms: float, tags: Tags | None = None) -> None:
        self._safe("timing", name, value_ms, tags)


# ── Environment-driven factory ──────────────────────────────────────────────


def telemetry_from_env(env: Mapping[str, str] | None = None) -> Telemetry:
    """Build a Telemetry from environment variables.

    Env vars:
      ``ARC_TELEMETRY``             comma- or plus-separated list of:
                                    ``cloudwatch``, ``datadog``, ``noop``.
                                    Default: ``noop``.
      ``ARC_TELEMETRY_NAMESPACE``   metric namespace (default ``Arc``).
      ``DD_AGENT_HOST``             DogStatsD host (default 127.0.0.1).
      ``DD_DOGSTATSD_PORT``         DogStatsD port (default 8125).

    Examples:

        ARC_TELEMETRY=cloudwatch
        ARC_TELEMETRY=datadog
        ARC_TELEMETRY=cloudwatch+datadog
    """
    env = env or os.environ
    spec = (env.get("ARC_TELEMETRY") or "noop").lower().replace("+", ",")
    namespace = env.get("ARC_TELEMETRY_NAMESPACE", _DEFAULT_NAMESPACE)
    parts = [p.strip() for p in spec.split(",") if p.strip()]

    emitters: list[Telemetry] = []
    for p in parts:
        if p in ("noop", "none", "off", "disabled"):
            continue
        if p == "cloudwatch":
            emitters.append(CloudWatchEMFTelemetry(namespace=namespace))
        elif p == "datadog":
            host = env.get("DD_AGENT_HOST", "127.0.0.1")
            try:
                port = int(env.get("DD_DOGSTATSD_PORT", "8125"))
            except ValueError:
                port = 8125
            emitters.append(
                DatadogTelemetry(host=host, port=port, namespace=namespace.lower())
            )
        else:
            logger.debug("telemetry_unknown_target name=%s", p)

    if not emitters:
        return NoOpTelemetry()
    if len(emitters) == 1:
        return emitters[0]
    return MultiTelemetry(emitters=emitters)


__all__ = [
    "Telemetry",
    "Tags",
    "NoOpTelemetry",
    "CloudWatchEMFTelemetry",
    "DatadogTelemetry",
    "MultiTelemetry",
    "telemetry_from_env",
]
