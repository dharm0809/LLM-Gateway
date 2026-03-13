# Storage Abstraction Layer Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace 12 scattered dual-write call sites with a `StorageRouter` that fans out writes to pluggable `StorageBackend` implementations.

**Architecture:** A `StorageBackend` Python protocol defines write methods for executions, attempts, and tool events. `WALBackend` and `WalacorBackend` wrap existing writers. A `StorageRouter` fans out to all backends independently. The orchestrator and completeness middleware call `ctx.storage.*` instead of touching backends directly.

**Tech Stack:** Python 3.12, pytest + anyio, Protocol (typing), dataclasses

---

### Task 1: StorageBackend Protocol + WriteResult

**Files:**
- Create: `src/gateway/storage/__init__.py`
- Create: `src/gateway/storage/backend.py`
- Create: `src/gateway/storage/router.py`
- Test: `tests/unit/test_storage_router.py`

**Step 1: Write failing tests for StorageRouter**

Create `tests/unit/test_storage_router.py`:

```python
"""Unit tests for StorageRouter fan-out logic."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock

from gateway.storage.backend import StorageBackend
from gateway.storage.router import StorageRouter, WriteResult


@pytest.fixture(params=["asyncio"])
def anyio_backend(request):
    return request.param


class FakeBackend:
    """Minimal StorageBackend for testing."""

    def __init__(self, name: str, fail_execution: bool = False, fail_attempt: bool = False, fail_tool: bool = False):
        self._name = name
        self._fail_execution = fail_execution
        self._fail_attempt = fail_attempt
        self._fail_tool = fail_tool
        self.closed = False
        self.executions: list[dict] = []
        self.attempts: list[dict] = []
        self.tool_events: list[dict] = []

    @property
    def name(self) -> str:
        return self._name

    async def write_execution(self, record: dict) -> bool:
        if self._fail_execution:
            raise RuntimeError("execution write failed")
        self.executions.append(record)
        return True

    async def write_attempt(self, record: dict) -> None:
        if self._fail_attempt:
            raise RuntimeError("attempt write failed")
        self.attempts.append(record)

    async def write_tool_event(self, record: dict) -> None:
        if self._fail_tool:
            raise RuntimeError("tool event write failed")
        self.tool_events.append(record)

    async def close(self) -> None:
        self.closed = True


@pytest.mark.anyio
async def test_write_execution_fan_out_both_succeed():
    b1 = FakeBackend("wal")
    b2 = FakeBackend("walacor")
    router = StorageRouter([b1, b2])
    result = await router.write_execution({"execution_id": "e1"})
    assert result.succeeded == ["wal", "walacor"]
    assert result.failed == []
    assert b1.executions == [{"execution_id": "e1"}]
    assert b2.executions == [{"execution_id": "e1"}]


@pytest.mark.anyio
async def test_write_execution_one_fails():
    b1 = FakeBackend("wal")
    b2 = FakeBackend("walacor", fail_execution=True)
    router = StorageRouter([b1, b2])
    result = await router.write_execution({"execution_id": "e2"})
    assert result.succeeded == ["wal"]
    assert result.failed == ["walacor"]
    assert b1.executions == [{"execution_id": "e2"}]


@pytest.mark.anyio
async def test_write_execution_all_fail():
    b1 = FakeBackend("wal", fail_execution=True)
    b2 = FakeBackend("walacor", fail_execution=True)
    router = StorageRouter([b1, b2])
    result = await router.write_execution({"execution_id": "e3"})
    assert result.succeeded == []
    assert result.failed == ["wal", "walacor"]


@pytest.mark.anyio
async def test_write_attempt_fire_and_forget():
    b1 = FakeBackend("wal")
    b2 = FakeBackend("walacor", fail_attempt=True)
    router = StorageRouter([b1, b2])
    # Should NOT raise despite b2 failing
    await router.write_attempt({"request_id": "r1"})
    assert b1.attempts == [{"request_id": "r1"}]


@pytest.mark.anyio
async def test_write_tool_event_fire_and_forget():
    b1 = FakeBackend("wal", fail_tool=True)
    b2 = FakeBackend("walacor")
    router = StorageRouter([b1, b2])
    await router.write_tool_event({"event_id": "t1"})
    assert b2.tool_events == [{"event_id": "t1"}]


@pytest.mark.anyio
async def test_close_all_backends():
    b1 = FakeBackend("wal")
    b2 = FakeBackend("walacor")
    router = StorageRouter([b1, b2])
    await router.close()
    assert b1.closed is True
    assert b2.closed is True


@pytest.mark.anyio
async def test_empty_backends_list():
    router = StorageRouter([])
    result = await router.write_execution({"execution_id": "e4"})
    assert result.succeeded == []
    assert result.failed == []
    await router.write_attempt({"request_id": "r2"})  # no-op, no error
    await router.write_tool_event({"event_id": "t2"})  # no-op, no error


def test_backend_names():
    b1 = FakeBackend("wal")
    b2 = FakeBackend("walacor")
    router = StorageRouter([b1, b2])
    assert router.backend_names == ["wal", "walacor"]
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_storage_router.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'gateway.storage'`

