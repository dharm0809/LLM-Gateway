"""Base class for audit log exporters."""
from __future__ import annotations
import asyncio
import logging
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)


class AuditExporter(ABC):
    """Abstract base for audit record exporters (S3, webhook, file)."""

    @abstractmethod
    async def export(self, record: dict) -> None:
        """Export one audit record."""

    async def export_batch(self, records: list[dict]) -> None:
        """Export a batch of records. Default: export one by one."""
        for record in records:
            await self.export(record)

    @abstractmethod
    async def close(self) -> None:
        """Release resources."""
