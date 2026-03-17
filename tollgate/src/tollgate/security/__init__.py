"""Security utilities for Tollgate.

Provides encryption, immutable audit logging, and enhanced verification
for enterprise deployments.

Usage:

    # Field-level encryption
    from tollgate.security import FieldEncryptor, EncryptedAuditSink

    key = os.urandom(32)  # 256-bit key
    encryptor = FieldEncryptor(key, key_id="key-2024-01")
    encrypted_sink = EncryptedAuditSink(jsonl_sink, encryptor)

    # Immutable audit logs with hash chaining
    from tollgate.security import ImmutableAuditSink

    signing_key = os.urandom(32)
    immutable_sink = ImmutableAuditSink(jsonl_sink, signing_key)
"""

try:
    from .encryption import (
        EncryptedAuditSink,
        EncryptedValue,
        FieldEncryptor,
    )
    from .immutable_audit import ImmutableAuditSink

    __all__ = [
        "FieldEncryptor",
        "EncryptedValue",
        "EncryptedAuditSink",
        "ImmutableAuditSink",
    ]
except ImportError:
    # cryptography not installed
    __all__ = []
