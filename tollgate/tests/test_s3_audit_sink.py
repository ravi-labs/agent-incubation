"""Tests for tollgate.backends.s3_audit_sink.

Uses a hand-rolled S3 stub instead of moto. The sink only calls
``put_object``; the stub captures every call so we can assert on:

  * key naming (partitioned by agent_id + date)
  * body shape (JSONL, one row per event)
  * SSE-KMS headers when configured
  * batch boundaries from the flush_every threshold
  * preservation of the buffer on PUT failure (no audit row dropped
    until after the retry path)
  * close() swallowing flush errors (so __exit__ never raises)
"""

from __future__ import annotations

from typing import Any

import pytest

from tollgate.backends.s3_audit_sink import S3AuditSink


# ── Stub S3 client ─────────────────────────────────────────────────────────────


class StubS3:
    """Captures put_object calls; can be configured to fail N times then succeed."""

    def __init__(self, fail_first: int = 0):
        self.calls: list[dict[str, Any]] = []
        self.fail_first = fail_first
        self._failed = 0

    def put_object(self, **kwargs):
        if self._failed < self.fail_first:
            self._failed += 1
            raise RuntimeError(f"simulated PUT failure #{self._failed}")
        self.calls.append(kwargs)
        return {"ETag": '"deadbeef"'}


# ── AuditEvent stand-in ────────────────────────────────────────────────────────


class FakeEvent:
    """Implements the protocol the sink needs: ``.to_dict()``."""

    def __init__(self, agent_id: str = "email-triage", **extra):
        self._d = {
            "agent": {"agent_id": agent_id, "version": "0.1.0"},
            "decision": "ALLOW",
            **extra,
        }

    def to_dict(self):
        return self._d


# ── 1. Construction ────────────────────────────────────────────────────────────


class TestConstruction:
    def test_empty_bucket_raises(self):
        with pytest.raises(ValueError, match="non-empty bucket"):
            S3AuditSink(bucket="")

    def test_non_positive_flush_every_raises(self):
        with pytest.raises(ValueError, match="flush_every"):
            S3AuditSink(bucket="x", flush_every=0)
        with pytest.raises(ValueError, match="flush_every"):
            S3AuditSink(bucket="x", flush_every=-1)

    def test_run_id_defaults_to_unique_value(self):
        a = S3AuditSink(bucket="x", client=StubS3())
        b = S3AuditSink(bucket="x", client=StubS3())
        assert a.run_id != b.run_id


# ── 2. Buffer + flush ──────────────────────────────────────────────────────────


class TestFlushBehaviour:
    def test_emit_below_threshold_does_not_flush(self):
        s3 = StubS3()
        sink = S3AuditSink(bucket="b", flush_every=10, client=s3)
        for _ in range(5):
            sink.emit(FakeEvent())
        assert s3.calls == []
        assert len(sink._buf) == 5

    def test_emit_at_threshold_flushes(self):
        s3 = StubS3()
        sink = S3AuditSink(bucket="b", flush_every=3, client=s3)
        for _ in range(3):
            sink.emit(FakeEvent())
        assert len(s3.calls) == 1
        # Body is JSONL with one row per event + trailing newline.
        body = s3.calls[0]["Body"].decode()
        assert body.count("\n") == 3

    def test_manual_flush_returns_event_count(self):
        s3 = StubS3()
        sink = S3AuditSink(bucket="b", flush_every=100, client=s3)
        sink.emit(FakeEvent()); sink.emit(FakeEvent())
        assert sink.flush() == 2
        assert sink.flush() == 0  # buffer drained

    def test_flush_empty_buffer_is_noop(self):
        s3 = StubS3()
        sink = S3AuditSink(bucket="b", client=s3)
        assert sink.flush() == 0
        assert s3.calls == []


# ── 3. Key naming + body shape ────────────────────────────────────────────────


