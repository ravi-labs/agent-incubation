"""Field-level encryption for Tollgate.

Provides AES-256-GCM encryption for sensitive data in audit logs and stores.

Requires: pip install tollgate[encryption] (cryptography>=41.0.0)

Usage:

    from tollgate.security import FieldEncryptor, EncryptedAuditSink

    # Create encryptor with 256-bit key
    key = os.urandom(32)
    encryptor = FieldEncryptor(key, key_id="key-2024-01")

    # Encrypt individual values
    encrypted = encryptor.encrypt("sensitive data")
    plaintext = encryptor.decrypt(encrypted)

    # Wrap an audit sink to encrypt sensitive fields
    jsonl_sink = JsonlAuditSink("audit.jsonl")
    encrypted_sink = EncryptedAuditSink(jsonl_sink, encryptor)
"""

import base64
import json
import os
from dataclasses import dataclass
from typing import Any

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError as err:
    raise ImportError(
        "Encryption requires the 'cryptography' package. "
        "Install it with: pip install tollgate[encryption]"
    ) from err

from ..audit import AuditSink
from ..types import AuditEvent


@dataclass(frozen=True)
class EncryptedValue:
    """An encrypted value with metadata for decryption."""

    ciphertext: str  # Base64-encoded ciphertext + tag
    nonce: str  # Base64-encoded nonce (12 bytes)
    key_id: str  # Key identifier for key rotation

    def to_dict(self) -> dict[str, str]:
        return {
            "_encrypted": True,
            "ciphertext": self.ciphertext,
            "nonce": self.nonce,
            "key_id": self.key_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EncryptedValue":
        return cls(
            ciphertext=data["ciphertext"],
            nonce=data["nonce"],
            key_id=data["key_id"],
        )

    @classmethod
    def is_encrypted(cls, data: Any) -> bool:
        """Check if a value is an encrypted value dict."""
        return isinstance(data, dict) and data.get("_encrypted") is True


class FieldEncryptor:
    """AES-256-GCM field-level encryption.

    Provides authenticated encryption with associated data (AEAD).
    Each encryption uses a unique nonce for semantic security.

    Args:
        key: 256-bit (32 bytes) encryption key.
        key_id: Identifier for this key (for key rotation tracking).
    """

    def __init__(self, key: bytes, key_id: str = "default"):
        if len(key) != 32:
            raise ValueError("Key must be 32 bytes (256 bits)")
        self._aesgcm = AESGCM(key)
        self._key_id = key_id

    @property
    def key_id(self) -> str:
        return self._key_id

    def encrypt(self, plaintext: str) -> EncryptedValue:
        """Encrypt a string value.

        Args:
            plaintext: The string to encrypt.

        Returns:
            EncryptedValue containing ciphertext, nonce, and key_id.
        """
        nonce = os.urandom(12)  # 96-bit nonce for GCM
        ciphertext = self._aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)

        return EncryptedValue(
            ciphertext=base64.b64encode(ciphertext).decode("ascii"),
            nonce=base64.b64encode(nonce).decode("ascii"),
            key_id=self._key_id,
        )

    def decrypt(self, encrypted: EncryptedValue) -> str:
        """Decrypt an encrypted value.

        Args:
            encrypted: The EncryptedValue to decrypt.

        Returns:
            The decrypted string.

        Raises:
            ValueError: If decryption fails (wrong key or tampered data).
        """
        if encrypted.key_id != self._key_id:
            raise ValueError(
                f"Key ID mismatch: expected '{self._key_id}', "
                f"got '{encrypted.key_id}'"
            )

        nonce = base64.b64decode(encrypted.nonce)
        ciphertext = base64.b64decode(encrypted.ciphertext)

        try:
            plaintext = self._aesgcm.decrypt(nonce, ciphertext, None)
            return plaintext.decode("utf-8")
        except Exception as e:
            raise ValueError(f"Decryption failed: {e}") from e

    def encrypt_dict(self, data: dict[str, Any]) -> dict[str, Any]:
        """Encrypt a dictionary by serializing to JSON first."""
        json_str = json.dumps(data, ensure_ascii=False)
        encrypted = self.encrypt(json_str)
        return encrypted.to_dict()

    def decrypt_dict(self, data: dict[str, Any]) -> dict[str, Any]:
        """Decrypt a dictionary that was encrypted with encrypt_dict."""
        encrypted = EncryptedValue.from_dict(data)
        json_str = self.decrypt(encrypted)
        return json.loads(json_str)


