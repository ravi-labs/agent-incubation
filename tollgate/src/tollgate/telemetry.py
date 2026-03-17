import logging
import os
import threading

from .types import AuditEvent

# Scarf handles anonymous telemetry for open source projects.
# This URL points to the Tollgate package analytics on Scarf.
SCARF_PIXEL_URL = (
    "https://static.scarf.sh/a.png?x-pxid=7f4b3b2c-8d1e-4f5a-9b6c-2d3e4f5a6b7c"
)


class TelemetryAuditSink:
    """Anonymous adoption metrics for Tollgate.

    Collects ONLY high-level usage aggregates:
    - Outcome (EXECUTED, BLOCKED, etc.)
    - Tool name (anonymized/truncated)
    - Tollgate version

    No parameters, no agent IDs, and no PII are ever collected.
    Users can opt-out by setting TOLLGATE_TELEMETRY=0.
    """

    def __init__(self, enabled: bool | None = None):
        # Opt-out via environment variable
        self.enabled = enabled
        if self.enabled is None:
            self.enabled = os.environ.get("TOLLGATE_TELEMETRY", "1") != "0"

        self._logger = logging.getLogger("tollgate.telemetry")
        self._version = "unknown"
        try:
            from . import __version__

            self._version = __version__
        except ImportError:
            pass

    def emit(self, event: AuditEvent) -> None:
        if not self.enabled:
            return

        # Fire-and-forget on a background thread
        thread = threading.Thread(target=self._send, args=(event,), daemon=True)
        thread.start()

    def _send(self, event: AuditEvent) -> None:
        """Send anonymous hit to Scarf."""
        # Data minimization: we only care about adoption and basic success rates
        # This is a no-op placeholder for now - actual telemetry would hit
        # a Scarf pixel endpoint with minimal metrics
        _ = {
            "v": self._version,
            "o": event.outcome.value,
            "e": (
                event.tool_request.effect.value
                if event.tool_request.effect
                else "unknown"
            ),
            "f": (
                event.tool_request.metadata.get("_framework", "custom")
                if event.tool_request.metadata
                else "custom"
            ),
        }
        # Telemetry failure should NEVER affect the user - this is a no-op
