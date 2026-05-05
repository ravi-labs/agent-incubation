"""Tests for arc.core.telemetry.

Three layers:

  1. Each concrete emitter on its own (NoOp, CloudWatch EMF, Datadog).
  2. Multi fan-out: one emitter failure does not stop the others.
  3. The env-driven factory builds the right composite.
"""

from __future__ import annotations

import io
import json

import pytest

from arc.core.telemetry import (
    CloudWatchEMFTelemetry,
    DatadogTelemetry,
    MultiTelemetry,
    NoOpTelemetry,
    Telemetry,
    telemetry_from_env,
)


# ── 1. NoOp ───────────────────────────────────────────────────────────────────


class TestNoOpTelemetry:
    def test_implements_protocol(self):
        t = NoOpTelemetry()
        assert isinstance(t, Telemetry)

    def test_returns_none_and_does_not_raise(self):
        t = NoOpTelemetry()
        assert t.count("x") is None
        assert t.gauge("x", 1.0) is None
        assert t.timing("x", 1.0) is None


# ── 2. CloudWatch EMF — JSON shape ────────────────────────────────────────────


class TestCloudWatchEMFTelemetry:
    def _last(self, buf: io.StringIO) -> dict:
        line = buf.getvalue().strip().splitlines()[-1]
        return json.loads(line)

    def test_count_emits_emf_envelope(self):
        buf = io.StringIO()
        t = CloudWatchEMFTelemetry(stream=buf)
        t.count("arc.effect.outcome", 1, tags={"agent_id": "a", "decision": "ALLOW"})

        payload = self._last(buf)
        # Structural envelope present
        assert "_aws" in payload
        cw = payload["_aws"]["CloudWatchMetrics"][0]
        assert cw["Namespace"] == "Arc"
        assert cw["Metrics"][0]["Name"] == "arc.effect.outcome"
        assert cw["Metrics"][0]["Unit"] == "Count"
        # Dimensions list contains the tag keys (one set)
        assert set(cw["Dimensions"][0]) == {"agent_id", "decision"}
        # Tag values land as top-level strings
        assert payload["agent_id"] == "a"
        assert payload["decision"] == "ALLOW"
        # Metric value at the metric name key
        assert payload["arc.effect.outcome"] == 1.0

    def test_timing_uses_milliseconds_unit(self):
        buf = io.StringIO()
        t = CloudWatchEMFTelemetry(stream=buf)
        t.timing("arc.effect.latency_ms", 42.5, tags={"agent_id": "a"})
        payload = self._last(buf)
        assert payload["_aws"]["CloudWatchMetrics"][0]["Metrics"][0]["Unit"] == "Milliseconds"
        assert payload["arc.effect.latency_ms"] == 42.5

    def test_gauge_uses_none_unit(self):
        buf = io.StringIO()
        t = CloudWatchEMFTelemetry(stream=buf)
        t.gauge("arc.queue.depth", 17, tags={"agent_id": "a"})
        payload = self._last(buf)
        assert payload["_aws"]["CloudWatchMetrics"][0]["Metrics"][0]["Unit"] == "None"
        assert payload["arc.queue.depth"] == 17.0

    def test_no_tags_emits_empty_dimensions(self):
        buf = io.StringIO()
        t = CloudWatchEMFTelemetry(stream=buf)
        t.count("arc.heartbeat")
        payload = self._last(buf)
        # CloudWatch requires Dimensions to be present; we use [[]] for "no dims"
        assert payload["_aws"]["CloudWatchMetrics"][0]["Dimensions"] == [[]]

    def test_custom_namespace(self):
        buf = io.StringIO()
        t = CloudWatchEMFTelemetry(stream=buf, namespace="MyOrg/Arc")
        t.count("x")
        payload = self._last(buf)
        assert payload["_aws"]["CloudWatchMetrics"][0]["Namespace"] == "MyOrg/Arc"

    def test_does_not_raise_on_broken_stream(self):
        class Broken:
            def write(self, _):
                raise OSError("disk full")

        t = CloudWatchEMFTelemetry(stream=Broken())
        # Must not raise — telemetry is best-effort.
        t.count("x", 1.0, tags={"a": "b"})


# ── 3. Datadog DogStatsD — wire format ────────────────────────────────────────


class _SpySocket:
    """Captures every UDP packet without actually sending."""

    def __init__(self):
        self.packets: list[tuple[bytes, tuple[str, int]]] = []
        self._closed = False

    def setblocking(self, _flag): pass

    def sendto(self, data, addr):
        if self._closed:
            raise OSError("closed")
        self.packets.append((data, addr))

    def close(self):
        self._closed = True


