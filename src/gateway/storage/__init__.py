"""Storage abstraction layer: pluggable backends with fan-out routing."""

from gateway.storage.backend import StorageBackend
from gateway.storage.router import StorageRouter, WriteResult
from gateway.storage.wal_backend import WALBackend
from gateway.storage.walacor_backend import WalacorBackend

__all__ = ["StorageBackend", "StorageRouter", "WriteResult", "WALBackend", "WalacorBackend"]
