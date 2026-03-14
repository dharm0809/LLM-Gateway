"""JSONL file exporter with size-based rotation."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import gateway.util.json_utils as json
from .base import AuditExporter

logger = logging.getLogger(__name__)


class FileExporter(AuditExporter):
    """Writes audit records to a rotating JSONL file."""

    def __init__(self, file_path: str, max_size_mb: int = 100) -> None:
        self._path = Path(file_path)
        self._max_size_bytes = max_size_mb * 1024 * 1024
        self._lock = asyncio.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)

    async def export(self, record: dict) -> None:
        line = json.dumps(record) + "\n"
        async with self._lock:
            try:
                # Rotate if file exceeds max size
                if self._path.exists() and self._path.stat().st_size >= self._max_size_bytes:
                    rotated = self._path.with_suffix(f".{int(self._path.stat().st_mtime)}.jsonl")
                    self._path.rename(rotated)
                with self._path.open("a", encoding="utf-8") as f:
                    f.write(line)
            except OSError as e:
                logger.error("FileExporter write failed: %s", e)

    async def close(self) -> None:
        pass  # nothing to release
