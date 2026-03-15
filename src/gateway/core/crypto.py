"""Cryptographic helpers; wraps hashing for validation."""

from __future__ import annotations

from gateway.core.hashing import compute_sha3_512, compute_sha3_512_string
from gateway.core.constants import SHA3_512_HEX_LENGTH


def validate_sha3_512_hex(h: str | None) -> str | None:
    """Validate SHA3-512 hex string (128 chars). Returns normalized lowercase or None."""
    if h is None or not h:
        return None
    h = h.strip().lower()
    if len(h) != SHA3_512_HEX_LENGTH:
        raise ValueError(f"SHA3-512 must be exactly {SHA3_512_HEX_LENGTH} hex characters, got {len(h)}")
    if not all(c in "0123456789abcdef" for c in h):
        raise ValueError("SHA3-512 must contain only hexadecimal characters")
    return h


__all__ = ["compute_sha3_512", "compute_sha3_512_string", "validate_sha3_512_hex"]