**Step 3: Write the implementation**

Create `src/gateway/storage/__init__.py`:

```python
"""Storage abstraction layer: pluggable backends with fan-out routing."""

from gateway.storage.backend import StorageBackend
from gateway.storage.router import StorageRouter, WriteResult

__all__ = ["StorageBackend", "StorageRouter", "WriteResult"]
```

Create `src/gateway/storage/backend.py`:

```python
"""StorageBackend protocol — interface for audit record storage backends."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class StorageBackend(Protocol):
    """Interface for audit record storage backends.

    Each backend handles its own field mapping, serialization, and error
    handling internally. The StorageRouter fans out writes to all backends.
    """

    @property
    def name(self) -> str:
        """Unique identifier for this backend (e.g. 'wal', 'walacor')."""
        ...

    async def write_execution(self, record: dict) -> bool:
        """Write an execution record. Returns True on success, False on failure."""
        ...

    async def write_attempt(self, record: dict) -> None:
        """Write an attempt record. Best-effort — must not raise."""
        ...

    async def write_tool_event(self, record: dict) -> None:
        """Write a tool event record. Best-effort — must not raise."""
        ...

    async def close(self) -> None:
        """Graceful shutdown."""
        ...
```

Create `src/gateway/storage/router.py`:

```python
"""StorageRouter — fans out writes to all registered backends independently."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from gateway.storage.backend import StorageBackend

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WriteResult:
    """Outcome of an execution write across all backends."""

    succeeded: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)


class StorageRouter:
    """Fans out writes to all registered StorageBackend instances."""

    def __init__(self, backends: list[StorageBackend]) -> None:
        self._backends = list(backends)

    @property
    def backend_names(self) -> list[str]:
        return [b.name for b in self._backends]

    async def write_execution(self, record: dict) -> WriteResult:
        """Fan-out execution write. Returns WriteResult with per-backend outcomes."""
        succeeded: list[str] = []
        failed: list[str] = []
        for backend in self._backends:
            try:
                ok = await backend.write_execution(record)
                (succeeded if ok else failed).append(backend.name)
            except Exception:
                logger.error(
                    "Storage backend %s write_execution failed for execution_id=%s",
                    backend.name,
                    record.get("execution_id"),
                    exc_info=True,
                )
                failed.append(backend.name)
        if self._backends and not succeeded:
            logger.error(
                "ALL storage backends failed for execution_id=%s",
                record.get("execution_id"),
            )
        return WriteResult(succeeded=succeeded, failed=failed)

    async def write_attempt(self, record: dict) -> None:
        """Fan-out attempt write. Fire-and-forget — never raises."""
        for backend in self._backends:
            try:
                await backend.write_attempt(record)
            except Exception:
                logger.warning(
                    "Storage backend %s write_attempt failed for request_id=%s",
                    backend.name,
                    record.get("request_id"),
                    exc_info=True,
                )

    async def write_tool_event(self, record: dict) -> None:
        """Fan-out tool event write. Fire-and-forget — never raises."""
        for backend in self._backends:
            try:
                await backend.write_tool_event(record)
            except Exception:
                logger.warning(
                    "Storage backend %s write_tool_event failed for event_id=%s",
                    backend.name,
                    record.get("event_id"),
                    exc_info=True,
                )

    async def close(self) -> None:
        """Close all backends. Errors logged but not raised."""
        for backend in self._backends:
            try:
                await backend.close()
            except Exception:
                logger.warning("Storage backend %s close failed", backend.name, exc_info=True)
```

**Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/test_storage_router.py -v`
Expected: 8 passed

**Step 5: Commit**

```bash
git add src/gateway/storage/ tests/unit/test_storage_router.py
git commit -m "feat(storage): add StorageBackend protocol, StorageRouter, and WriteResult"
```

---

### Task 2: WALBackend Implementation

**Files:**
- Create: `src/gateway/storage/wal_backend.py`
- Modify: `tests/unit/test_storage_router.py` (add WALBackend tests)

**Step 1: Add failing tests for WALBackend**

Append to `tests/unit/test_storage_router.py`:

```python
from unittest.mock import MagicMock, patch
from gateway.storage.wal_backend import WALBackend


def _make_wal_writer() -> MagicMock:
    writer = MagicMock()
    writer.write_and_fsync = MagicMock()
    writer.write_attempt = MagicMock()
    writer.write_tool_event = MagicMock()
    writer.close = MagicMock()
    return writer


@pytest.mark.anyio
async def test_wal_backend_write_execution_success():
    writer = _make_wal_writer()
    backend = WALBackend(writer)
    assert backend.name == "wal"
    ok = await backend.write_execution({"execution_id": "e1", "model_id": "qwen3:4b"})
    assert ok is True
    writer.write_and_fsync.assert_called_once_with({"execution_id": "e1", "model_id": "qwen3:4b"})


@pytest.mark.anyio
async def test_wal_backend_write_execution_failure():
    writer = _make_wal_writer()
    writer.write_and_fsync.side_effect = RuntimeError("disk full")
    backend = WALBackend(writer)
    ok = await backend.write_execution({"execution_id": "e2"})
    assert ok is False


@pytest.mark.anyio
async def test_wal_backend_write_attempt():
    writer = _make_wal_writer()
    backend = WALBackend(writer)
    await backend.write_attempt({
        "request_id": "r1", "tenant_id": "t1", "path": "/v1/chat/completions",
        "disposition": "allowed", "status_code": 200,
    })
    writer.write_attempt.assert_called_once_with(
        request_id="r1", tenant_id="t1", path="/v1/chat/completions",
        disposition="allowed", status_code=200,
    )


@pytest.mark.anyio
async def test_wal_backend_write_tool_event():
    writer = _make_wal_writer()
    backend = WALBackend(writer)
    await backend.write_tool_event({"event_id": "t1"})
    writer.write_tool_event.assert_called_once_with({"event_id": "t1"})


