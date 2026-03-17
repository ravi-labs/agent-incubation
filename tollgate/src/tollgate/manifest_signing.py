"""Cryptographic manifest signing and verification.

Uses HMAC-SHA256 for manifest integrity verification. This ensures that
the manifest file has not been tampered with since it was signed by a
trusted party.

For production use with asymmetric keys (Ed25519), install the optional
``cryptography`` dependency and use the Ed25519 variants.

Usage (HMAC — zero dependencies):

    # Sign a manifest (CI/build step):
    from tollgate.manifest_signing import sign_manifest, verify_manifest

    sign_manifest("manifest.yaml", secret_key=b"build-secret")
    # Creates manifest.yaml.sig alongside the manifest

    # Verify at load time:
    valid = verify_manifest("manifest.yaml", secret_key=b"build-secret")
    # Returns True if signature matches, False otherwise

    # Use with ToolRegistry:
    registry = ToolRegistry("manifest.yaml", signing_key=b"build-secret")
    # Raises ValueError if signature is missing or invalid
"""

import hashlib
import hmac
from pathlib import Path


def _compute_hmac(content: bytes, secret_key: bytes) -> str:
    """Compute HMAC-SHA256 hex digest of content."""
    return hmac.new(secret_key, content, hashlib.sha256).hexdigest()


def sign_manifest(manifest_path: str | Path, *, secret_key: bytes) -> Path:
    """Sign a manifest file, writing the signature to ``<path>.sig``.

    Args:
        manifest_path: Path to the manifest YAML file.
        secret_key: Shared secret key for HMAC-SHA256.

    Returns:
        Path to the created signature file.
    """
    manifest_path = Path(manifest_path)
    if not manifest_path.exists():
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")

    content = manifest_path.read_bytes()
    signature = _compute_hmac(content, secret_key)

    sig_path = manifest_path.with_suffix(manifest_path.suffix + ".sig")
    sig_path.write_text(signature, encoding="utf-8")
    return sig_path


def verify_manifest(manifest_path: str | Path, *, secret_key: bytes) -> bool:
    """Verify a manifest file against its ``.sig`` signature file.

    Args:
        manifest_path: Path to the manifest YAML file.
        secret_key: Shared secret key for HMAC-SHA256.

    Returns:
        True if the signature is valid, False otherwise.
        Returns False if the signature file doesn't exist.
    """
    manifest_path = Path(manifest_path)
    sig_path = manifest_path.with_suffix(manifest_path.suffix + ".sig")

    if not manifest_path.exists() or not sig_path.exists():
        return False

    content = manifest_path.read_bytes()
    expected = _compute_hmac(content, secret_key)

    stored = sig_path.read_text(encoding="utf-8").strip()
    return hmac.compare_digest(expected, stored)


def get_manifest_hash(manifest_path: str | Path) -> str:
    """Compute a SHA-256 content hash of the manifest (for audit trails)."""
    content = Path(manifest_path).read_bytes()
    return hashlib.sha256(content).hexdigest()
