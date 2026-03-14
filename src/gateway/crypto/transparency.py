"""Transparency log publisher — POST signed Merkle roots to append-only endpoint."""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


class TransparencyLogPublisher:
    """Publishes Merkle tree checkpoint roots to an external transparency log.

    Each checkpoint is POSTed as a JSON payload containing the root hash,
    leaf count, timestamp, and gateway ID. The endpoint is expected to be
    an append-only log that returns a sequence number.

    Fail-open: publishing failures are logged but never block gateway operation.
    """

    def __init__(self, log_url: str, gateway_id: str = "") -> None:
        self._log_url = log_url
        self._gateway_id = gateway_id
        self._published: list[dict[str, Any]] = []

    async def publish(
        self,
        root_hash: str,
        leaf_count: int,
        http_client: Any,
    ) -> dict[str, Any] | None:
        """Publish a Merkle root to the transparency log.

        Args:
            root_hash: The Merkle tree root hash (SHA3-512 hex).
            leaf_count: Number of leaves in the tree.
            http_client: httpx.AsyncClient for making the POST request.

        Returns:
            Response dict from the log server, or None on failure.
        """
        if not self._log_url:
            logger.debug("Transparency log URL not configured, skipping publish")
            return None

        payload = {
            "root_hash": root_hash,
            "leaf_count": leaf_count,
            "timestamp": time.time(),
            "gateway_id": self._gateway_id,
        }

        try:
            response = await http_client.post(
                self._log_url,
                json=payload,
                timeout=10.0,
            )
            if response.status_code < 300:
                result = response.json()
                entry = {**payload, "sequence": result.get("sequence"), "status": "published"}
                self._published.append(entry)
                logger.info(
                    "Transparency log published: root=%s leaves=%d seq=%s",
                    root_hash[:16], leaf_count, result.get("sequence"),
                )
                return result
            else:
                logger.warning(
                    "Transparency log publish failed: status=%d body=%s",
                    response.status_code, response.text[:200],
                )
                return None
        except Exception as e:
            logger.warning("Transparency log publish error (fail-open): %s", e)
            return None

    @property
    def published_count(self) -> int:
        return len(self._published)

    @property
    def last_published(self) -> dict[str, Any] | None:
        return self._published[-1] if self._published else None