class TestKeyShape:
    def test_key_partitioned_by_agent_and_date(self):
        s3 = StubS3()
        sink = S3AuditSink(
            bucket="my-audit-bucket",
            prefix="audit",
            flush_every=1,
            run_id="abc123",
            client=s3,
        )
        sink.emit(FakeEvent(agent_id="email-triage"))

        key = s3.calls[0]["Key"]
        # audit/agent_id=email-triage/year=YYYY/month=MM/day=DD/abc123-000001.jsonl
        assert key.startswith("audit/agent_id=email-triage/year=")
        assert "/month=" in key
        assert "/day=" in key
        assert key.endswith("/abc123-000001.jsonl")

    def test_unknown_agent_when_no_agent_id(self):
        class Eventless:
            def to_dict(self): return {"some": "row"}

        s3 = StubS3()
        sink = S3AuditSink(bucket="b", flush_every=1, run_id="r", client=s3)
        sink.emit(Eventless())
        assert "agent_id=unknown/" in s3.calls[0]["Key"]

    def test_sequence_number_increments_across_flushes(self):
        s3 = StubS3()
        sink = S3AuditSink(bucket="b", flush_every=1, run_id="r", client=s3)
        sink.emit(FakeEvent()); sink.emit(FakeEvent()); sink.emit(FakeEvent())
        keys = [c["Key"] for c in s3.calls]
        assert any(k.endswith("000001.jsonl") for k in keys)
        assert any(k.endswith("000002.jsonl") for k in keys)
        assert any(k.endswith("000003.jsonl") for k in keys)

    def test_body_is_ndjson_one_row_per_line(self):
        s3 = StubS3()
        sink = S3AuditSink(bucket="b", flush_every=2, client=s3)
        sink.emit(FakeEvent(agent_id="a"))
        sink.emit(FakeEvent(agent_id="a"))

        body = s3.calls[0]["Body"].decode()
        lines = [l for l in body.split("\n") if l]
        assert len(lines) == 2
        # Each line is valid JSON
        import json
        for l in lines:
            json.loads(l)
        assert s3.calls[0]["ContentType"] == "application/x-ndjson"


# ── 4. KMS encryption ──────────────────────────────────────────────────────────


class TestKMS:
    def test_no_kms_means_no_sse_headers(self):
        s3 = StubS3()
        sink = S3AuditSink(bucket="b", flush_every=1, client=s3)
        sink.emit(FakeEvent())
        assert "ServerSideEncryption" not in s3.calls[0]
        assert "SSEKMSKeyId"          not in s3.calls[0]

    def test_kms_key_adds_sse_headers(self):
        s3 = StubS3()
        sink = S3AuditSink(
            bucket="b",
            flush_every=1,
            sse_kms_key_id="arn:aws:kms:us-east-1:111:key/abc",
            client=s3,
        )
        sink.emit(FakeEvent())
        assert s3.calls[0]["ServerSideEncryption"] == "aws:kms"
        assert s3.calls[0]["SSEKMSKeyId"]           == "arn:aws:kms:us-east-1:111:key/abc"


# ── 5. Failure preservation ────────────────────────────────────────────────────


class TestFailureHandling:
    def test_put_failure_preserves_buffer_for_retry(self):
        s3 = StubS3(fail_first=1)
        sink = S3AuditSink(bucket="b", flush_every=100, client=s3)
        sink.emit(FakeEvent()); sink.emit(FakeEvent())

        # First flush raises — but the buffer is still there.
        with pytest.raises(RuntimeError, match="simulated PUT failure"):
            sink.flush()
        assert len(sink._buf) == 2

        # Second flush succeeds; buffer drains.
        assert sink.flush() == 2
        assert sink._buf == []

    def test_emit_threshold_flush_failure_does_not_raise(self):
        """Threshold-triggered flush failures are logged but don't crash emit()."""
        s3 = StubS3(fail_first=10)  # all flushes fail
        sink = S3AuditSink(bucket="b", flush_every=2, client=s3)
        # No exception even though every flush attempt fails.
        for _ in range(5):
            sink.emit(FakeEvent())
        # Buffer holds everything because nothing succeeded.
        assert len(sink._buf) == 5

    def test_close_swallows_flush_errors(self):
        s3 = StubS3(fail_first=10)
        sink = S3AuditSink(bucket="b", client=s3)
        sink.emit(FakeEvent())
        # __exit__ must never raise.
        sink.close()

    def test_serialisation_failure_drops_event_does_not_raise(self):
        class BrokenEvent:
            def to_dict(self): raise ValueError("can't serialise")

        s3 = StubS3()
        sink = S3AuditSink(bucket="b", flush_every=1, client=s3)
        # Must not raise.
        sink.emit(BrokenEvent())
        # Nothing was buffered or flushed.
        assert sink._buf == []
        assert s3.calls == []


# ── 6. Context manager ─────────────────────────────────────────────────────────


class TestContextManager:
    def test_with_block_flushes_on_exit(self):
        s3 = StubS3()
        with S3AuditSink(bucket="b", flush_every=100, client=s3) as sink:
            sink.emit(FakeEvent())
            sink.emit(FakeEvent())
            assert s3.calls == []  # not yet flushed
        # Exiting the block flushes.
        assert len(s3.calls) == 1
        assert s3.calls[0]["Body"].decode().count("\n") == 2
