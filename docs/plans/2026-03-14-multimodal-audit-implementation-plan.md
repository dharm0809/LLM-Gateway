# Multimodal Audit Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Track, classify, and audit every piece of data (text, images, documents) that enters the model — storing metadata and cryptographic proof only, never file bytes.

**Architecture:** Three new gateway components plug into the existing request pipeline before the orchestrator. C2 (attachment tracker) extracts file/image metadata from requests and correlates with OpenWebUI webhook notifications. C1 (image safety) sends images to LlamaGuard Vision for S1-S14 classification. C4 (image OCR) extracts text from images via Tesseract and runs existing PII/toxicity analyzers on it. All components are opt-in, fail-open, and store results in the execution record.

**Tech Stack:** Python 3.12, pytest, hashlib (SHA3-512), pytesseract, Pillow, Ollama API, OpenWebUI Pipeline SDK

**Design doc:** `docs/plans/2026-03-14-multimodal-audit-design.md`

---

## Phase C2: Document/File Tracking (Tasks 1–6)

---

### Task 1: Attachment Notification Cache

**Files:**
- Create: `src/gateway/middleware/attachment_tracker.py`
- Test: `tests/unit/test_attachment_tracker.py`

**Step 1: Write the failing test**

```python
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
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_attachment_tracker.py -v`
Expected: FAIL with "ModuleNotFoundError" or "cannot import name"

**Step 3: Write minimal implementation**

```python
"""Attachment tracking: notification cache + request body image/file extraction."""

from __future__ import annotations

import base64
import hashlib
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
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_attachment_tracker.py -v`
Expected: PASS (5 tests)

**Step 5: Commit**

```bash
git add src/gateway/middleware/attachment_tracker.py tests/unit/test_attachment_tracker.py
git commit -m "feat: attachment notification cache with TTL and bounded eviction"
```

---

### Task 2: Image Extraction from Request Body

**Files:**
- Modify: `src/gateway/middleware/attachment_tracker.py`
- Test: `tests/unit/test_attachment_tracker.py`

**Step 1: Write the failing test**

```python
def test_extract_images_from_messages():
    """Extract base64 images from OpenAI-format message content blocks."""
    from gateway.middleware.attachment_tracker import extract_images_from_messages

    messages = [
        {"role": "user", "content": [
            {"type": "text", "text": "What is this?"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgo="}},
        ]},
    ]
    images = extract_images_from_messages(messages)
    assert len(images) == 1
    assert images[0]["index"] == 0
    assert images[0]["mimetype"] == "image/png"
    assert isinstance(images[0]["raw_bytes"], bytes)
    assert len(images[0]["hash_sha3_512"]) == 128


def test_extract_images_no_images():
    """Text-only messages return empty list."""
    from gateway.middleware.attachment_tracker import extract_images_from_messages

    messages = [{"role": "user", "content": "Hello world"}]
    assert extract_images_from_messages(messages) == []


def test_extract_images_url_reference_skipped():
    """URL references (not base64) are logged but not extracted."""
    from gateway.middleware.attachment_tracker import extract_images_from_messages

    messages = [
        {"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "https://example.com/photo.jpg"}},
        ]},
    ]
    images = extract_images_from_messages(messages)
    assert len(images) == 0


def test_extract_images_multiple():
    """Multiple images across messages are all extracted."""
    from gateway.middleware.attachment_tracker import extract_images_from_messages

    b64 = base64.b64encode(b"fake_png_data").decode()
    messages = [
        {"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
        ]},
    ]
    images = extract_images_from_messages(messages)
    assert len(images) == 2
    assert images[0]["mimetype"] == "image/png"
    assert images[1]["mimetype"] == "image/jpeg"
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_attachment_tracker.py::test_extract_images_from_messages -v`
Expected: FAIL with "cannot import name 'extract_images_from_messages'"

**Step 3: Write minimal implementation**

Add to `src/gateway/middleware/attachment_tracker.py`:

```python
def extract_images_from_messages(messages: list[dict]) -> list[dict[str, Any]]:
    """Extract base64-encoded images from OpenAI-format message content blocks.

    Returns list of dicts: {index, mimetype, raw_bytes, hash_sha3_512, size_bytes}.
    URL references (non-base64) are skipped.
    """
    images: list[dict[str, Any]] = []
    idx = 0
    for msg in messages:
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") not in ("image_url", "image"):
                continue
            url_obj = block.get("image_url") or block
            url = url_obj.get("url", "") if isinstance(url_obj, dict) else ""
            if not url.startswith("data:"):
                logger.debug("Skipping non-base64 image URL: %.60s...", url)
                continue
            try:
                header, b64_data = url.split(",", 1)
                mimetype = header.split(";")[0].replace("data:", "")
                raw_bytes = base64.b64decode(b64_data)
                file_hash = hashlib.sha3_512(raw_bytes).hexdigest()
                images.append({
                    "index": idx,
                    "mimetype": mimetype,
                    "raw_bytes": raw_bytes,
                    "size_bytes": len(raw_bytes),
                    "hash_sha3_512": file_hash,
                })
                idx += 1
            except Exception:
                logger.warning("Failed to decode base64 image at index %d", idx, exc_info=True)
    return images
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_attachment_tracker.py -v`
Expected: PASS (9 tests)

**Step 5: Commit**

```bash
git add src/gateway/middleware/attachment_tracker.py tests/unit/test_attachment_tracker.py
git commit -m "feat: extract and hash base64 images from request messages"
```

---

### Task 3: OpenWebUI Metadata Extraction

**Files:**
- Modify: `src/gateway/middleware/attachment_tracker.py`
- Test: `tests/unit/test_attachment_tracker.py`

**Step 1: Write the failing test**

```python
def test_extract_openwebui_file_metadata():
    """Extract file metadata from OpenWebUI's metadata.files field."""
    from gateway.middleware.attachment_tracker import extract_openwebui_files

    body = {
        "model": "qwen3:8b",
        "messages": [{"role": "user", "content": "Summarize this doc"}],
        "metadata": {
            "files": [
                {"id": "f1", "filename": "report.pdf", "type": "application/pdf", "size": 50000},
                {"id": "f2", "filename": "data.csv", "type": "text/csv", "size": 1200},
            ]
        },
    }
    files = extract_openwebui_files(body)
    assert len(files) == 2
    assert files[0]["filename"] == "report.pdf"
    assert files[0]["mimetype"] == "application/pdf"
    assert files[0]["size_bytes"] == 50000
    assert files[0]["source"] == "openwebui_upload"


def test_extract_openwebui_files_no_metadata():
    """Body without metadata.files returns empty list."""
    from gateway.middleware.attachment_tracker import extract_openwebui_files

    body = {"model": "qwen3:8b", "messages": []}
    assert extract_openwebui_files(body) == []


def test_extract_openwebui_files_empty_list():
    """Empty files list returns empty."""
    from gateway.middleware.attachment_tracker import extract_openwebui_files

    body = {"metadata": {"files": []}}
    assert extract_openwebui_files(body) == []
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_attachment_tracker.py::test_extract_openwebui_file_metadata -v`
Expected: FAIL

**Step 3: Write minimal implementation**

Add to `src/gateway/middleware/attachment_tracker.py`:

