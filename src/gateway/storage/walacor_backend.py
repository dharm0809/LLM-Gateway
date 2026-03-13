"""WalacorBackend — StorageBackend wrapping the Walacor REST client."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gateway.walacor.client import WalacorClient

logger = logging.getLogger(__name__)


class WalacorBackend:
    """StorageBackend implementation backed by Walacor cloud API."""

    name = "walacor"

    def __init__(self, client: WalacorClient) -> None:
        self._client = client

    async def write_execution(self, record: dict) -> bool:
        try:
            await self._client.write_execution(record)
            return True
        except Exception:
            logger.error(
                "Walacor write_execution failed execution_id=%s",
                record.get("execution_id"),
                exc_info=True,
            )
            return False

    async def write_attempt(self, record: dict) -> None:
        try:
            await self._client.write_attempt(**record)
        except Exception:
            logger.warning(
                "Walacor write_attempt failed request_id=%s",
                record.get("request_id"),
                exc_info=True,
            )

    async def write_tool_event(self, record: dict) -> None:
        try:
            await self._client.write_tool_event(record)
        except Exception:
            logger.warning(
                "Walacor write_tool_event failed event_id=%s",
                record.get("event_id"),
                exc_info=True,
            )

    async def close(self) -> None:
        await self._client.close()
