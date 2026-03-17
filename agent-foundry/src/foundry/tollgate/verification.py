"""Agent identity verification via HMAC and Ed25519 signing.

Provides utilities to sign and verify AgentContext instances so that
the ControlTower can trust agent_id claims are authentic.

Supports two signing methods:
- HMAC-SHA256: Symmetric key signing (default, no extra dependencies)
- Ed25519: Asymmetric key signing (requires cryptography package)

Usage:
    # HMAC signing (symmetric)
    from tollgate.verification import sign_agent_context, make_verifier

    ctx = sign_agent_context(
        AgentContext(agent_id="my-agent", version="1.0", owner="team-a"),
        secret_key=b"shared-secret",
    )
    tower = ControlTower(..., verify_fn=make_verifier(b"shared-secret"))

    # Ed25519 signing (asymmetric) - requires pip install tollgate[encryption]
    from tollgate.verification import (
        generate_ed25519_keypair,
        sign_agent_context_ed25519,
        make_ed25519_verifier,
    )

    private_key, public_key = generate_ed25519_keypair()
    ctx = sign_agent_context_ed25519(ctx, private_key)
    tower = ControlTower(..., verify_fn=make_ed25519_verifier(public_key))
"""

import hashlib
import hmac
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from .types import AgentContext


def _compute_signature(agent_ctx: AgentContext, secret_key: bytes) -> str:
    """Compute HMAC-SHA256 over the canonical agent identity fields."""
    payload = f"{agent_ctx.agent_id}|{agent_ctx.version}|{agent_ctx.owner}"
    return hmac.new(secret_key, payload.encode("utf-8"), hashlib.sha256).hexdigest()


def sign_agent_context(agent_ctx: AgentContext, secret_key: bytes) -> AgentContext:
    """Return a new AgentContext with an HMAC signature in metadata.

    The signature covers ``agent_id``, ``version``, and ``owner``.
    It is stored under ``metadata["_signature"]``.
    """
    sig = _compute_signature(agent_ctx, secret_key)
    new_meta: dict[str, Any] = {**agent_ctx.metadata, "_signature": sig}
    return replace(agent_ctx, metadata=new_meta)


def verify_agent_context(agent_ctx: AgentContext, secret_key: bytes) -> bool:
    """Verify that the AgentContext signature is valid.

    Returns True if the signature matches, False otherwise.
    Returns False if no signature is present.
    """
    sig = agent_ctx.metadata.get("_signature")
    if not sig or not isinstance(sig, str):
        return False
    expected = _compute_signature(agent_ctx, secret_key)
    return hmac.compare_digest(sig, expected)


def make_verifier(
    secret_key: bytes,
) -> "callable[[AgentContext], bool]":
    """Create a verification function suitable for ControlTower.verify_fn.

    Example:
        tower = ControlTower(
            ...,
            verify_fn=make_verifier(b"my-secret"),
        )
    """

    def _verify(agent_ctx: AgentContext) -> bool:
        return verify_agent_context(agent_ctx, secret_key)

    return _verify


# =============================================================================
# Ed25519 Asymmetric Signing (requires cryptography package)
# =============================================================================


def _get_ed25519_private_key():
    """Lazy import of Ed25519PrivateKey."""
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        return Ed25519PrivateKey
    except ImportError as e:
        raise ImportError(
            "Ed25519 signing requires the cryptography package. "
            "Install it with: pip install tollgate[encryption]"
        ) from e


def _get_ed25519_public_key():
    """Lazy import of Ed25519PublicKey."""
    try:
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        return Ed25519PublicKey
    except ImportError as e:
        raise ImportError(
            "Ed25519 verification requires the cryptography package. "
            "Install it with: pip install tollgate[encryption]"
        ) from e