```python
def extract_openwebui_files(body: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract file metadata from OpenWebUI's metadata.files field.

    Returns list of dicts: {filename, mimetype, size_bytes, source, file_id}.
    """
    metadata = body.get("metadata")
    if not isinstance(metadata, dict):
        return []
    files_list = metadata.get("files")
    if not isinstance(files_list, list):
        return []
    result = []
    for f in files_list:
        if not isinstance(f, dict):
            continue
        result.append({
            "filename": f.get("filename", f.get("name", "unknown")),
            "mimetype": f.get("type", f.get("mime_type", "application/octet-stream")),
            "size_bytes": f.get("size", 0),
            "source": "openwebui_upload",
            "file_id": f.get("id", ""),
        })
    return result
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_attachment_tracker.py -v`
Expected: PASS (12 tests)

**Step 5: Commit**

```bash
git add src/gateway/middleware/attachment_tracker.py tests/unit/test_attachment_tracker.py
git commit -m "feat: extract file metadata from OpenWebUI request body"
```

---

### Task 4: Notification Webhook Endpoint

**Files:**
- Modify: `src/gateway/middleware/attachment_tracker.py` (add route handler)
- Modify: `src/gateway/main.py` (register route + init cache)
- Modify: `src/gateway/pipeline/context.py` (add attachment_cache field)
- Modify: `src/gateway/middleware/completeness.py` (skip path)
- Modify: `src/gateway/config.py` (add config field)
- Test: `tests/unit/test_attachment_tracker.py`

**Step 1: Write the failing test**

```python
import pytest
from unittest.mock import MagicMock


@pytest.fixture(params=["asyncio"])
def anyio_backend(request):
    return request.param


@pytest.mark.anyio
async def test_attachment_notify_endpoint():
    """POST /v1/attachments/notify stores metadata in cache."""
    from gateway.middleware.attachment_tracker import (
        attachment_notify_handler,
        AttachmentNotificationCache,
    )
    from starlette.testclient import TestClient
    from starlette.requests import Request

    cache = AttachmentNotificationCache(max_size=100, ttl_seconds=3600)

    body = {
        "filename": "contract.pdf",
        "mimetype": "application/pdf",
        "size_bytes": 245000,
        "hash_sha3_512": "abc" * 42 + "ab",
        "chat_id": "chat-123",
        "user_id": "user-1",
        "user_email": "user@test.com",
        "upload_timestamp": "2026-03-14T12:00:00Z",
    }

    # Build a mock request
    import json
    from starlette.requests import Request as StarletteRequest
    from starlette.datastructures import Headers

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/v1/attachments/notify",
        "headers": [(b"content-type", b"application/json")],
    }
    request = StarletteRequest(scope, receive=None)
    request._body = json.dumps(body).encode()

    response = await attachment_notify_handler(request, cache)

    assert response.status_code == 200
    stored = cache.get("abc" * 42 + "ab")
    assert stored is not None
    assert stored["filename"] == "contract.pdf"
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_attachment_tracker.py::test_attachment_notify_endpoint -v`
Expected: FAIL

**Step 3: Write minimal implementation**

Add to `src/gateway/middleware/attachment_tracker.py`:

```python
from starlette.requests import Request
from starlette.responses import JSONResponse


async def attachment_notify_handler(request: Request, cache: AttachmentNotificationCache) -> JSONResponse:
    """Handle POST /v1/attachments/notify from OpenWebUI pipeline plugin."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    file_hash = body.get("hash_sha3_512")
    if not file_hash:
        return JSONResponse({"error": "Missing hash_sha3_512"}, status_code=400)

    cache.store(body)
    logger.info("Attachment notification stored: filename=%s hash=%.16s...", body.get("filename", "?"), file_hash)
    return JSONResponse({"stored": True})
```

Add config field to `src/gateway/config.py` (near line 109, after `presidio_pii_enabled`):

```python
    attachment_tracking_enabled: bool = Field(default=True, description="Track file/image metadata in execution records")
```

Add to `src/gateway/pipeline/context.py` (in PipelineContext class):

```python
    attachment_cache: Any = None  # AttachmentNotificationCache instance
```

Add skip path to `src/gateway/middleware/completeness.py` line 27: add `"/v1/attachments"` to the skip tuple.

Add skip path to `src/gateway/main.py` `api_key_middleware` line 135: do NOT add — this endpoint REQUIRES auth (keep it protected).

Register route in `src/gateway/main.py` `create_app()` — add route for the endpoint, wiring it to the cache from context.

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_attachment_tracker.py -v`
Expected: PASS (13 tests)

**Step 5: Commit**

```bash
git add src/gateway/middleware/attachment_tracker.py src/gateway/config.py src/gateway/pipeline/context.py src/gateway/middleware/completeness.py src/gateway/main.py tests/unit/test_attachment_tracker.py
git commit -m "feat: POST /v1/attachments/notify webhook endpoint for OpenWebUI"
```

---

### Task 5: Wire Attachment Tracker into Orchestrator

**Files:**
- Modify: `src/gateway/pipeline/orchestrator.py` (read request.state.file_metadata, add to record)
- Modify: `src/gateway/pipeline/hasher.py` (add file_metadata and image_analysis params)
- Test: `tests/unit/test_attachment_tracker.py`

**Step 1: Write the failing test**

```python
def test_build_execution_record_with_file_metadata():
    """Execution record includes file_metadata when present."""
    from gateway.pipeline.hasher import build_execution_record
    from unittest.mock import MagicMock

    call = MagicMock()
    call.prompt_text = "summarize this"
    call.model_id = "qwen3:8b"
    call.metadata = {}
    resp = MagicMock()
    resp.content = "Here is a summary"
    resp.thinking_content = None
    resp.provider_request_id = "req-1"
    resp.usage = {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}

    file_metadata = [{"filename": "doc.pdf", "hash_sha3_512": "abc123", "mimetype": "application/pdf", "size_bytes": 5000, "source": "openwebui_upload"}]

    record = build_execution_record(
        call=call,
        model_response=resp,
        attestation_id="att-1",
        policy_version=1,
        policy_result="pass",
        tenant_id="t1",
        gateway_id="gw-1",
        file_metadata=file_metadata,
    )
    assert record["file_metadata"] == file_metadata
    assert record["image_analysis"] == []
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_attachment_tracker.py::test_build_execution_record_with_file_metadata -v`
Expected: FAIL with "unexpected keyword argument 'file_metadata'"

**Step 3: Write minimal implementation**

Modify `src/gateway/pipeline/hasher.py` `build_execution_record()`:
- Add params: `file_metadata: list[dict] | None = None`, `image_analysis: list[dict] | None = None`
- Add to returned dict: `"file_metadata": file_metadata or []`, `"image_analysis": image_analysis or []`

Modify `src/gateway/pipeline/orchestrator.py`:
- In `_build_and_write_record()` / the record-building section: read `getattr(request.state, "file_metadata", None)` and `getattr(request.state, "image_analysis", None)`, pass to `build_execution_record()`

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_attachment_tracker.py -v`
Expected: PASS (14 tests)

**Step 5: Commit**

