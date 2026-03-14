"""Unit tests for Ed25519 record signing."""

import os
import tempfile

import pytest


def _has_cryptography():
    try:
        import cryptography  # noqa: F401

        return True
    except ImportError:
        return False


needs_cryptography = pytest.mark.skipif(
    not _has_cryptography(), reason="cryptography not installed"
)


@needs_cryptography
def test_generate_keypair():
    """Generate keypair creates a valid PEM file."""
    from gateway.crypto.signing import generate_keypair

    with tempfile.TemporaryDirectory() as tmp:
        key_path = os.path.join(tmp, "test_key.pem")
        assert generate_keypair(key_path) is True
        assert os.path.exists(key_path)
        content = open(key_path, "rb").read()
        assert b"PRIVATE KEY" in content


@needs_cryptography
def test_load_and_sign():
    """Load key and sign a hash."""
    from gateway.crypto import signing

    # Reset module state
    signing._signing_key = None
    signing._verify_key = None

    with tempfile.TemporaryDirectory() as tmp:
        key_path = os.path.join(tmp, "test_key.pem")
        signing.generate_keypair(key_path)
        assert signing.load_signing_key(key_path) is True
        sig = signing.sign_hash("abc123hash")
        assert sig is not None
        assert len(sig) > 0

    # Cleanup
    signing._signing_key = None
    signing._verify_key = None


@needs_cryptography
def test_sign_and_verify():
    """Sign and verify round-trip."""
    from gateway.crypto import signing

    signing._signing_key = None
    signing._verify_key = None

    with tempfile.TemporaryDirectory() as tmp:
        key_path = os.path.join(tmp, "test_key.pem")
        signing.generate_keypair(key_path)
        signing.load_signing_key(key_path)

        record_hash = "deadbeef" * 16
        sig = signing.sign_hash(record_hash)
        assert sig is not None
        assert signing.verify_signature(record_hash, sig) is True

    signing._signing_key = None
    signing._verify_key = None


@needs_cryptography
def test_verify_wrong_hash():
    """Verification fails for wrong hash."""
    from gateway.crypto import signing

    signing._signing_key = None
    signing._verify_key = None

    with tempfile.TemporaryDirectory() as tmp:
        key_path = os.path.join(tmp, "test_key.pem")
        signing.generate_keypair(key_path)
        signing.load_signing_key(key_path)

        sig = signing.sign_hash("original_hash")
        assert sig is not None
        assert signing.verify_signature("different_hash", sig) is False

    signing._signing_key = None
    signing._verify_key = None


@needs_cryptography
def test_verify_wrong_signature():
    """Verification fails for corrupted signature."""
    from gateway.crypto import signing

    signing._signing_key = None
    signing._verify_key = None

    with tempfile.TemporaryDirectory() as tmp:
        key_path = os.path.join(tmp, "test_key.pem")
        signing.generate_keypair(key_path)
        signing.load_signing_key(key_path)

        assert signing.verify_signature("hash", "bm90YXNpZw==") is False

    signing._signing_key = None
    signing._verify_key = None


def test_sign_without_key():
    """Signing without loaded key returns None."""
    from gateway.crypto import signing

    signing._signing_key = None
    signing._verify_key = None
    assert signing.sign_hash("test") is None


def test_verify_without_key():
    """Verification without loaded key returns False."""
    from gateway.crypto import signing

    signing._signing_key = None
    signing._verify_key = None
    assert signing.verify_signature("test", "sig") is False


def test_load_nonexistent_key():
    """Loading nonexistent key returns False."""
    from gateway.crypto.signing import load_signing_key

    assert load_signing_key("/nonexistent/path/key.pem") is False


@needs_cryptography
def test_get_public_key_pem():
    """Public key PEM export works after loading."""
    from gateway.crypto import signing

    signing._signing_key = None
    signing._verify_key = None

    with tempfile.TemporaryDirectory() as tmp:
        key_path = os.path.join(tmp, "test_key.pem")
        signing.generate_keypair(key_path)
        signing.load_signing_key(key_path)

        pem = signing.get_public_key_pem()
        assert pem is not None
        assert "PUBLIC KEY" in pem

    signing._signing_key = None
    signing._verify_key = None


def test_get_public_key_without_key():
    """Public key export returns None without loaded key."""
    from gateway.crypto import signing

    signing._signing_key = None
    signing._verify_key = None
    assert signing.get_public_key_pem() is None