class TestDatadogTelemetry:
    def test_count_format(self, monkeypatch):
        spy = _SpySocket()
        t = DatadogTelemetry(host="127.0.0.1", port=8125, namespace="arc")
        t._sock = spy

        t.count("effect.outcome", 1.0, tags={"agent_id": "email-triage", "decision": "ALLOW"})

        assert len(spy.packets) == 1
        data, addr = spy.packets[0]
        assert addr == ("127.0.0.1", 8125)
        # arc.effect.outcome:1.0|c|#agent_id:email-triage,decision:ALLOW
        text = data.decode()
        assert text.startswith("arc.effect.outcome:1.0|c|#")
        assert "agent_id:email-triage" in text
        assert "decision:ALLOW" in text

    def test_timing_uses_ms_suffix(self):
        spy = _SpySocket()
        t = DatadogTelemetry(namespace="arc")
        t._sock = spy
        t.timing("effect.latency_ms", 17.0, tags={"agent_id": "a"})
        assert spy.packets[0][0].decode().startswith("arc.effect.latency_ms:17.0|ms|#")

    def test_gauge_uses_g_suffix(self):
        spy = _SpySocket()
        t = DatadogTelemetry(namespace="arc")
        t._sock = spy
        t.gauge("queue.depth", 5.0, tags={"agent_id": "a"})
        assert spy.packets[0][0].decode().startswith("arc.queue.depth:5.0|g|#")

    def test_constant_tags_merged_with_per_call_tags(self):
        spy = _SpySocket()
        t = DatadogTelemetry(namespace="arc", constant_tags={"env": "prod"})
        t._sock = spy
        t.count("x", 1.0, tags={"agent_id": "a"})
        text = spy.packets[0][0].decode()
        assert "env:prod" in text
        assert "agent_id:a" in text

    def test_no_tags_drops_hash(self):
        spy = _SpySocket()
        t = DatadogTelemetry(namespace="arc")
        t._sock = spy
        t.count("heartbeat", 1.0)
        text = spy.packets[0][0].decode()
        assert "|c" in text
        assert "#" not in text  # no tag suffix

    def test_send_failure_does_not_raise(self):
        class Refusing:
            def setblocking(self, _f): pass
            def sendto(self, *_): raise OSError("ECONNREFUSED")
        t = DatadogTelemetry(namespace="arc")
        t._sock = Refusing()
        # Telemetry is fire-and-forget; must not crash callers.
        t.count("x", 1.0, tags={"a": "b"})

    def test_no_socket_means_silent_drop(self):
        t = DatadogTelemetry(namespace="arc")
        t._sock = None  # simulate init failure
        # No exception — drops silently.
        t.count("x", 1.0)


# ── 4. Multi fan-out ──────────────────────────────────────────────────────────


class _RecordingTel:
    def __init__(self, name: str, fail: bool = False):
        self.name = name
        self.fail = fail
        self.calls: list[tuple] = []

    def count(self, name, value=1.0, tags=None):
        if self.fail: raise RuntimeError("boom")
        self.calls.append(("count", name, value, dict(tags or {})))

    def gauge(self, name, value, tags=None):
        if self.fail: raise RuntimeError("boom")
        self.calls.append(("gauge", name, value, dict(tags or {})))

    def timing(self, name, value_ms, tags=None):
        if self.fail: raise RuntimeError("boom")
        self.calls.append(("timing", name, value_ms, dict(tags or {})))


class TestMultiTelemetry:
    def test_fans_out_to_all(self):
        a, b = _RecordingTel("a"), _RecordingTel("b")
        m = MultiTelemetry([a, b])
        m.count("x", 1.0, tags={"k": "v"})
        m.timing("y", 2.5)
        assert ("count",  "x", 1.0, {"k": "v"}) in a.calls
        assert ("count",  "x", 1.0, {"k": "v"}) in b.calls
        assert ("timing", "y", 2.5, {})         in a.calls

    def test_one_failure_does_not_stop_the_other(self):
        broken, ok = _RecordingTel("broken", fail=True), _RecordingTel("ok")
        m = MultiTelemetry([broken, ok])
        m.count("x", 1.0)         # broken raises; ok still records
        assert ok.calls == [("count", "x", 1.0, {})]


# ── 5. Env factory ────────────────────────────────────────────────────────────


class TestTelemetryFromEnv:
    def test_default_is_noop(self):
        t = telemetry_from_env(env={})
        assert isinstance(t, NoOpTelemetry)

    def test_explicit_off(self):
        for spec in ("noop", "off", "disabled", "none"):
            t = telemetry_from_env(env={"ARC_TELEMETRY": spec})
            assert isinstance(t, NoOpTelemetry), f"failed for {spec!r}"

    def test_cloudwatch_only(self):
        t = telemetry_from_env(env={"ARC_TELEMETRY": "cloudwatch"})
        assert isinstance(t, CloudWatchEMFTelemetry)

    def test_datadog_only(self):
        t = telemetry_from_env(env={
            "ARC_TELEMETRY":     "datadog",
            "DD_AGENT_HOST":     "10.0.0.5",
            "DD_DOGSTATSD_PORT": "9125",
        })
        assert isinstance(t, DatadogTelemetry)
        assert t.host == "10.0.0.5"
        assert t.port == 9125

    def test_both_returns_multi(self):
        for spec in ("cloudwatch+datadog", "cloudwatch,datadog"):
            t = telemetry_from_env(env={"ARC_TELEMETRY": spec})
            assert isinstance(t, MultiTelemetry)
            assert len(t.emitters) == 2

    def test_unknown_target_falls_back_to_noop(self):
        t = telemetry_from_env(env={"ARC_TELEMETRY": "splunk"})
        assert isinstance(t, NoOpTelemetry)

    def test_namespace_override(self):
        t = telemetry_from_env(env={
            "ARC_TELEMETRY":           "cloudwatch",
            "ARC_TELEMETRY_NAMESPACE": "MyOrg/Arc",
        })
        assert isinstance(t, CloudWatchEMFTelemetry)
        assert t.namespace == "MyOrg/Arc"
