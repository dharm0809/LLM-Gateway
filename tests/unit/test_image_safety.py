"""Unit tests for image safety analysis via LlamaGuard Vision."""

import pytest
from unittest.mock import AsyncMock, MagicMock


@pytest.fixture(params=["asyncio"])
def anyio_backend(request):
    return request.param


@pytest.mark.anyio
async def test_image_safety_safe_image(anyio_backend):
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
async def test_image_safety_unsafe_s4(anyio_backend):
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
async def test_image_safety_unsafe_other(anyio_backend):
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
async def test_image_safety_timeout_fail_open(anyio_backend):
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
async def test_image_safety_connection_error_fail_open(anyio_backend):
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
