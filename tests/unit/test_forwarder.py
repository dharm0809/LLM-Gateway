"""Unit tests for the forwarder pipeline step."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


# Pin anyio to asyncio (AsyncMock is asyncio-specific)
@pytest.fixture(params=["asyncio"])
def anyio_backend(request):
    return request.param


def _make_mock_adapter(provider_name: str = "openai"):
    adapter = MagicMock()
    adapter.get_provider_name.return_value = provider_name
    req = MagicMock()
    req.method = "POST"
    req.url = "https://api.openai.com/v1/chat/completions"
    req.headers = {}
    req.content = b"{}"
    adapter.build_forward_request = AsyncMock(return_value=req)
    return adapter


def _make_mock_call():
    call = MagicMock()
    call.metadata = {}
    return call


def _make_mock_request():
    request = MagicMock()
    return request


# ---------------------------------------------------------------------------
# stream_with_tee: upstream status code propagation
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_stream_with_tee_propagates_upstream_4xx_status():
    """StreamingResponse must carry the actual upstream status (e.g. 429), not hard-coded 200."""
    from gateway.pipeline.forwarder import stream_with_tee
    from gateway.pipeline.context import get_pipeline_context

    # Build a mock upstream response that returns 429
    mock_upstream = AsyncMock()
    mock_upstream.status_code = 429
    mock_upstream.aiter_bytes = AsyncMock(return_value=aiter([b'{"error":"rate_limited"}\n']))

    # Mock the stream context manager
    mock_stream_ctx = AsyncMock()
    mock_stream_ctx.__aenter__ = AsyncMock(return_value=mock_upstream)
    mock_stream_ctx.__aexit__ = AsyncMock(return_value=False)

    mock_client = MagicMock()
    mock_client.stream = MagicMock(return_value=mock_stream_ctx)

    ctx = get_pipeline_context()
    ctx.http_client = mock_client

    adapter = _make_mock_adapter()
    call = _make_mock_call()
    request = _make_mock_request()

    with patch("gateway.pipeline.forwarder.forward_duration") as mock_dur:
        mock_dur.labels.return_value.observe = MagicMock()
        resp, buf = await stream_with_tee(adapter, call, request)

    assert resp.status_code == 429

    # Restore
    ctx.http_client = None


@pytest.mark.anyio
async def test_stream_with_tee_propagates_upstream_200_status():
    """StreamingResponse carries 200 on success."""
    from gateway.pipeline.forwarder import stream_with_tee
    from gateway.pipeline.context import get_pipeline_context

    mock_upstream = AsyncMock()
    mock_upstream.status_code = 200
    mock_upstream.aiter_bytes = AsyncMock(return_value=aiter([b"data: chunk\n\n"]))

    mock_stream_ctx = AsyncMock()
    mock_stream_ctx.__aenter__ = AsyncMock(return_value=mock_upstream)
    mock_stream_ctx.__aexit__ = AsyncMock(return_value=False)

    mock_client = MagicMock()
    mock_client.stream = MagicMock(return_value=mock_stream_ctx)

    ctx = get_pipeline_context()
    ctx.http_client = mock_client

    adapter = _make_mock_adapter()
    call = _make_mock_call()
    request = _make_mock_request()

    with patch("gateway.pipeline.forwarder.forward_duration") as mock_dur:
        mock_dur.labels.return_value.observe = MagicMock()
        resp, buf = await stream_with_tee(adapter, call, request)

    assert resp.status_code == 200
    ctx.http_client = None


# ---------------------------------------------------------------------------
# generate() exception handling: __aexit__ called with exc info
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_generate_passes_exception_to_aexit():
    """When the stream raises, __aexit__ receives the exception type (not None, None, None)."""
    from gateway.pipeline.forwarder import stream_with_tee
    from gateway.pipeline.context import get_pipeline_context

    class StreamError(Exception):
        pass

    async def failing_aiter():
        yield b"chunk1"
        raise StreamError("upstream broke")

    mock_upstream = MagicMock()
    mock_upstream.status_code = 200
    mock_upstream.aiter_bytes = MagicMock(return_value=failing_aiter())

    mock_stream_ctx = AsyncMock()
    mock_stream_ctx.__aenter__ = AsyncMock(return_value=mock_upstream)
    mock_stream_ctx.__aexit__ = AsyncMock(return_value=False)

    mock_client = MagicMock()
    mock_client.stream = MagicMock(return_value=mock_stream_ctx)

    ctx = get_pipeline_context()
    ctx.http_client = mock_client

    adapter = _make_mock_adapter()
    call = _make_mock_call()
    request = _make_mock_request()

    with patch("gateway.pipeline.forwarder.forward_duration") as mock_dur:
        mock_dur.labels.return_value.observe = MagicMock()
        resp, _ = await stream_with_tee(adapter, call, request)

    # Consume the generator to trigger the exception
    gen = resp.body_iterator
    chunks = []
    try:
        async for chunk in gen:
            chunks.append(chunk)
    except StreamError:
        pass

    # __aexit__ should have been called with StreamError type as first arg, not None
    mock_stream_ctx.__aexit__.assert_awaited()
    call_args = mock_stream_ctx.__aexit__.call_args
    exc_type = call_args.args[0] if call_args.args else None
    assert exc_type is StreamError, f"Expected StreamError but got {exc_type}"

    ctx.http_client = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def aiter(items):
    """Helper: make a sync list into an async iterator."""
    for item in items:
        yield item
