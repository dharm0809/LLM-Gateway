"""SHA3-512 hashing. Single source of truth for the gateway."""

from __future__ import annotations

import hashlib
import os


def compute_sha3_512(data: bytes) -> str:
    """Compute SHA3-512 hash of raw bytes, return 128-char hex digest."""
    return hashlib.sha3_512(data).hexdigest()


def compute_sha3_512_string(text: str) -> str:
    """Compute SHA3-512 hash of a UTF-8 string, return 128-char hex digest."""
    return hashlib.sha3_512(text.encode("utf-8")).hexdigest()


def generate_mock_hash() -> str:
    """Generate a random SHA3-512 hash for mock data."""
    return compute_sha3_512(os.urandom(64))