```bash
git add src/gateway/pipeline/hasher.py src/gateway/pipeline/orchestrator.py tests/unit/test_attachment_tracker.py
git commit -m "feat: wire file_metadata and image_analysis into execution records"
```

---

### Task 6: Lineage Attachments Endpoint

**Files:**
- Modify: `src/gateway/lineage/reader.py` (add get_attachments method)
- Modify: `src/gateway/lineage/api.py` (add route handler)
- Modify: `src/gateway/main.py` (register route)
- Test: `tests/unit/test_attachment_tracker.py`

**Step 1: Write the failing test**

```python
def test_lineage_reader_get_attachments(tmp_path):
    """LineageReader.get_attachments extracts file_metadata from execution records."""
    import json
    import sqlite3

    db_path = str(tmp_path / "wal.db")
    conn = sqlite3.connect(db_path)
    conn.execute("""CREATE TABLE wal_records (
        execution_id TEXT PRIMARY KEY, record_json TEXT NOT NULL,
        created_at TEXT NOT NULL, delivered INTEGER NOT NULL DEFAULT 0, delivered_at TEXT)""")

    record = {
        "execution_id": "exec-1",
        "session_id": "sess-1",
        "file_metadata": [{"filename": "test.pdf", "hash_sha3_512": "abc", "mimetype": "application/pdf", "size_bytes": 1000}],
        "image_analysis": [],
    }
    conn.execute("INSERT INTO wal_records VALUES (?, ?, ?, 0, NULL)", ("exec-1", json.dumps(record), "2026-03-14T00:00:00Z"))
    conn.commit()
    conn.close()

    from gateway.lineage.reader import LineageReader
    reader = LineageReader(db_path)
    result = reader.get_attachments("sess-1")
    assert len(result) == 1
    assert result[0]["filename"] == "test.pdf"
    reader.close()
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_attachment_tracker.py::test_lineage_reader_get_attachments -v`
Expected: FAIL with "has no attribute 'get_attachments'"

**Step 3: Write minimal implementation**

Add to `src/gateway/lineage/reader.py`:

```python
def get_attachments(self, session_id: str) -> list[dict]:
    """Get all file_metadata entries from execution records in a session."""
    rows = self._conn.execute(
        "SELECT record_json FROM wal_records WHERE json_extract(record_json, '$.session_id') = ?",
        (session_id,),
    ).fetchall()
    attachments = []
    for (record_json,) in rows:
        record = json.loads(record_json)
        for fm in record.get("file_metadata", []):
            fm["execution_id"] = record.get("execution_id", "")
            attachments.append(fm)
    return attachments
```

Add handler to `src/gateway/lineage/api.py`:

```python
async def lineage_attachments(request: Request) -> JSONResponse:
    """GET /v1/lineage/attachments?session_id=X — file metadata for a session."""
    session_id = request.query_params.get("session_id", "")
    if not session_id:
        return JSONResponse({"error": "session_id query parameter required"}, status_code=400)
    ctx = get_pipeline_context()
    if not ctx.lineage_reader:
        return JSONResponse({"error": "Lineage not enabled"}, status_code=503)
    attachments = ctx.lineage_reader.get_attachments(session_id)
    return JSONResponse({"session_id": session_id, "attachments": attachments})
```

Register route in `src/gateway/main.py` `create_app()`.

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_attachment_tracker.py -v`
Expected: PASS (15 tests)

**Step 5: Commit**

```bash
git add src/gateway/lineage/reader.py src/gateway/lineage/api.py src/gateway/main.py tests/unit/test_attachment_tracker.py
git commit -m "feat: GET /v1/lineage/attachments endpoint for file metadata queries"
```

---

## Phase C1: Image Safety Classification (Tasks 7–10)

---

### Task 7: ImageSafetyAnalyzer Core

**Files:**
- Create: `src/gateway/content/image_safety.py`
- Test: `tests/unit/test_image_safety.py`

**Step 1: Write the failing test**

```python
"""Unit tests for image safety analysis via LlamaGuard Vision."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.fixture(params=["asyncio"])
def anyio_backend(request):
    return request.param


@pytest.mark.anyio
async def test_image_safety_safe_image():
    """Safe image returns PASS verdict."""
    from gateway.content.image_safety import ImageSafetyAnalyzer

    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"message": {"content": "safe"}}
    mock_client.post.return_value = mock_resp

    analyzer = ImageSafetyAnalyzer(
        ollama_url="http://localhost:11434",
        model="llama-guard3-vision:11b",
        timeout_ms=10000,
        http_client=mock_client,
    )

    decision = await analyzer.analyze_image(b"fake_image_bytes", "image/png")
    assert decision.verdict.value == "pass"


@pytest.mark.anyio
async def test_image_safety_unsafe_s4():
    """S4 child_safety returns BLOCK verdict."""
    from gateway.content.image_safety import ImageSafetyAnalyzer

    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"message": {"content": "unsafe\nS4"}}
    mock_client.post.return_value = mock_resp

    analyzer = ImageSafetyAnalyzer(
        ollama_url="http://localhost:11434",
        model="llama-guard3-vision:11b",
        timeout_ms=10000,
        http_client=mock_client,
    )

    decision = await analyzer.analyze_image(b"fake_image_bytes", "image/png")
    assert decision.verdict.value == "block"
    assert decision.category == "child_safety"


@pytest.mark.anyio
async def test_image_safety_unsafe_other():
    """Non-S4 unsafe returns BLOCK verdict with correct category."""
    from gateway.content.image_safety import ImageSafetyAnalyzer

    mock_client = AsyncMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"message": {"content": "unsafe\nS1"}}
    mock_client.post.return_value = mock_resp

    analyzer = ImageSafetyAnalyzer(
        ollama_url="http://localhost:11434",
        model="llama-guard3-vision:11b",
        timeout_ms=10000,
        http_client=mock_client,
    )

    decision = await analyzer.analyze_image(b"fake_image_bytes", "image/png")
    assert decision.verdict.value == "block"
    assert decision.category == "violent_crimes"


@pytest.mark.anyio
async def test_image_safety_timeout_fail_open():
    """Timeout returns PASS with confidence=0.0 (fail-open)."""
    from gateway.content.image_safety import ImageSafetyAnalyzer
    import httpx

    mock_client = AsyncMock()
    mock_client.post.side_effect = httpx.TimeoutException("timeout")

    analyzer = ImageSafetyAnalyzer(
        ollama_url="http://localhost:11434",
        model="llama-guard3-vision:11b",
        timeout_ms=10000,
        http_client=mock_client,
    )

    decision = await analyzer.analyze_image(b"fake_image_bytes", "image/png")
    assert decision.verdict.value == "pass"
    assert decision.confidence == 0.0


@pytest.mark.anyio
async def test_image_safety_connection_error_fail_open():
    """Connection error returns PASS with confidence=0.0."""
    from gateway.content.image_safety import ImageSafetyAnalyzer
    import httpx

    mock_client = AsyncMock()
    mock_client.post.side_effect = httpx.ConnectError("refused")

    analyzer = ImageSafetyAnalyzer(
        ollama_url="http://localhost:11434",
        model="llama-guard3-vision:11b",
        timeout_ms=10000,
        http_client=mock_client,
    )

    decision = await analyzer.analyze_image(b"fake_image_bytes", "image/png")
    assert decision.verdict.value == "pass"
    assert decision.confidence == 0.0


def test_image_safety_analyzer_id():
    """Analyzer ID is stable."""
    from gateway.content.image_safety import ImageSafetyAnalyzer

    analyzer = ImageSafetyAnalyzer(
        ollama_url="http://localhost:11434",
        model="llama-guard3-vision:11b",
        timeout_ms=10000,
    )
    assert analyzer.analyzer_id == "walacor.image_safety.v1"
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_image_safety.py -v`
Expected: FAIL with "ModuleNotFoundError"

**Step 3: Write minimal implementation**

Create `src/gateway/content/image_safety.py`:

```python
"""Image safety analysis via LlamaGuard Vision.

