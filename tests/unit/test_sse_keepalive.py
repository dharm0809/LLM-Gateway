# tests/unit/test_sse_keepalive.py
import pytest
import asyncio


@pytest.fixture(params=["asyncio"])
def anyio_backend(request):
    return request.param


@pytest.mark.anyio
async def test_keepalive_produces_sse_comments():
    """Keepalive task produces SSE comment lines."""
    from gateway.pipeline.forwarder import sse_keepalive_generator

    chunks = []
    gen = sse_keepalive_generator(interval_seconds=0.05)  # fast for test
    task = asyncio.create_task(_collect(gen, chunks, max_items=3))
    await asyncio.sleep(0.2)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert len(chunks) >= 2
    for chunk in chunks:
        assert chunk == b": keepalive\n\n"


async def _collect(gen, out, max_items):
    count = 0
    async for item in gen:
        out.append(item)
        count += 1
        if count >= max_items:
            break
