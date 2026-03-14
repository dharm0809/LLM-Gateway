"""HTTP webhook exporter for SIEM integration (Splunk HEC, Datadog, Elastic)."""
from __future__ import annotations

import asyncio
import logging

import httpx

import gateway.util.json_utils as json
from .base import AuditExporter

logger = logging.getLogger(__name__)

_RETRY_ATTEMPTS = 3
_RETRY_DELAY = 1.0  # seconds


class WebhookExporter(AuditExporter):
    """POSTs audit records to an HTTP endpoint."""

    def __init__(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        batch_size: int = 50,
        flush_interval: int = 30,
    ) -> None:
        self._url = url
        self._headers = headers or {}
        self._batch_size = batch_size
        self._flush_interval = flush_interval
        self._buffer: list[dict] = []
        self._lock = asyncio.Lock()
        self._client = httpx.AsyncClient(timeout=10.0)
        self._flush_task: asyncio.Task | None = None

    def start(self) -> None:
        """Start the background flush task."""
        self._flush_task = asyncio.create_task(self._flush_loop())

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(self._flush_interval)
            await self._flush()

    async def export(self, record: dict) -> None:
        async with self._lock:
            self._buffer.append(record)
            if len(self._buffer) >= self._batch_size:
                await self._flush_locked()

    async def _flush(self) -> None:
        async with self._lock:
            await self._flush_locked()

    async def _flush_locked(self) -> None:
        if not self._buffer:
            return
        batch = self._buffer[:]
        self._buffer.clear()
        await self._send_batch(batch)

    async def _send_batch(self, batch: list[dict]) -> None:
        for attempt in range(_RETRY_ATTEMPTS):
            try:
                resp = await self._client.post(
                    self._url,
                    content=json.dumps_bytes({"records": batch}),
                    headers={"Content-Type": "application/json", **self._headers},
                )
                resp.raise_for_status()
                return
            except Exception as e:
                logger.warning("WebhookExporter attempt %d failed: %s", attempt + 1, e)
                if attempt < _RETRY_ATTEMPTS - 1:
                    await asyncio.sleep(_RETRY_DELAY)
        logger.error(
            "WebhookExporter: all %d attempts failed, dropping %d records",
            _RETRY_ATTEMPTS, len(batch),
        )

    async def close(self) -> None:
        if self._flush_task:
            self._flush_task.cancel()
        await self._flush()
        await self._client.aclose()