Sends images to LlamaGuard 3 Vision via Ollama /api/chat for S1-S14 classification.
Fail-open: returns PASS with confidence=0.0 on timeout, connection error, or model unavailability.
"""

from __future__ import annotations

import base64
import logging
from typing import Any

from gateway.content.base import ContentAnalyzer, Decision, Verdict

logger = logging.getLogger(__name__)

# Same category map as text LlamaGuard — see llama_guard.py
_CATEGORY_MAP: dict[str, str] = {
    "S1": "violent_crimes", "S2": "non_violent_crimes", "S3": "sex_related_crimes",
    "S4": "child_safety", "S5": "defamation", "S6": "specialized_advice",
    "S7": "privacy", "S8": "intellectual_property", "S9": "indiscriminate_weapons",
    "S10": "hate", "S11": "suicide_self_harm", "S12": "sexual_content",
    "S13": "elections", "S14": "code_interpreter_abuse",
}


class ImageSafetyAnalyzer:
    """Classify images using LlamaGuard 3 Vision via Ollama."""

    def __init__(
        self,
        ollama_url: str,
        model: str = "llama-guard3-vision:11b",
        timeout_ms: int = 10000,
        http_client: Any = None,
    ):
        self._ollama_url = ollama_url.rstrip("/")
        self._model = model
        self._timeout_ms = timeout_ms
        self._http_client = http_client

    @property
    def analyzer_id(self) -> str:
        return "walacor.image_safety.v1"

    @property
    def timeout_ms(self) -> int:
        return self._timeout_ms

    async def analyze_image(self, image_bytes: bytes, mimetype: str = "image/png") -> Decision:
        """Classify a single image for safety. Returns Decision."""
        if self._http_client is None:
            return Decision(verdict=Verdict.PASS, confidence=0.0, analyzer_id=self.analyzer_id, category="image_safety", reason="no_http_client")

        b64_image = base64.b64encode(image_bytes).decode("ascii")

        payload = {
            "model": self._model,
            "messages": [
                {
                    "role": "user",
                    "content": "Analyze this image for safety.",
                    "images": [b64_image],
                }
            ],
            "stream": False,
        }

        try:
            resp = await self._http_client.post(
                f"{self._ollama_url}/api/chat",
                json=payload,
                timeout=self._timeout_ms / 1000.0,
            )
            if resp.status_code != 200:
                logger.warning("Image safety model returned %d", resp.status_code)
                return Decision(verdict=Verdict.PASS, confidence=0.0, analyzer_id=self.analyzer_id, category="image_safety", reason=f"model_error_{resp.status_code}")

            body = resp.json()
            response_text = body.get("message", {}).get("content", "").strip().lower()
            return self._parse_response(response_text)

        except Exception as e:
            logger.warning("Image safety analysis failed (fail-open): %s", e)
            return Decision(verdict=Verdict.PASS, confidence=0.0, analyzer_id=self.analyzer_id, category="image_safety", reason="analyzer_unavailable")

    def _parse_response(self, response_text: str) -> Decision:
        """Parse LlamaGuard response: 'safe' or 'unsafe\\nS4'."""
        if response_text.startswith("safe"):
            return Decision(verdict=Verdict.PASS, confidence=0.95, analyzer_id=self.analyzer_id, category="image_safety", reason="safe")

        # Parse "unsafe\nS4" format
        lines = response_text.split("\n")
        categories = []
        for line in lines[1:]:
            for token in line.replace(",", " ").split():
                token = token.strip().upper()
                if token in _CATEGORY_MAP:
                    categories.append(token)

        category_name = _CATEGORY_MAP.get(categories[0], "unknown") if categories else "unknown"

        return Decision(
            verdict=Verdict.BLOCK,
            confidence=0.95,
            analyzer_id=self.analyzer_id,
            category=category_name,
            reason=f"unsafe: {','.join(categories)}" if categories else "unsafe",
        )
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_image_safety.py -v`
Expected: PASS (6 tests)

**Step 5: Commit**

```bash
git add src/gateway/content/image_safety.py tests/unit/test_image_safety.py
git commit -m "feat: ImageSafetyAnalyzer — LlamaGuard Vision for image classification"
```

---

### Task 8: Image Safety Config + Initialization

**Files:**
- Modify: `src/gateway/config.py` (add 4 config fields)
- Modify: `src/gateway/main.py` (add `_init_image_safety()`)
- Modify: `src/gateway/pipeline/context.py` (add `image_safety_analyzer` field)

**Step 1: Add config fields**

Add to `src/gateway/config.py` after the `presidio_pii_enabled` field (around line 107):

```python
    image_safety_enabled: bool = Field(default=False, description="Enable LlamaGuard Vision image safety classification")
    image_safety_model: str = Field(default="llama-guard3-vision:11b", description="Ollama model for image safety")
    image_safety_timeout_ms: int = Field(default=10000, description="Image safety classification timeout in ms")
    image_safety_max_images: int = Field(default=5, description="Max images to analyze per request (skip if exceeded)")
    image_ocr_enabled: bool = Field(default=False, description="Enable Tesseract OCR + PII detection on images")
    image_ocr_max_size_mb: int = Field(default=10, description="Skip OCR for images larger than this (MB)")
```

**Step 2: Add context field**

Add to `src/gateway/pipeline/context.py` PipelineContext:

```python
    image_safety_analyzer: Any = None  # ImageSafetyAnalyzer instance
```

**Step 3: Add init function in main.py**

Add `_init_image_safety()` near the other analyzer init functions (around line 787):

```python
def _init_image_safety(settings, ctx):
    """Initialize image safety analyzer if enabled."""
    from gateway.content.image_safety import ImageSafetyAnalyzer
    ollama_url = settings.llama_guard_ollama_url or settings.provider_ollama_url or "http://localhost:11434"
    ctx.image_safety_analyzer = ImageSafetyAnalyzer(
        ollama_url=ollama_url,
        model=settings.image_safety_model,
        timeout_ms=settings.image_safety_timeout_ms,
        http_client=ctx.http_client,
    )
    logger.info("Image safety analyzer enabled: model=%s timeout=%dms", settings.image_safety_model, settings.image_safety_timeout_ms)
```

Call it in `on_startup()` after Presidio init:

```python
if settings.image_safety_enabled:
    _init_image_safety(settings, ctx)
```

**Step 4: Run full test suite**

Run: `python -m pytest tests/ -v --tb=short`
Expected: All pass, no regressions

**Step 5: Commit**

```bash
git add src/gateway/config.py src/gateway/pipeline/context.py src/gateway/main.py
git commit -m "feat: image safety config fields and startup initialization"
```

---

### Task 9: Wire Image Safety into Request Pipeline

**Files:**
- Modify: `src/gateway/pipeline/orchestrator.py`
- Test: `tests/unit/test_image_safety.py`

**Step 1: Write the failing test**

```python
@pytest.mark.anyio
async def test_image_safety_block_returns_403():
    """When image safety returns BLOCK, gateway returns 403 with reason."""
    from gateway.content.image_safety import ImageSafetyAnalyzer, evaluate_image_safety
    from gateway.content.base import Decision, Verdict

    mock_analyzer = MagicMock()
    mock_analyzer.analyze_image = AsyncMock(return_value=Decision(
        verdict=Verdict.BLOCK,
        confidence=0.95,
        analyzer_id="walacor.image_safety.v1",
        category="child_safety",
        reason="unsafe: S4",
    ))

    images = [{"index": 0, "raw_bytes": b"fake", "mimetype": "image/png", "hash_sha3_512": "abc", "size_bytes": 4}]

    blocked, response, analysis = await evaluate_image_safety(mock_analyzer, images, max_images=5)
    assert blocked is True
    assert response is not None
    assert response.status_code == 403
    body = json.loads(response.body.decode())
    assert "child_safety" in body["error"]
    assert analysis[0]["safety_verdict"] == "block"
    assert analysis[0]["safety_category"] == "child_safety"


@pytest.mark.anyio
async def test_image_safety_pass_continues():
    """When image safety returns PASS, no block."""
    from gateway.content.image_safety import evaluate_image_safety
    from gateway.content.base import Decision, Verdict

    mock_analyzer = MagicMock()
    mock_analyzer.analyze_image = AsyncMock(return_value=Decision(
        verdict=Verdict.PASS,
        confidence=0.95,
        analyzer_id="walacor.image_safety.v1",
        category="image_safety",
        reason="safe",
    ))

    images = [{"index": 0, "raw_bytes": b"fake", "mimetype": "image/png", "hash_sha3_512": "abc", "size_bytes": 4}]

    blocked, response, analysis = await evaluate_image_safety(mock_analyzer, images, max_images=5)
    assert blocked is False
    assert response is None
    assert analysis[0]["safety_verdict"] == "pass"
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_image_safety.py::test_image_safety_block_returns_403 -v`
Expected: FAIL with "cannot import name 'evaluate_image_safety'"

**Step 3: Write minimal implementation**

Add to `src/gateway/content/image_safety.py`:

```python
async def evaluate_image_safety(
    analyzer: ImageSafetyAnalyzer,
    images: list[dict[str, Any]],
    max_images: int = 5,
) -> tuple[bool, JSONResponse | None, list[dict[str, Any]]]:
    """Run image safety on extracted images.

    Returns (is_blocked, error_response_or_None, image_analysis_results).
    """
    from starlette.responses import JSONResponse

    analysis_results: list[dict[str, Any]] = []

    if len(images) > max_images:
        logger.warning("Too many images (%d > %d), skipping image safety", len(images), max_images)
        for img in images:
            analysis_results.append({
                "image_index": img["index"],
                "hash_sha3_512": img["hash_sha3_512"],
                "safety_verdict": "skip",
                "safety_category": None,
                "safety_reason": f"exceeded_max_images_{max_images}",
            })
        return False, None, analysis_results

    for img in images:
        decision = await analyzer.analyze_image(img["raw_bytes"], img.get("mimetype", "image/png"))

        result = {
            "image_index": img["index"],
            "hash_sha3_512": img["hash_sha3_512"],
            "safety_verdict": decision.verdict.value,
            "safety_category": decision.category if decision.verdict != Verdict.PASS else None,
            "safety_reason": decision.reason,
        }
        analysis_results.append(result)

        if decision.verdict == Verdict.BLOCK:
            logger.critical(
                "IMAGE SAFETY BLOCK: category=%s hash=%.16s...",
                decision.category, img["hash_sha3_512"],
            )
            error_body = {
                "error": f"Request blocked: image content violates safety policy ({decision.category})",
                "category": decision.category,
            }
            return True, JSONResponse(error_body, status_code=403), analysis_results

    return False, None, analysis_results
```

Wire into orchestrator: in `handle_request()`, after body is read and before adapter parse, check for images and run safety analysis. If blocked, return 403 response and write execution record with denial.

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_image_safety.py -v`
Expected: PASS (8 tests)

**Step 5: Commit**

```bash
git add src/gateway/content/image_safety.py src/gateway/pipeline/orchestrator.py tests/unit/test_image_safety.py
git commit -m "feat: wire image safety into request pipeline — BLOCK returns 403 with reason"
```

---

### Task 10: OpenWebUI Pipeline Plugin

**Files:**
- Create: `plugins/openwebui/attachment_notifier.py`

**Step 1: Write the plugin**

```python
"""OpenWebUI Pipeline Plugin: Attachment Notifier.

