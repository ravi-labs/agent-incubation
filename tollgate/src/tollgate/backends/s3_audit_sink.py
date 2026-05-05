"""S3-backed audit sink — durable storage for the compliance record.

Why this exists
---------------
``JsonlAuditSink`` writes to a local file. In Lambda that's ``/tmp/``,
which is wiped on cold-start: every run's audit rows live for as long
as the container's warm-pool slot, then disappear. That's fine for
local dev and bad for production.

This sink buffers events in memory and flushes them to S3 in batches.
One S3 object per ``flush()`` call, named so a year of audit data
remains queryable from S3 Select / Athena without listing the whole
prefix tree:

    s3://<bucket>/<prefix>/agent_id=<id>/year=YYYY/month=MM/day=DD/<run_id>-<seq>.jsonl

The Lambda runtime should call ``flush()`` at the end of each invocation
(``arc.runtime.deploy.lambda_handler`` already wires this in). Long-lived
processes (ECS Fargate) can rely on the in-memory threshold or call
``flush()`` periodically.

Design rules
------------
- **Best-effort buffer drain.** A failed flush logs at ERROR but never
  raises into the agent path. Audit rows are dropped only as a last
  resort and only after retry; production setups should pair this with
  S3 versioning + a dead-letter SQS queue for unrecoverable batches.
- **No streaming.** S3 multipart upload would be technically nicer but
  adds complexity; one flush = one PUT keeps the failure surface small.
- **KMS-aware.** Pass ``sse_kms_key_id`` and the sink uses
  ``aws:kms`` server-side encryption. Default is ``AES256`` SSE.
- **Threshold-driven flush.** Every ``flush_every`` events trigger a
  PUT automatically. Default 100 — small enough to bound memory, large
  enough to avoid per-event PUT cost.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ..types import AuditEvent

logger = logging.getLogger("tollgate.backends.s3_audit_sink")

_DEFAULT_FLUSH_EVERY = 100   # events per S3 object
_DEFAULT_PREFIX      = "audit"


@dataclass
class S3AuditSink:
    """Audit sink that batches rows and PUTs them to S3.

    Args:
        bucket:         Target S3 bucket name (must exist).
        prefix:         Key prefix under which to write (default ``audit``).
                        Subkeys are partitioned by agent_id + date for
                        Athena-friendly queries.
        region:         AWS region for the boto3 client. Defaults to
                        ``AWS_REGION`` / ``AWS_DEFAULT_REGION`` / boto3
                        session default.
        sse_kms_key_id: Optional KMS key ID/ARN for server-side
                        encryption. When set, objects are written with
                        ``ServerSideEncryption=aws:kms``. Otherwise the
                        bucket's default SSE applies (typically AES256).
        flush_every:    Number of buffered events that triggers an
                        automatic flush. Default 100. Set lower for
                        tight latency, higher for throughput.
        run_id:         Optional run/session identifier woven into
                        object names. Defaults to a per-process UUID
                        (good enough for Lambda — one run per
                        invocation).
        client:         Optional pre-built boto3 S3 client. Mostly for
                        tests; production code should let the sink
                        construct one lazily from the region.
    """

    bucket:         str
    prefix:         str  = _DEFAULT_PREFIX
    region:         str | None = None
    sse_kms_key_id: str | None = None
    flush_every:    int  = _DEFAULT_FLUSH_EVERY
    run_id:         str  = field(default_factory=lambda: uuid.uuid4().hex[:12])
    client:         Any  = None

    _buf:           list[dict[str, Any]] = field(default_factory=list, init=False, repr=False)
    _seq:           int                  = field(default=0, init=False, repr=False)
    _lock:          Any                  = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self._lock = threading.Lock()
        if not self.bucket:
            raise ValueError("S3AuditSink requires a non-empty bucket name.")
        if self.flush_every <= 0:
            raise ValueError(
                f"flush_every must be > 0; got {self.flush_every}"
            )

    # ── boto3 client (lazy) ────────────────────────────────────────────

    def _get_client(self) -> Any:
        if self.client is not None:
            return self.client
        try:
            import boto3
        except ImportError as exc:
            raise ImportError(
                "boto3 is required for S3AuditSink. "
                "Install with: pip install 'tollgate[aws]' or "
                "pip install boto3"
            ) from exc
        kwargs: dict[str, Any] = {}
        region = (
            self.region
            or os.environ.get("AWS_REGION")
            or os.environ.get("AWS_DEFAULT_REGION")
        )
        if region:
            kwargs["region_name"] = region
        self.client = boto3.client("s3", **kwargs)
        return self.client

    # ── AuditSink protocol ─────────────────────────────────────────────

    def emit(self, event: AuditEvent) -> None:
        """Append an event to the in-memory buffer; flush on threshold.

        Always synchronous + lock-protected. Never raises into the
        agent path; on serialisation error the event is dropped after
        a debug log. On flush failure the buffer is preserved so the
        next flush can retry.
        """
        try:
            row = event.to_dict()
        except Exception as exc:  # noqa: BLE001
            logger.debug("s3_audit_serialise_failed err=%s", exc)
            return

        with self._lock:
            self._buf.append(row)
            should_flush = len(self._buf) >= self.flush_every

        if should_flush:
            try:
                self.flush()
            except Exception as exc:  # noqa: BLE001
                logger.error("s3_audit_threshold_flush_failed err=%s", exc)

    # ── Manual flush ───────────────────────────────────────────────────

    def flush(self) -> int:
        """Flush the buffer to S3. Returns the number of events written.

        Raises:
            Any boto3 exception from ``put_object`` after the buffer has
            been preserved for retry. Callers (e.g. Lambda runtime) can
            decide whether to surface the failure or swallow it.
        """
        with self._lock:
            if not self._buf:
                return 0
            batch = self._buf
            self._buf = []
            self._seq += 1
            seq = self._seq

        # Build the JSONL body. Outside the lock — serialisation can be
        # slow for large batches.
        body = "\n".join(json.dumps(r, ensure_ascii=False) for r in batch) + "\n"

        # Partition by the *first* event's agent_id so a batch is never
        # split across prefixes. In practice Lambda invocations hit one
        # agent per run; the partitioning is for Athena efficiency.
        first = batch[0]
        agent_id = (
            (first.get("agent") or {}).get("agent_id")
            or first.get("agent_id")
            or "unknown"
        )
        ts = datetime.now(timezone.utc)
        key = (
            f"{self.prefix.rstrip('/')}/agent_id={agent_id}/"
            f"year={ts.year:04d}/month={ts.month:02d}/day={ts.day:02d}/"
            f"{self.run_id}-{seq:06d}.jsonl"
        )

        put_kwargs: dict[str, Any] = {
            "Bucket": self.bucket,
            "Key":    key,
            "Body":   body.encode("utf-8"),
            "ContentType": "application/x-ndjson",
        }
        if self.sse_kms_key_id:
            put_kwargs["ServerSideEncryption"] = "aws:kms"
            put_kwargs["SSEKMSKeyId"]          = self.sse_kms_key_id

        try:
            self._get_client().put_object(**put_kwargs)
        except Exception:
            # Restore buffer for the next attempt — audit rows are too
            # important to drop on a transient network error.
            with self._lock:
                self._buf = batch + self._buf
                self._seq -= 1
            raise

        logger.debug(
            "s3_audit_flushed bucket=%s key=%s rows=%d",
            self.bucket, key, len(batch),
        )
        return len(batch)

    # ── Lifecycle ──────────────────────────────────────────────────────

    def close(self) -> None:
        """Final flush. Swallows errors — close() must not raise."""
        try:
            self.flush()
        except Exception as exc:  # noqa: BLE001
            logger.error("s3_audit_close_flush_failed err=%s", exc)

    def __enter__(self) -> "S3AuditSink":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()


__all__ = ["S3AuditSink"]
