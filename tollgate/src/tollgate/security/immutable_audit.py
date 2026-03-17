"""Immutable audit logging with cryptographic hash chaining.

Provides tamper-evident audit logs using HMAC-SHA256 chain linking.
Each audit event includes the hash of the previous event, creating
an immutable chain that can be verified for integrity.

Usage:

    from tollgate.security import ImmutableAuditSink

    signing_key = os.urandom(32)
    jsonl_sink = JsonlAuditSink("audit.jsonl")
    immutable_sink = ImmutableAuditSink(jsonl_sink, signing_key)

    # Later, verify the chain
    events = load_events_from_file("audit.jsonl")
    is_valid = ImmutableAuditSink.verify_chain(events, signing_key)
"""

import hashlib
import hmac
import json
import threading
from dataclasses import dataclass
from typing import Any

from ..audit import AuditSink
from ..types import AuditEvent


@dataclass
class ChainedAuditEvent:
    """An audit event with chain linking metadata."""

    event: dict[str, Any]
    prev_hash: str  # Hash of previous event (hex)
    event_hash: str  # Hash of this event including prev_hash (hex)
    signature: str  # HMAC signature of event_hash (hex)
    sequence: int  # Sequence number in chain

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.event,
            "_chain": {
                "prev_hash": self.prev_hash,
                "event_hash": self.event_hash,
                "signature": self.signature,
                "sequence": self.sequence,
            },
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ChainedAuditEvent":
        chain = data.pop("_chain", {})
        return cls(
            event=data,
            prev_hash=chain.get("prev_hash", ""),
            event_hash=chain.get("event_hash", ""),
            signature=chain.get("signature", ""),
            sequence=chain.get("sequence", 0),
        )


class ImmutableAuditSink:
    """Audit sink that creates tamper-evident logs using hash chaining.

    Each event is linked to the previous event via cryptographic hashes,
    creating a blockchain-like structure. Any modification to the chain
    can be detected during verification.

    Args:
        inner: The inner AuditSink to emit chained events to.
        signing_key: 32-byte key for HMAC-SHA256 signatures.
        initial_hash: Optional initial hash for the first event.
            Defaults to zeros.
    """

    GENESIS_HASH = "0" * 64  # SHA-256 produces 64 hex chars

    def __init__(
        self,
        inner: AuditSink,
        signing_key: bytes,
        initial_hash: str | None = None,
    ):
        if len(signing_key) < 32:
            raise ValueError("Signing key must be at least 32 bytes")

        self._inner = inner
        self._signing_key = signing_key
        self._prev_hash = initial_hash or self.GENESIS_HASH
        self._sequence = 0
        self._lock = threading.Lock()

    def emit(self, event: AuditEvent) -> None:
        """Emit an audit event with chain linking."""
        with self._lock:
            event_dict = event.to_dict()

            # Compute hash of this event including previous hash
            event_hash = self._compute_event_hash(event_dict, self._prev_hash)

            # Sign the event hash
            signature = self._sign(event_hash)

            # Create chained event
            chained = ChainedAuditEvent(
                event=event_dict,
                prev_hash=self._prev_hash,
                event_hash=event_hash,
                signature=signature,
                sequence=self._sequence,
            )

            # Emit to inner sink
            self._inner.emit(_ChainedEventWrapper(chained))

            # Update state for next event
            self._prev_hash = event_hash
            self._sequence += 1

    def _compute_event_hash(self, event: dict[str, Any], prev_hash: str) -> str:
        """Compute SHA-256 hash of event including previous hash."""
        # Create deterministic JSON representation
        canonical = json.dumps(event, sort_keys=True, ensure_ascii=True)
        data = f"{prev_hash}:{canonical}"
        return hashlib.sha256(data.encode("utf-8")).hexdigest()

    def _sign(self, event_hash: str) -> str:
        """Sign an event hash with HMAC-SHA256."""
        return hmac.new(
            self._signing_key,
            event_hash.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    @classmethod
    def verify_chain(
        cls,
        events: list[dict[str, Any]],
        signing_key: bytes,
    ) -> tuple[bool, str | None]:
        """Verify the integrity of a chain of audit events.

        Args:
            events: List of event dicts with _chain metadata.
            signing_key: The key used to sign the events.

        Returns:
            (is_valid, error_message)
            - is_valid: True if chain is intact and signatures are valid.
            - error_message: Description of the first error found, or None.
        """
        if not events:
            return True, None

        expected_prev_hash = cls.GENESIS_HASH

        for expected_sequence, event_data in enumerate(events):
            # Extract chain metadata
            chain = event_data.get("_chain", {})
            if not chain:
                return False, f"Event {expected_sequence}: Missing _chain metadata"

            prev_hash = chain.get("prev_hash", "")
            event_hash = chain.get("event_hash", "")
            signature = chain.get("signature", "")
            sequence = chain.get("sequence", -1)

            # Check sequence
            if sequence != expected_sequence:
                return (
                    False,
                    f"Event {expected_sequence}: Sequence mismatch, got {sequence}",
                )

            # Check prev_hash links correctly
            if prev_hash != expected_prev_hash:
                return (
                    False,
                    f"Event {expected_sequence}: Chain broken: prev_hash doesn't match",
                )

            # Recompute event hash
            event_only = {k: v for k, v in event_data.items() if k != "_chain"}
            computed_hash = cls._compute_event_hash_static(event_only, prev_hash)

            if computed_hash != event_hash:
                return (
                    False,
                    f"Event {expected_sequence}: Event hash mismatch (may be tampered)",
                )

            # Verify signature
            expected_sig = hmac.new(
                signing_key,
                event_hash.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()

            if not hmac.compare_digest(signature, expected_sig):
                return (
                    False,
                    f"Event {expected_sequence}: Invalid signature",
                )

            # Update expected prev_hash for next iteration
            expected_prev_hash = event_hash

        return True, None

    @staticmethod
    def _compute_event_hash_static(event: dict[str, Any], prev_hash: str) -> str:
        """Static version of _compute_event_hash for verification."""
        canonical = json.dumps(event, sort_keys=True, ensure_ascii=True)
        data = f"{prev_hash}:{canonical}"
        return hashlib.sha256(data.encode("utf-8")).hexdigest()

    @property
    def current_hash(self) -> str:
        """Get the current chain head hash."""
        with self._lock:
            return self._prev_hash

    @property
    def sequence(self) -> int:
        """Get the current sequence number."""
        with self._lock:
            return self._sequence


class _ChainedEventWrapper:
    """Wrapper to pass ChainedAuditEvent through AuditSink."""

    def __init__(self, chained: ChainedAuditEvent):
        self._chained = chained

    def to_dict(self) -> dict[str, Any]:
        return self._chained.to_dict()