Sends file upload metadata to the Walacor Gateway webhook endpoint
when users upload files in OpenWebUI chats.

Install: copy to OpenWebUI's pipelines directory or upload via admin UI.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any

import requests
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class Pipeline:
    """OpenWebUI Filter pipeline that notifies the gateway about file uploads."""

    class Valves(BaseModel):
        priority: int = Field(default=0, description="Pipeline priority (lower = first)")
        gateway_url: str = Field(default="http://localhost:8000", description="Walacor Gateway base URL")
        gateway_api_key: str = Field(default="", description="Gateway API key for webhook auth")
        enabled: bool = Field(default=True, description="Enable attachment notifications")

    def __init__(self):
        self.name = "Walacor Attachment Notifier"
        self.valves = self.Valves()

    def inlet(self, body: dict, __user__: dict | None = None) -> dict:
        """Pre-request hook: detect file references and notify gateway."""
        if not self.valves.enabled:
            return body

        user_info = __user__ or {}
        user_id = user_info.get("id", "")
        user_email = user_info.get("email", "")
        chat_id = body.get("metadata", {}).get("chat_id", "")

        # Check for files in metadata
        files = body.get("metadata", {}).get("files", [])
        for f in files:
            self._notify_gateway(
                filename=f.get("filename", f.get("name", "unknown")),
                mimetype=f.get("type", "application/octet-stream"),
                size_bytes=f.get("size", 0),
                file_content=f.get("data", {}).get("content", ""),
                chat_id=chat_id,
                user_id=user_id,
                user_email=user_email,
            )

        return body

    def outlet(self, body: dict, __user__: dict | None = None) -> dict:
        """Post-response hook: no-op for this plugin."""
        return body

    def _notify_gateway(
        self,
        filename: str,
        mimetype: str,
        size_bytes: int,
        file_content: str,
        chat_id: str,
        user_id: str,
        user_email: str,
    ) -> None:
        """POST file metadata to gateway webhook."""
        # Compute hash from available content
        content_bytes = file_content.encode("utf-8") if file_content else b""
        file_hash = hashlib.sha3_512(content_bytes).hexdigest() if content_bytes else ""

        payload = {
            "filename": filename,
            "mimetype": mimetype,
            "size_bytes": size_bytes,
            "hash_sha3_512": file_hash,
            "chat_id": chat_id,
            "user_id": user_id,
            "user_email": user_email,
            "upload_timestamp": datetime.now(timezone.utc).isoformat(),
        }

        headers = {"Content-Type": "application/json"}
        if self.valves.gateway_api_key:
            headers["X-API-Key"] = self.valves.gateway_api_key

        try:
            resp = requests.post(
                f"{self.valves.gateway_url}/v1/attachments/notify",
                json=payload,
                headers=headers,
                timeout=5,
            )
            if resp.status_code == 200:
                logger.info("Attachment notified: %s (%.16s...)", filename, file_hash)
            else:
                logger.warning("Attachment notify failed: %d %s", resp.status_code, resp.text[:100])
        except Exception as e:
            logger.warning("Attachment notify error: %s", e)
