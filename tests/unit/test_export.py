"""Tests for audit log export: FileExporter, WebhookExporter, AuditExporter base."""
import asyncio
import json
import os
import tempfile
import pytest
from unittest.mock import AsyncMock, patch

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_file_exporter_writes():
    from gateway.export.file_exporter import FileExporter
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
        path = f.name
    try:
        exporter = FileExporter(path)
        await exporter.export({"key": "value", "num": 42})
        await exporter.close()
        with open(path) as f:
            line = f.readline()
        record = json.loads(line)
        assert record["key"] == "value"
        assert record["num"] == 42
    finally:
        os.unlink(path)


async def test_file_exporter_multiple_records():
    from gateway.export.file_exporter import FileExporter
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
        path = f.name
    try:
        exporter = FileExporter(path)
        for i in range(5):
            await exporter.export({"index": i})
        await exporter.close()
        with open(path) as f:
            lines = f.readlines()
        assert len(lines) == 5
        for i, line in enumerate(lines):
            assert json.loads(line)["index"] == i
    finally:
        os.unlink(path)


async def test_file_exporter_appends_to_existing():
    """FileExporter opens with 'a' mode — existing data is preserved."""
    from gateway.export.file_exporter import FileExporter
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False, mode="w") as f:
        path = f.name
        f.write(json.dumps({"existing": True}) + "\n")
    try:
        exporter = FileExporter(path)
        await exporter.export({"new": True})
        await exporter.close()
        with open(path) as f:
            lines = f.readlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["existing"] is True
        assert json.loads(lines[1])["new"] is True
    finally:
        os.unlink(path)


async def test_file_exporter_rotation():
    """File rotates when max size is exceeded."""
    from gateway.export.file_exporter import FileExporter
    with tempfile.TemporaryDirectory() as tmpdir:
        path = os.path.join(tmpdir, "audit.jsonl")
        # max_size_mb=0 forces rotation on every write after first byte
        exporter = FileExporter(path, max_size_mb=0)
        await exporter.export({"first": True})
        await exporter.export({"second": True})
        await exporter.close()
        files = os.listdir(tmpdir)
        # Should have original + at least one rotated file
        assert len(files) >= 2


async def test_webhook_exporter_sends():
    from gateway.export.webhook_exporter import WebhookExporter
    exporter = WebhookExporter(url="http://localhost:9999/ingest", batch_size=1, flush_interval=60)
    with patch.object(exporter._client, "post", new_callable=AsyncMock) as mock_post:
        mock_resp = AsyncMock()
        mock_resp.raise_for_status = lambda: None
        mock_post.return_value = mock_resp
        await exporter.export({"event": "test"})
    mock_post.assert_called_once()
    # Verify JSON body contains the record
    call_kwargs = mock_post.call_args
    body = call_kwargs.kwargs.get("content") or call_kwargs.args[1] if len(call_kwargs.args) > 1 else call_kwargs.kwargs.get("content")
    payload = json.loads(body)
    assert "records" in payload
    assert payload["records"][0]["event"] == "test"
    await exporter.close()


async def test_webhook_exporter_batches():
    """Records are batched; flush only fires when batch_size is reached."""
    from gateway.export.webhook_exporter import WebhookExporter
    exporter = WebhookExporter(url="http://localhost:9999/ingest", batch_size=3, flush_interval=60)
    post_calls = []

    async def fake_post(url, *, content, headers):
        post_calls.append(json.loads(content))
        resp = AsyncMock()
        resp.raise_for_status = lambda: None
        return resp

    with patch.object(exporter._client, "post", side_effect=fake_post):
        await exporter.export({"n": 1})
        assert len(post_calls) == 0  # not yet flushed
        await exporter.export({"n": 2})
        assert len(post_calls) == 0
        await exporter.export({"n": 3})
        assert len(post_calls) == 1  # batch_size=3 reached
        assert len(post_calls[0]["records"]) == 3
    await exporter.close()


async def test_webhook_exporter_flush_on_close():
    """Remaining buffered records are flushed on close()."""
    from gateway.export.webhook_exporter import WebhookExporter
    exporter = WebhookExporter(url="http://localhost:9999/ingest", batch_size=100, flush_interval=60)
    flushed = []

    async def fake_post(url, *, content, headers):
        flushed.append(json.loads(content))
        resp = AsyncMock()
        resp.raise_for_status = lambda: None
        return resp

    with patch.object(exporter._client, "post", side_effect=fake_post):
        await exporter.export({"partial": True})
        assert len(flushed) == 0  # not yet flushed
        await exporter.close()
    assert len(flushed) == 1
    assert flushed[0]["records"][0]["partial"] is True


async def test_webhook_exporter_retry_then_success():
    """Exporter retries on failure and succeeds on second attempt."""
    from gateway.export.webhook_exporter import WebhookExporter
    exporter = WebhookExporter(url="http://localhost:9999/ingest", batch_size=1, flush_interval=60)
    attempt = 0

    async def fake_post(url, *, content, headers):
        nonlocal attempt
        attempt += 1
        if attempt == 1:
            raise Exception("transient error")
        resp = AsyncMock()
        resp.raise_for_status = lambda: None
        return resp

    with patch("gateway.export.webhook_exporter._RETRY_DELAY", 0):
        with patch.object(exporter._client, "post", side_effect=fake_post):
            await exporter.export({"retry": True})

    assert attempt == 2
    await exporter.close()


async def test_base_export_batch():
    from gateway.export.base import AuditExporter
    exported = []

    class TestExporter(AuditExporter):
        async def export(self, record):
            exported.append(record)

        async def close(self):
            pass

    exp = TestExporter()
    await exp.export_batch([{"a": 1}, {"b": 2}])
    assert len(exported) == 2
    assert exported[0] == {"a": 1}
    assert exported[1] == {"b": 2}


async def test_base_export_batch_empty():
    """export_batch with empty list does nothing."""
    from gateway.export.base import AuditExporter

    class TestExporter(AuditExporter):
        async def export(self, record):
            raise AssertionError("should not be called")

        async def close(self):
            pass

    exp = TestExporter()
    await exp.export_batch([])  # should not raise


async def test_webhook_exporter_custom_headers():
    """Custom headers are forwarded in the POST request."""
    from gateway.export.webhook_exporter import WebhookExporter
    exporter = WebhookExporter(
        url="http://localhost:9999/ingest",
        headers={"Authorization": "Splunk abc123"},
        batch_size=1,
        flush_interval=60,
    )
    captured_headers = {}

    async def fake_post(url, *, content, headers):
        captured_headers.update(headers)
        resp = AsyncMock()
        resp.raise_for_status = lambda: None
        return resp

    with patch.object(exporter._client, "post", side_effect=fake_post):
        await exporter.export({"splunk": True})

    assert captured_headers.get("Authorization") == "Splunk abc123"
    assert captured_headers.get("Content-Type") == "application/json"
    await exporter.close()