class EncryptedAuditSink:
    """AuditSink wrapper that encrypts sensitive fields before emission.

    Wraps another audit sink and encrypts specified sensitive fields
    in audit events before passing them to the inner sink.

    Args:
        inner: The inner AuditSink to emit to.
        encryptor: The FieldEncryptor to use.
        sensitive_fields: Set of field names to encrypt.
            Defaults to {"params", "result_summary"}.
    """

    DEFAULT_SENSITIVE_FIELDS = frozenset({"params", "result_summary"})

    def __init__(
        self,
        inner: AuditSink,
        encryptor: FieldEncryptor,
        sensitive_fields: frozenset[str] | None = None,
    ):
        self._inner = inner
        self._encryptor = encryptor
        self._sensitive_fields = sensitive_fields or self.DEFAULT_SENSITIVE_FIELDS

    def emit(self, event: AuditEvent) -> None:
        """Emit an audit event with sensitive fields encrypted."""
        # Convert to dict for modification
        event_dict = event.to_dict()

        # Encrypt tool_request.params if present
        if "params" in self._sensitive_fields:
            tool_req = event_dict.get("tool_request", {})
            if tool_req.get("params"):
                tool_req["params"] = self._encryptor.encrypt_dict(tool_req["params"])

        # Encrypt result_summary if present and in sensitive fields
        result_summary = event_dict.get("result_summary")
        if "result_summary" in self._sensitive_fields and result_summary:
            encrypted = self._encryptor.encrypt(result_summary)
            event_dict["result_summary"] = encrypted.to_dict()

        # Encrypt metadata fields if present
        if "metadata" in self._sensitive_fields:
            for container in ["agent", "intent", "tool_request"]:
                if container in event_dict and "metadata" in event_dict[container]:
                    metadata = event_dict[container].get("metadata", {})
                    if metadata:
                        event_dict[container]["metadata"] = (
                            self._encryptor.encrypt_dict(metadata)
                        )

        # Create modified event for emission
        # We emit the dict directly since we've modified it
        self._inner.emit(_DictAuditEvent(event_dict))


class _DictAuditEvent:
    """Wrapper to pass a dict through AuditSink that expects to_dict()."""

    def __init__(self, data: dict[str, Any]):
        self._data = data

    def to_dict(self) -> dict[str, Any]:
        return self._data


class EncryptedValueDecoder:
    """Utility to decrypt encrypted values in audit event dicts.

    Args:
        encryptors: Mapping of key_id to FieldEncryptor.
    """

    def __init__(self, encryptors: dict[str, FieldEncryptor]):
        self._encryptors = encryptors

    def decrypt_event(self, event_dict: dict[str, Any]) -> dict[str, Any]:
        """Decrypt all encrypted values in an event dict."""
        return self._decrypt_recursive(event_dict)

    def _decrypt_recursive(self, data: Any) -> Any:
        if isinstance(data, dict):
            if EncryptedValue.is_encrypted(data):
                encrypted = EncryptedValue.from_dict(data)
                encryptor = self._encryptors.get(encrypted.key_id)
                if encryptor is None:
                    raise ValueError(f"No encryptor for key_id: {encrypted.key_id}")
                decrypted = encryptor.decrypt(encrypted)
                # Try to parse as JSON if it looks like JSON
                if decrypted.startswith("{") or decrypted.startswith("["):
                    try:
                        return json.loads(decrypted)
                    except json.JSONDecodeError:
                        pass
                return decrypted
            return {k: self._decrypt_recursive(v) for k, v in data.items()}
        if isinstance(data, list):
            return [self._decrypt_recursive(item) for item in data]
        return data