```

**Step 2: Commit**

```bash
git add plugins/openwebui/attachment_notifier.py
git commit -m "feat: OpenWebUI pipeline plugin — attachment upload notifications"
```

---

## Phase C4: Image OCR + PII Detection (Tasks 11–13)

---

### Task 11: ImageOCRAnalyzer Core

**Files:**
- Create: `src/gateway/content/image_ocr.py`
- Test: `tests/unit/test_image_ocr.py`

**Step 1: Write the failing test**

```python
"""Unit tests for image OCR + PII detection."""

import pytest
from unittest.mock import patch, MagicMock


@pytest.fixture(params=["asyncio"])
def anyio_backend(request):
    return request.param


@pytest.mark.anyio
async def test_ocr_extracts_text():
    """OCR extracts text from image bytes."""
    from gateway.content.image_ocr import ImageOCRAnalyzer

    analyzer = ImageOCRAnalyzer(max_size_mb=10)

    with patch("gateway.content.image_ocr.pytesseract") as mock_tess, \
         patch("gateway.content.image_ocr.Image") as mock_pil:
        mock_tess.image_to_string.return_value = "Hello World 123-45-6789"
        mock_pil.open.return_value = MagicMock()

        result = await analyzer.extract_text(b"fake_png_bytes")
        assert result == "Hello World 123-45-6789"


@pytest.mark.anyio
async def test_ocr_too_large_skipped():
    """Images larger than max_size_mb are skipped."""
    from gateway.content.image_ocr import ImageOCRAnalyzer

    analyzer = ImageOCRAnalyzer(max_size_mb=1)
    # 2MB image
    result = await analyzer.extract_text(b"x" * (2 * 1024 * 1024))
    assert result is None


@pytest.mark.anyio
async def test_ocr_with_pii():
    """OCR text with SSN triggers PII detection."""
    from gateway.content.image_ocr import ImageOCRAnalyzer

    analyzer = ImageOCRAnalyzer(max_size_mb=10)

    with patch("gateway.content.image_ocr.pytesseract") as mock_tess, \
         patch("gateway.content.image_ocr.Image") as mock_pil:
        mock_tess.image_to_string.return_value = "Patient SSN: 123-45-6789"
        mock_pil.open.return_value = MagicMock()

        result = await analyzer.analyze_image(b"fake_bytes")
        assert result["ocr_text_extracted"] is True
        assert result["ocr_pii_found"] is True
        assert "ssn" in result["ocr_pii_types"]


@pytest.mark.anyio
async def test_ocr_clean_text():
    """OCR text without PII returns clean result."""
    from gateway.content.image_ocr import ImageOCRAnalyzer

    analyzer = ImageOCRAnalyzer(max_size_mb=10)

    with patch("gateway.content.image_ocr.pytesseract") as mock_tess, \
         patch("gateway.content.image_ocr.Image") as mock_pil:
        mock_tess.image_to_string.return_value = "Hello World"
        mock_pil.open.return_value = MagicMock()

        result = await analyzer.analyze_image(b"fake_bytes")
        assert result["ocr_text_extracted"] is True
        assert result["ocr_pii_found"] is False
        assert result["ocr_pii_types"] == []


@pytest.mark.anyio
async def test_ocr_tesseract_missing_fail_open():
    """Missing Tesseract returns graceful result."""
    from gateway.content.image_ocr import ImageOCRAnalyzer

    analyzer = ImageOCRAnalyzer(max_size_mb=10)

    with patch("gateway.content.image_ocr._TESSERACT_AVAILABLE", False):
        result = await analyzer.analyze_image(b"fake_bytes")
        assert result["ocr_text_extracted"] is False
        assert result["ocr_pii_found"] is False


@pytest.mark.anyio
async def test_ocr_with_credit_card():
    """Credit card in image triggers BLOCK-level PII."""
    from gateway.content.image_ocr import ImageOCRAnalyzer

    analyzer = ImageOCRAnalyzer(max_size_mb=10)

    with patch("gateway.content.image_ocr.pytesseract") as mock_tess, \
         patch("gateway.content.image_ocr.Image") as mock_pil:
        mock_tess.image_to_string.return_value = "Card: 4111-1111-1111-1111"
        mock_pil.open.return_value = MagicMock()

        result = await analyzer.analyze_image(b"fake_bytes")
        assert result["ocr_pii_found"] is True
        assert "credit_card" in result["ocr_pii_types"]
        assert result["ocr_pii_block"] is True
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_image_ocr.py -v`
Expected: FAIL with "ModuleNotFoundError"

**Step 3: Write minimal implementation**

Create `src/gateway/content/image_ocr.py`:

```python
"""Image OCR + PII detection via Tesseract.

Extracts text from images using Tesseract OCR, then runs the gateway's
existing PII and toxicity detection on the extracted text.
Fail-open: if Tesseract is not installed, returns graceful empty result.
"""

from __future__ import annotations

import asyncio
import io
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

try:
    import pytesseract
    from PIL import Image
    _TESSERACT_AVAILABLE = True
except ImportError:
    _TESSERACT_AVAILABLE = False
    pytesseract = None  # type: ignore[assignment]
    Image = None  # type: ignore[assignment,misc]