def generate_ed25519_keypair() -> tuple[bytes, bytes]:
    """Generate a new Ed25519 key pair.

    Returns:
        Tuple of (private_key_bytes, public_key_bytes).
        Both keys are in raw 32-byte format.

    Raises:
        ImportError: If cryptography package is not installed.

    Example:
        private_key, public_key = generate_ed25519_keypair()
        # Store private_key securely for signing
        # Distribute public_key for verification
    """
    from cryptography.hazmat.primitives import serialization

    ed25519_cls = _get_ed25519_private_key()
    private_key = ed25519_cls.generate()
    public_key = private_key.public_key()

    private_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_bytes = public_key.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )

    return private_bytes, public_bytes


def _compute_ed25519_payload(agent_ctx: AgentContext) -> bytes:
    """Compute canonical payload for Ed25519 signing."""
    payload = f"{agent_ctx.agent_id}|{agent_ctx.version}|{agent_ctx.owner}"
    return payload.encode("utf-8")


def sign_agent_context_ed25519(
    agent_ctx: AgentContext, private_key: bytes
) -> AgentContext:
    """Return a new AgentContext with an Ed25519 signature in metadata.

    The signature covers ``agent_id``, ``version``, and ``owner``.
    It is stored under ``metadata["_ed25519_signature"]`` as a hex string.

    Args:
        agent_ctx: The agent context to sign.
        private_key: 32-byte Ed25519 private key (raw format).

    Returns:
        New AgentContext with signature in metadata.

    Raises:
        ImportError: If cryptography package is not installed.
        ValueError: If private_key is not valid.

    Example:
        private_key, public_key = generate_ed25519_keypair()
        signed_ctx = sign_agent_context_ed25519(ctx, private_key)
    """
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    _get_ed25519_private_key()  # Ensure cryptography is available

    # Load the private key from raw bytes
    key = Ed25519PrivateKey.from_private_bytes(private_key)

    # Sign the canonical payload
    payload = _compute_ed25519_payload(agent_ctx)
    signature = key.sign(payload)

    # Store signature as hex string in metadata
    new_meta: dict[str, Any] = {
        **agent_ctx.metadata,
        "_ed25519_signature": signature.hex(),
    }
    return replace(agent_ctx, metadata=new_meta)


def verify_agent_context_ed25519(
    agent_ctx: AgentContext, public_key: bytes
) -> bool:
    """Verify that the AgentContext Ed25519 signature is valid.

    Args:
        agent_ctx: The agent context to verify.
        public_key: 32-byte Ed25519 public key (raw format).

    Returns:
        True if the signature is valid, False otherwise.
        Returns False if no signature is present.

    Raises:
        ImportError: If cryptography package is not installed.

    Example:
        is_valid = verify_agent_context_ed25519(signed_ctx, public_key)
    """
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    _get_ed25519_public_key()  # Ensure cryptography is available

    # Get the signature from metadata
    sig_hex = agent_ctx.metadata.get("_ed25519_signature")
    if not sig_hex or not isinstance(sig_hex, str):
        return False

    try:
        signature = bytes.fromhex(sig_hex)
    except ValueError:
        return False

    # Load the public key from raw bytes
    try:
        key = Ed25519PublicKey.from_public_bytes(public_key)
    except ValueError:
        return False

    # Verify the signature
    payload = _compute_ed25519_payload(agent_ctx)
    try:
        key.verify(signature, payload)
        return True
    except InvalidSignature:
        return False


def make_ed25519_verifier(
    public_key: bytes,
) -> Callable[[AgentContext], bool]:
    """Create an Ed25519 verification function suitable for ControlTower.verify_fn.

    Args:
        public_key: 32-byte Ed25519 public key (raw format).

    Returns:
        A verification function that takes AgentContext and returns bool.

    Raises:
        ImportError: If cryptography package is not installed (at verification time).

    Example:
        private_key, public_key = generate_ed25519_keypair()
        tower = ControlTower(
            ...,
            verify_fn=make_ed25519_verifier(public_key),
        )
    """

    def _verify(agent_ctx: AgentContext) -> bool:
        return verify_agent_context_ed25519(agent_ctx, public_key)

    return _verify