@pytest.mark.anyio
async def test_wal_backend_close():
    writer = _make_wal_writer()
    backend = WALBackend(writer)
    await backend.close()
    writer.close.assert_called_once()
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_storage_router.py::test_wal_backend_write_execution_success -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'gateway.storage.wal_backend'`

**Step 3: Write WALBackend implementation**

Create `src/gateway/storage/wal_backend.py`:

```python
"""WALBackend — StorageBackend wrapping the local SQLite WAL writer."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gateway.wal.writer import WALWriter

logger = logging.getLogger(__name__)


class WALBackend:
    """StorageBackend implementation backed by local SQLite WAL."""

    name = "wal"

    def __init__(self, wal_writer: WALWriter) -> None:
        self._writer = wal_writer

    async def write_execution(self, record: dict) -> bool:
        try:
            self._writer.write_and_fsync(record)
            return True
        except Exception:
            logger.error(
                "WAL write_execution failed execution_id=%s",
                record.get("execution_id"),
                exc_info=True,
            )
            return False

    async def write_attempt(self, record: dict) -> None:
        try:
            self._writer.write_attempt(**record)
        except Exception:
            logger.warning(
                "WAL write_attempt failed request_id=%s",
                record.get("request_id"),
                exc_info=True,
            )

    async def write_tool_event(self, record: dict) -> None:
        try:
            self._writer.write_tool_event(record)
        except Exception:
            logger.warning(
                "WAL write_tool_event failed event_id=%s",
                record.get("event_id"),
                exc_info=True,
            )

    async def close(self) -> None:
        self._writer.close()
```

**Step 4: Run tests**

Run: `python -m pytest tests/unit/test_storage_router.py -v`
Expected: 13 passed

**Step 5: Commit**

```bash
git add src/gateway/storage/wal_backend.py tests/unit/test_storage_router.py
git commit -m "feat(storage): add WALBackend wrapping SQLite WAL writer"
```

---

### Task 3: WalacorBackend Implementation

**Files:**
- Create: `src/gateway/storage/walacor_backend.py`
- Modify: `tests/unit/test_storage_router.py` (add WalacorBackend tests)

**Step 1: Add failing tests for WalacorBackend**

Append to `tests/unit/test_storage_router.py`:

```python
from gateway.storage.walacor_backend import WalacorBackend


def _make_walacor_client() -> MagicMock:
    client = MagicMock()
    client.write_execution = AsyncMock()
    client.write_attempt = AsyncMock()
    client.write_tool_event = AsyncMock()
    client.close = AsyncMock()
    return client


@pytest.mark.anyio
async def test_walacor_backend_write_execution_success():
    client = _make_walacor_client()
    backend = WalacorBackend(client)
    assert backend.name == "walacor"
    ok = await backend.write_execution({"execution_id": "e1"})
    assert ok is True
    client.write_execution.assert_called_once_with({"execution_id": "e1"})


@pytest.mark.anyio
async def test_walacor_backend_write_execution_failure():
    client = _make_walacor_client()
    client.write_execution.side_effect = RuntimeError("Walacor 500")
    backend = WalacorBackend(client)
    ok = await backend.write_execution({"execution_id": "e2"})
    assert ok is False


@pytest.mark.anyio
async def test_walacor_backend_write_attempt():
    client = _make_walacor_client()
    backend = WalacorBackend(client)
    await backend.write_attempt({
        "request_id": "r1", "tenant_id": "t1", "path": "/v1/chat/completions",
        "disposition": "allowed", "status_code": 200,
    })
    client.write_attempt.assert_called_once_with(
        request_id="r1", tenant_id="t1", path="/v1/chat/completions",
        disposition="allowed", status_code=200,
    )


@pytest.mark.anyio
async def test_walacor_backend_write_tool_event():
    client = _make_walacor_client()
    backend = WalacorBackend(client)
    await backend.write_tool_event({"event_id": "t1"})
    client.write_tool_event.assert_called_once_with({"event_id": "t1"})


@pytest.mark.anyio
async def test_walacor_backend_close():
    client = _make_walacor_client()
    backend = WalacorBackend(client)
    await backend.close()
    client.close.assert_called_once()
```

**Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/test_storage_router.py::test_walacor_backend_write_execution_success -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'gateway.storage.walacor_backend'`

**Step 3: Write WalacorBackend implementation**

Create `src/gateway/storage/walacor_backend.py`:

```python
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
```

**Step 4: Run tests**

Run: `python -m pytest tests/unit/test_storage_router.py -v`
Expected: 18 passed

**Step 5: Update `__init__.py` exports and commit**

Update `src/gateway/storage/__init__.py`:

```python
"""Storage abstraction layer: pluggable backends with fan-out routing."""

from gateway.storage.backend import StorageBackend
from gateway.storage.router import StorageRouter, WriteResult
from gateway.storage.wal_backend import WALBackend
from gateway.storage.walacor_backend import WalacorBackend

__all__ = ["StorageBackend", "StorageRouter", "WriteResult", "WALBackend", "WalacorBackend"]
```

```bash
git add src/gateway/storage/ tests/unit/test_storage_router.py
git commit -m "feat(storage): add WalacorBackend and update exports"
```

---

### Task 4: Wire StorageRouter into PipelineContext + Startup

**Files:**
- Modify: `src/gateway/pipeline/context.py:20-69`
- Modify: `src/gateway/main.py` (after `_init_walacor` and `_init_wal`)

**Step 1: Add `storage` field to PipelineContext**

In `src/gateway/pipeline/context.py`, add to the TYPE_CHECKING imports:

```python
from gateway.storage.router import StorageRouter
```

Add after line 41 (`self.walacor_client`):

```python
        # Storage abstraction layer (fans out to WAL + Walacor)
        self.storage: StorageRouter | None = None
```

**Step 2: Initialize StorageRouter in `main.py`**

In `src/gateway/main.py`, add import at top:

```python
from gateway.storage import StorageRouter, WALBackend, WalacorBackend
```

After the existing `_init_walacor()` and `_init_wal()` calls in `on_startup()`, add a new helper:

```python
def _init_storage(ctx) -> None:
    """Build StorageRouter from available backends."""
    from gateway.storage import StorageRouter, WALBackend, WalacorBackend
    backends = []
    if ctx.wal_writer:
        backends.append(WALBackend(ctx.wal_writer))
    if ctx.walacor_client:
        backends.append(WalacorBackend(ctx.walacor_client))
    ctx.storage = StorageRouter(backends)
    logger.info("Storage router ready: backends=%s", [b.name for b in backends])
```

Call it in `on_startup()` right after both `_init_walacor()` and `_init_wal()` have run (but before governance init). Both the governance path and skip_governance path should call it.

**Step 3: Run full test suite**

Run: `python -m pytest tests/unit/ -x -q`
Expected: all pass (no call sites changed yet, just wiring)

**Step 4: Commit**

```bash
git add src/gateway/pipeline/context.py src/gateway/main.py
git commit -m "feat(storage): wire StorageRouter into PipelineContext and startup"
```

---

### Task 5: Replace orchestrator dual-write sites with `ctx.storage`

**Files:**
- Modify: `src/gateway/pipeline/orchestrator.py`

This is the largest task. There are 5 dual-write patterns to replace:

**Site 1: `_store_execution` (line ~747-765)**

Replace the entire function body:

```python
async def _store_execution(record, request: Request, ctx) -> None:
    """Write execution record via storage router, then tag request state."""
    eid = record["execution_id"]
    if ctx.storage:
        result = await ctx.storage.write_execution(record)
        if result.succeeded:
            execution_id_var.set(eid)
            request.state.walacor_execution_id = eid
```

**Site 2: `_after_stream_record` streaming background task (line ~850-863)**

Replace the Walacor+WAL dual-write block with:

```python
        if ctx.storage:
            result = await ctx.storage.write_execution(record)
            if result.succeeded:
                execution_id_var.set(record["execution_id"])
```

**Site 3: `_after_stream_skip_governance` (line ~906-910)**

Replace:

```python
        if ctx.storage:
            await ctx.storage.write_execution(record)
        execution_id_var.set(record["execution_id"])
```

**Site 4: Skip-governance non-streaming write (line ~1218-1226)**

Replace:

```python
        try:
            if ctx.storage:
                result = await ctx.storage.write_execution(record)
                if result.succeeded:
                    execution_id_var.set(record["execution_id"])
                    request.state.walacor_execution_id = record["execution_id"]
        except Exception as exc:
            logger.error("Skip-governance write_execution failed execution_id=%s: %s", record["execution_id"], exc)
```

**Site 5: `_write_tool_events` (line ~580-590)**

Replace the dual-write block:

```python
        if ctx.storage:
            await ctx.storage.write_tool_event(record)
```

After all replacements, remove the direct imports/references to `ctx.walacor_client.write_execution`, `ctx.walacor_client.write_tool_event`, `ctx.wal_writer.write_and_fsync`, and `ctx.wal_writer.write_tool_event` from the write paths. Keep any other references to `ctx.walacor_client` or `ctx.wal_writer` that are NOT writes (e.g., health checks, delivery worker).

**Step 1: Make the replacements**

Edit each site as described above.

**Step 2: Run full test suite**

Run: `python -m pytest tests/unit/ -x -q`
Expected: all pass

**Step 3: Commit**

```bash
git add src/gateway/pipeline/orchestrator.py
git commit -m "refactor(storage): replace orchestrator dual-write sites with ctx.storage"
```

---

### Task 6: Replace completeness middleware dual-write with `ctx.storage`

**Files:**
- Modify: `src/gateway/middleware/completeness.py`

**Step 1: Replace the dual-write block**

Replace lines 37-74 (the entire `if settings.completeness_enabled` block) with:

```python
        if settings.completeness_enabled and ctx.storage:
            disposition = getattr(request.state, "walacor_disposition", disposition_var.get())
            status_code = response.status_code if response is not None else 500
            tenant_id = settings.gateway_tenant_id or ""
            provider = getattr(request.state, "walacor_provider", provider_var.get())
            model_id = getattr(request.state, "walacor_model_id", model_id_var.get())
            execution_id = getattr(request.state, "walacor_execution_id", execution_id_var.get())
            user_id = getattr(request.state, "walacor_user_id", None)
            try:
                await ctx.storage.write_attempt({
                    "request_id": rid,
                    "tenant_id": tenant_id,
                    "path": request.url.path,
                    "disposition": disposition,
                    "status_code": status_code,
                    "provider": provider,
                    "model_id": model_id,
                    "execution_id": execution_id,
                    "user": user_id,
                })
                gateway_attempts_total.labels(disposition=disposition).inc()
            except Exception as e:
                logger.warning("Failed to write gateway_attempt: %s", e)
```

Note: the condition changes from `ctx.wal_writer or ctx.walacor_client` to `ctx.storage`. The `gateway_attempts_total` metric increment stays.

**Step 2: Run full test suite**

Run: `python -m pytest tests/unit/ -x -q`
Expected: all pass

**Step 3: Commit**

```bash
git add src/gateway/middleware/completeness.py
git commit -m "refactor(storage): replace completeness middleware dual-write with ctx.storage"
```

---

### Task 7: Full regression test + cleanup

**Files:**
- Verify: all existing tests pass
- Verify: no remaining direct write calls to `ctx.walacor_client.write_execution/write_tool_event` or `ctx.wal_writer.write_and_fsync/write_tool_event` in the pipeline path

**Step 1: Run full test suite**

Run: `python -m pytest tests/unit/ -x -q`
Expected: all pass (447+ tests)

**Step 2: Verify no remaining dual-write patterns**

Run: `grep -rn "ctx\.walacor_client\.write_\|ctx\.wal_writer\.write_and_fsync\|ctx\.wal_writer\.write_tool_event" src/gateway/pipeline/ src/gateway/middleware/`

Expected: no matches (all write call sites should go through `ctx.storage` now). Note: `ctx.wal_writer` may still appear in `main.py` (self-test, delivery worker) and health checks — that's fine.

**Step 3: Run grep to confirm main.py self-test still uses wal_writer directly**

Run: `grep -n "ctx\.wal_writer" src/gateway/main.py`

Expected: only self-test (`write_and_fsync` + `mark_delivered`), delivery worker init, and shutdown. These are intentionally NOT routed through StorageRouter.

**Step 4: Commit any cleanup**

```bash
git add -A
git commit -m "chore(storage): verify no remaining dual-write patterns in pipeline"
```
