"""Attachment tracking: notification cache + request body image/file extraction."""

from __future__ import annotations

import logging
import time
from collections import OrderedDict
from typing import Any

logger = logging.getLogger(__name__)


class AttachmentNotificationCache:
    """Bounded TTL cache for file upload notifications from OpenWebUI webhook.

    Stores metadata keyed by SHA3-512 hash. Entries expire after ttl_seconds.
    Evicts oldest entries when max_size is exceeded.
    """

    def __init__(self, max_size: int = 1000, ttl_seconds: int = 3600):
        self._max_size = max_size
        self._ttl = ttl_seconds
        self._entries: OrderedDict[str, tuple[dict, float]] = OrderedDict()

    def store(self, meta: dict[str, Any]) -> None:
        file_hash = meta.get("hash_sha3_512")
        if not file_hash:
            return
        now = time.monotonic()
        self._entries[file_hash] = (meta, now)
        self._entries.move_to_end(file_hash)
        while len(self._entries) > self._max_size:
            self._entries.popitem(last=False)

    def get(self, file_hash: str) -> dict[str, Any] | None:
        entry = self._entries.get(file_hash)
        if entry is None:
            return None
        meta, stored_at = entry
        if time.monotonic() - stored_at > self._ttl:
            del self._entries[file_hash]
            return None
        return meta
