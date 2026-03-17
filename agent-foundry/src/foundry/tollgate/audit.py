import json
import logging
import threading
from pathlib import Path
from typing import Protocol
from urllib.request import Request, urlopen

from .types import AuditEvent, Outcome


class AuditSink(Protocol):
    """Protocol for auditing tool execution results."""

    def emit(self, event: AuditEvent) -> None:
        """Emit an audit event."""
        ...


class JsonlAuditSink:
    """Audit sink that writes to a JSONL file with buffering."""

    def __init__(self, log_path: str | Path):
        """Initialize the sink and ensure the log directory exists."""
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._f = None

    def _get_file(self):
        if self._f is None or self._f.closed:
            self._f = self.log_path.open("a", encoding="utf-8", buffering=1)
        return self._f

    def emit(self, event: AuditEvent) -> None:
        """Append an audit event to the JSONL file."""
        f = self._get_file()
        f.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")

    def close(self):
        """Close the file handle."""
        if self._f and not self._f.closed:
            self._f.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def __del__(self):
        self.close()


class CompositeAuditSink:
    """Chains multiple AuditSink implementations together.

    Example:
        sink = CompositeAuditSink([
            JsonlAuditSink("audit.jsonl"),
            WebhookAuditSink("https://hooks.slack.com/..."),
        ])
    """

    def __init__(self, sinks: list[AuditSink]):
        if not sinks:
            raise ValueError("CompositeAuditSink requires at least one sink.")
        self._sinks = list(sinks)

    def emit(self, event: AuditEvent) -> None:
        """Emit an event to all registered sinks."""
        for sink in self._sinks:
            try:
                sink.emit(event)
            except Exception:
                # Never let one sink failure block others
                logging.getLogger("tollgate.audit").exception(
                    "AuditSink failed: %s", type(sink).__name__
                )


class WebhookAuditSink:
    """Fire-and-forget HTTP POST on security-relevant audit events.

    By default, alerts are sent for BLOCKED, APPROVAL_DENIED, FAILED,
    and TIMEOUT outcomes. Customise via ``alert_outcomes``.

    The webhook payload is the full AuditEvent dict as JSON. Requests are
    dispatched on a daemon thread so they never block the execution path.
    """

    # Outcomes that trigger a webhook by default
    DEFAULT_ALERT_OUTCOMES = frozenset(
        {Outcome.BLOCKED, Outcome.APPROVAL_DENIED, Outcome.FAILED, Outcome.TIMEOUT}
    )

    def __init__(
        self,
        webhook_url: str,
        *,
        alert_outcomes: frozenset[Outcome] | None = None,
        timeout_seconds: float = 5.0,
        headers: dict[str, str] | None = None,
    ):
        if not webhook_url:
            raise ValueError("webhook_url must not be empty.")
        self.webhook_url = webhook_url
        self.alert_outcomes = alert_outcomes or self.DEFAULT_ALERT_OUTCOMES
        self.timeout_seconds = timeout_seconds
        self._headers = {"Content-Type": "application/json"}
        if headers:
            self._headers.update(headers)
        self._logger = logging.getLogger("tollgate.audit.webhook")

    def emit(self, event: AuditEvent) -> None:
        """Send a webhook if the event outcome warrants an alert."""
        if event.outcome not in self.alert_outcomes:
            return  # Not an alertable event — skip silently

        # Fire-and-forget on a daemon thread to avoid blocking execution
        thread = threading.Thread(target=self._send, args=(event,), daemon=True)
        thread.start()

    def _send(self, event: AuditEvent) -> None:
        """Synchronous HTTP POST (runs on background thread)."""
        try:
            body = json.dumps(event.to_dict(), ensure_ascii=False).encode("utf-8")
            req = Request(
                self.webhook_url,
                data=body,
                headers=self._headers,
                method="POST",
            )
            urlopen(req, timeout=self.timeout_seconds)  # noqa: S310
        except Exception:
            self._logger.exception("Webhook delivery failed: %s", self.webhook_url)
