"""Unit tests for attachment notification cache."""

import time
from gateway.middleware.attachment_tracker import AttachmentNotificationCache


def test_store_and_retrieve():
    """Store a notification, retrieve by hash."""
    cache = AttachmentNotificationCache(max_size=100, ttl_seconds=3600)
    meta = {
        "filename": "test.pdf",
        "mimetype": "application/pdf",
        "size_bytes": 1000,
        "hash_sha3_512": "abc123",
        "chat_id": "chat-1",
        "user_id": "user-1",
        "user_email": "user@example.com",
        "upload_timestamp": "2026-03-14T00:00:00Z",
    }
    cache.store(meta)
    result = cache.get("abc123")
    assert result is not None
    assert result["filename"] == "test.pdf"
    assert result["user_id"] == "user-1"


def test_get_missing_returns_none():
    """Missing hash returns None."""
    cache = AttachmentNotificationCache(max_size=100, ttl_seconds=3600)
    assert cache.get("nonexistent") is None


def test_max_size_evicts_oldest():
    """Cache evicts oldest entries when max_size exceeded."""
    cache = AttachmentNotificationCache(max_size=2, ttl_seconds=3600)
    cache.store({"hash_sha3_512": "a", "filename": "1.pdf"})
    cache.store({"hash_sha3_512": "b", "filename": "2.pdf"})
    cache.store({"hash_sha3_512": "c", "filename": "3.pdf"})
    assert cache.get("a") is None  # evicted
    assert cache.get("b") is not None
    assert cache.get("c") is not None


def test_ttl_expiry():
    """Entries expire after TTL."""
    cache = AttachmentNotificationCache(max_size=100, ttl_seconds=0)
    cache.store({"hash_sha3_512": "x", "filename": "old.pdf"})
    # TTL=0 means already expired
    assert cache.get("x") is None


def test_store_requires_hash():
    """Store without hash_sha3_512 is silently skipped."""
    cache = AttachmentNotificationCache(max_size=100, ttl_seconds=3600)
    cache.store({"filename": "no_hash.pdf"})
    assert len(cache._entries) == 0
