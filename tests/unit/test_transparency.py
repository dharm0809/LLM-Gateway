"""Unit tests for transparency log publisher."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from gateway.crypto.transparency import TransparencyLogPublisher


@pytest.fixture(params=["asyncio"])
def anyio_backend(request):
    return request.param


@pytest.mark.anyio
async def test_publish_success():
    """Successful publish records entry and returns response."""
    pub = TransparencyLogPublisher("https://log.example.com/append", "gw-1")
    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"sequence": 42}
    mock_client.post.return_value = mock_response

    result = await pub.publish("abc123", 100, mock_client)
    assert result == {"sequence": 42}
    assert pub.published_count == 1
    assert pub.last_published["root_hash"] == "abc123"
    assert pub.last_published["sequence"] == 42


@pytest.mark.anyio
async def test_publish_failure_status():
    """Non-2xx status returns None (fail-open)."""
    pub = TransparencyLogPublisher("https://log.example.com/append")
    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.status_code = 500
    mock_response.text = "Internal server error"
    mock_client.post.return_value = mock_response

    result = await pub.publish("abc123", 50, mock_client)
    assert result is None
    assert pub.published_count == 0


@pytest.mark.anyio
async def test_publish_network_error():
    """Network error returns None (fail-open)."""
    pub = TransparencyLogPublisher("https://log.example.com/append")
    mock_client = AsyncMock()
    mock_client.post.side_effect = ConnectionError("timeout")

    result = await pub.publish("abc123", 50, mock_client)
    assert result is None
    assert pub.published_count == 0


@pytest.mark.anyio
async def test_publish_no_url():
    """Empty URL skips publish."""
    pub = TransparencyLogPublisher("")
    mock_client = AsyncMock()

    result = await pub.publish("abc123", 50, mock_client)
    assert result is None
    mock_client.post.assert_not_called()


@pytest.mark.anyio
async def test_published_count_increments():
    """Multiple successful publishes increment counter."""
    pub = TransparencyLogPublisher("https://log.example.com/append")
    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"sequence": 1}
    mock_client.post.return_value = mock_response

    await pub.publish("root1", 10, mock_client)
    await pub.publish("root2", 20, mock_client)
    assert pub.published_count == 2


def test_initial_state():
    """New publisher starts with zero published."""
    pub = TransparencyLogPublisher("https://log.example.com/append")
    assert pub.published_count == 0
    assert pub.last_published is None


@pytest.mark.anyio
async def test_publish_payload_structure():
    """Published payload has expected fields."""
    pub = TransparencyLogPublisher("https://log.example.com/append", "gw-test")
    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"sequence": 1}
    mock_client.post.return_value = mock_response

    await pub.publish("hash123", 42, mock_client)
    call_kwargs = mock_client.post.call_args
    payload = call_kwargs.kwargs.get("json") or call_kwargs[1].get("json")
    assert payload["root_hash"] == "hash123"
    assert payload["leaf_count"] == 42
    assert payload["gateway_id"] == "gw-test"
    assert "timestamp" in payload