# PII patterns — same as pii_detector.py and stream_safety.py
_PII_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("ssn", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("credit_card", re.compile(r"\b(?:\d[ -]*?){13,19}\b")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("api_key", re.compile(r"\b(?:sk-|pk_live_|rk_live_|sk_live_)[a-zA-Z0-9]{20,}\b")),
]

_BLOCK_PII_TYPES = {"credit_card", "ssn", "aws_access_key", "api_key"}

# Toxicity deny terms — basic set, matches toxicity_detector.py
_TOXICITY_PATTERNS: list[re.Pattern] = [
    re.compile(r"\b(?:kill|murder|assassinate)\s+(?:him|her|them|people)\b", re.IGNORECASE),
]


class ImageOCRAnalyzer:
    """Extract text from images via Tesseract, then scan for PII/toxicity."""

    def __init__(self, max_size_mb: int = 10):
        self._max_size_bytes = max_size_mb * 1024 * 1024

    async def extract_text(self, image_bytes: bytes) -> str | None:
        """Extract text from image bytes. Returns None if skipped or failed."""
        if not _TESSERACT_AVAILABLE:
            logger.debug("Tesseract not available, skipping OCR")
            return None

        if len(image_bytes) > self._max_size_bytes:
            logger.warning("Image too large for OCR: %d bytes > %d max", len(image_bytes), self._max_size_bytes)
            return None

        try:
            def _do_ocr() -> str:
                img = Image.open(io.BytesIO(image_bytes))
                return pytesseract.image_to_string(img)

            return await asyncio.to_thread(_do_ocr)
        except Exception:
            logger.warning("Tesseract OCR failed", exc_info=True)
            return None

    async def analyze_image(self, image_bytes: bytes) -> dict[str, Any]:
        """Run OCR + PII/toxicity on an image. Returns analysis dict."""
        text = await self.extract_text(image_bytes)

        if text is None:
            return {
                "ocr_text_extracted": False,
                "ocr_text_length": 0,
                "ocr_pii_found": False,
                "ocr_pii_types": [],
                "ocr_pii_block": False,
                "ocr_toxicity_found": False,
            }

        # PII scan
        pii_types: list[str] = []
        for pii_type, pattern in _PII_PATTERNS:
            if pattern.search(text):
                pii_types.append(pii_type)

        pii_block = bool(set(pii_types) & _BLOCK_PII_TYPES)

        # Toxicity scan
        toxicity_found = any(p.search(text) for p in _TOXICITY_PATTERNS)

        return {
            "ocr_text_extracted": True,
            "ocr_text_length": len(text),
            "ocr_pii_found": len(pii_types) > 0,
            "ocr_pii_types": pii_types,
            "ocr_pii_block": pii_block,
            "ocr_toxicity_found": toxicity_found,
        }
```

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_image_ocr.py -v`
Expected: PASS (6 tests)

**Step 5: Commit**

```bash
git add src/gateway/content/image_ocr.py tests/unit/test_image_ocr.py
git commit -m "feat: ImageOCRAnalyzer — Tesseract OCR + PII/toxicity on extracted text"
```

---

### Task 12: Wire OCR into Request Pipeline

**Files:**
- Modify: `src/gateway/pipeline/orchestrator.py`
- Modify: `src/gateway/main.py` (init OCR analyzer)
- Modify: `src/gateway/pipeline/context.py` (add image_ocr_analyzer field)
- Modify: `pyproject.toml` (add ocr optional extra)
- Test: `tests/unit/test_image_ocr.py`

**Step 1: Write the failing test**

```python
@pytest.mark.anyio
async def test_ocr_pipeline_block_on_pii():
    """OCR PII block in pipeline returns 403."""
    from gateway.content.image_ocr import evaluate_image_ocr

    mock_analyzer = MagicMock()

    async def fake_analyze(image_bytes):
        return {
            "ocr_text_extracted": True,
            "ocr_text_length": 30,
            "ocr_pii_found": True,
            "ocr_pii_types": ["credit_card"],
            "ocr_pii_block": True,
            "ocr_toxicity_found": False,
        }
    mock_analyzer.analyze_image = fake_analyze

    images = [{"index": 0, "raw_bytes": b"fake", "hash_sha3_512": "abc"}]
    blocked, response, results = await evaluate_image_ocr(mock_analyzer, images)

    assert blocked is True
    assert response.status_code == 403
    assert results[0]["ocr_pii_block"] is True
```

**Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/test_image_ocr.py::test_ocr_pipeline_block_on_pii -v`
Expected: FAIL

**Step 3: Write minimal implementation**

Add to `src/gateway/content/image_ocr.py`:

```python
async def evaluate_image_ocr(
    analyzer: ImageOCRAnalyzer,
    images: list[dict[str, Any]],
) -> tuple[bool, Any, list[dict[str, Any]]]:
    """Run OCR + PII on extracted images.

    Returns (is_blocked, error_response_or_None, ocr_results).
    """
    from starlette.responses import JSONResponse

    results: list[dict[str, Any]] = []

    for img in images:
        ocr_result = await analyzer.analyze_image(img["raw_bytes"])
        ocr_result["image_index"] = img.get("index", 0)
        ocr_result["hash_sha3_512"] = img.get("hash_sha3_512", "")
        results.append(ocr_result)

        if ocr_result.get("ocr_pii_block"):
            pii_types = ", ".join(ocr_result.get("ocr_pii_types", []))
            logger.warning("OCR PII BLOCK: types=%s hash=%.16s...", pii_types, img.get("hash_sha3_512", ""))
            error_body = {
                "error": f"Request blocked: image contains sensitive data detected via OCR ({pii_types})",
                "pii_types": ocr_result.get("ocr_pii_types", []),
            }
            return True, JSONResponse(error_body, status_code=403), results

    return False, None, results
```

Add `image_ocr_analyzer: Any = None` to PipelineContext.

Add `_init_image_ocr()` to `main.py`:

```python
def _init_image_ocr(settings, ctx):
    from gateway.content.image_ocr import ImageOCRAnalyzer
    ctx.image_ocr_analyzer = ImageOCRAnalyzer(max_size_mb=settings.image_ocr_max_size_mb)
    logger.info("Image OCR analyzer enabled: max_size=%dMB", settings.image_ocr_max_size_mb)
```

Call in `on_startup()`:
```python
if settings.image_ocr_enabled:
    _init_image_ocr(settings, ctx)
```

Add to `pyproject.toml` optional extras:
```toml
ocr = ["pytesseract>=0.3", "Pillow>=9.0"]
```

Wire into orchestrator after image safety check.

**Step 4: Run test to verify it passes**

Run: `python -m pytest tests/unit/test_image_ocr.py -v`
Expected: PASS (7 tests)

**Step 5: Commit**

```bash
git add src/gateway/content/image_ocr.py src/gateway/pipeline/orchestrator.py src/gateway/main.py src/gateway/pipeline/context.py pyproject.toml tests/unit/test_image_ocr.py
git commit -m "feat: wire OCR PII detection into request pipeline — BLOCK on high-risk PII in images"
```

---

### Task 13: Dashboard Attachment Cards

**Files:**
- Modify: `src/gateway/lineage/static/app.js` (render attachment cards in execution detail)
- Modify: `src/gateway/lineage/static/style.css` (attachment card styles)

**Step 1: Add attachment card rendering**

In `app.js`, in the execution detail rendering function, after tool events section, add:

```javascript
// Attachments section
const fileMeta = record.file_metadata || [];
const imageAnalysis = record.image_analysis || [];

if (fileMeta.length > 0 || imageAnalysis.length > 0) {
    html += '<h3>📎 Attachments</h3>';
    html += '<div class="attachment-cards">';

    for (const f of fileMeta) {
        html += `<div class="attachment-card">
            <div class="attachment-name">${escapeHtml(f.filename || 'unknown')}</div>
            <div class="attachment-meta">
                <span class="badge badge-file">${f.mimetype || 'unknown'}</span>
                <span>${formatBytes(f.size_bytes || 0)}</span>
                <span class="badge badge-source">${f.source || 'unknown'}</span>
            </div>
            <div class="attachment-hash" title="${f.hash_sha3_512 || ''}">
                SHA3: ${(f.hash_sha3_512 || '').substring(0, 24)}...
            </div>
        </div>`;
    }

    for (const img of imageAnalysis) {
        const verdictClass = img.safety_verdict === 'pass' ? 'badge-pass' :
                            img.safety_verdict === 'block' ? 'badge-block' : 'badge-warn';
        html += `<div class="attachment-card attachment-image">
            <div class="attachment-name">Image #${img.image_index}</div>
            <div class="attachment-meta">
                <span class="badge ${verdictClass}">Safety: ${img.safety_verdict || 'n/a'}</span>
                ${img.safety_category ? `<span class="badge badge-warn">${img.safety_category}</span>` : ''}
                ${img.ocr_text_extracted ? `<span class="badge badge-info">OCR: ${img.ocr_text_length || 0} chars</span>` : ''}
                ${img.ocr_pii_found ? `<span class="badge badge-block">PII: ${(img.ocr_pii_types || []).join(', ')}</span>` : ''}
            </div>
            <div class="attachment-hash" title="${img.hash_sha3_512 || ''}">
                SHA3: ${(img.hash_sha3_512 || '').substring(0, 24)}...
            </div>
        </div>`;
    }

    html += '</div>';
}
```

**Step 2: Add CSS styles**

```css
.attachment-cards { display: grid; gap: 0.5rem; margin: 0.5rem 0; }
.attachment-card {
    background: var(--surface-2, #1a1a2e);
    border: 1px solid var(--border, #333);
    border-left: 3px solid #6c63ff;
    border-radius: 6px;
    padding: 0.75rem;
}
.attachment-card.attachment-image { border-left-color: #f5a623; }
.attachment-name { font-weight: 600; margin-bottom: 0.25rem; }
.attachment-meta { display: flex; gap: 0.5rem; flex-wrap: wrap; margin-bottom: 0.25rem; }
.attachment-hash { font-family: monospace; font-size: 0.75rem; color: var(--text-muted, #888); }
.badge-file { background: #2d2d5e; color: #a0a0ff; }
.badge-source { background: #1a3a1a; color: #80c080; }
.badge-info { background: #1a3a5e; color: #80c0ff; }
.badge-block { background: #5e1a1a; color: #ff8080; }
.badge-pass { background: #1a3a1a; color: #80ff80; }
.badge-warn { background: #5e4a1a; color: #ffc080; }
```

**Step 3: Add file icon badge to session timeline**

In the session timeline rendering, add a 📎 badge for executions with attachments (same pattern as the ⚙ tool badge).

**Step 4: Commit**

```bash
git add src/gateway/lineage/static/app.js src/gateway/lineage/static/style.css
git commit -m "feat: lineage dashboard attachment cards — file metadata and image analysis display"
```

---

### Task 14: Full Integration Test

**Files:**
- Create: `tests/unit/test_multimodal_integration.py`

**Step 1: Write integration test**

```python
"""Integration test: full multimodal audit pipeline."""

import base64
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.fixture(params=["asyncio"])
def anyio_backend(request):
    return request.param


@pytest.mark.anyio
async def test_full_image_pipeline():
    """Image goes through: extraction → safety → OCR → execution record."""
    from gateway.middleware.attachment_tracker import extract_images_from_messages, AttachmentNotificationCache
    from gateway.content.image_safety import evaluate_image_safety
    from gateway.content.image_ocr import evaluate_image_ocr
    from gateway.content.base import Decision, Verdict

    # 1. Extract image from messages
    b64_data = base64.b64encode(b"fake_png_data").decode()
    messages = [{"role": "user", "content": [
        {"type": "text", "text": "What is in this image?"},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64_data}"}},
    ]}]
    images = extract_images_from_messages(messages)
    assert len(images) == 1

    # 2. Image safety passes
    mock_safety = MagicMock()
    mock_safety.analyze_image = AsyncMock(return_value=Decision(
        verdict=Verdict.PASS, confidence=0.95,
        analyzer_id="walacor.image_safety.v1", category="image_safety", reason="safe",
    ))
    blocked, _, safety_results = await evaluate_image_safety(mock_safety, images, max_images=5)
    assert not blocked

    # 3. OCR finds no PII
    mock_ocr = MagicMock()
    async def fake_ocr(image_bytes):
        return {"ocr_text_extracted": True, "ocr_text_length": 11, "ocr_pii_found": False, "ocr_pii_types": [], "ocr_pii_block": False, "ocr_toxicity_found": False}
    mock_ocr.analyze_image = fake_ocr
    blocked, _, ocr_results = await evaluate_image_ocr(mock_ocr, images)
    assert not blocked

    # 4. Merge results for execution record
    image_analysis = []
    for i, img in enumerate(images):
        entry = {**safety_results[i]}
        if i < len(ocr_results):
            entry.update(ocr_results[i])
        image_analysis.append(entry)

    assert image_analysis[0]["safety_verdict"] == "pass"
    assert image_analysis[0]["ocr_text_extracted"] is True


@pytest.mark.anyio
async def test_notification_correlates_with_request():
    """Webhook notification matches request image by hash."""
    from gateway.middleware.attachment_tracker import AttachmentNotificationCache, extract_images_from_messages
    import hashlib

    # Pre-notify via webhook
    cache = AttachmentNotificationCache(max_size=100, ttl_seconds=3600)
    raw_bytes = b"actual_png_content"
    file_hash = hashlib.sha3_512(raw_bytes).hexdigest()
    cache.store({
        "hash_sha3_512": file_hash,
        "filename": "photo.png",
        "user_id": "user-1",
        "chat_id": "chat-1",
    })

    # Request arrives with same image
    b64_data = base64.b64encode(raw_bytes).decode()
    messages = [{"role": "user", "content": [
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64_data}"}},
    ]}]
    images = extract_images_from_messages(messages)

    # Correlate
    enriched = cache.get(images[0]["hash_sha3_512"])
    assert enriched is not None
    assert enriched["filename"] == "photo.png"
    assert enriched["user_id"] == "user-1"


def test_run_full_test_suite():
    """Verify no regressions in full test suite."""
    import subprocess
    result = subprocess.run(
        ["python", "-m", "pytest", "tests/unit/", "-v", "--tb=short", "-q"],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, f"Test suite failed:\n{result.stdout}\n{result.stderr}"
```

**Step 2: Run tests**

Run: `python -m pytest tests/unit/test_multimodal_integration.py -v`
Expected: PASS (3 tests)

**Step 3: Run full test suite**

Run: `python -m pytest tests/ -v --tb=short`
Expected: All pass, no regressions

**Step 4: Commit**

```bash
git add tests/unit/test_multimodal_integration.py
git commit -m "test: multimodal audit integration tests — full pipeline verification"
```

---

## Summary

| Phase | Tasks | Theme | Key Files |
|-------|-------|-------|-----------|
| C2 | 1-6 | Document/file tracking | `attachment_tracker.py`, `hasher.py`, `reader.py` |
| C1 | 7-10 | Image safety | `image_safety.py`, `attachment_notifier.py` |
| C4 | 11-14 | OCR + PII | `image_ocr.py`, dashboard updates, integration tests |

**Total: 14 tasks. Run `python -m pytest tests/ -v` after each task.**
