"""Unit tests for retry logic with tenacity."""

import pytest

from gateway.routing.retry import forward_with_retry, is_retryable

import httpx


@pytest.fixture(params=["asyncio"])
def anyio_backend(request):
    return request.param


class _MockResponse:
    def __init__(self, status_code, text=""):
        self.status_code = status_code
        self.text = text


class _FakeProviderError(Exception):
    """Simulates a provider HTTP error with status code."""
    def __init__(self, status_code, body=""):
        self.status_code = status_code
        self.body = body
        super().__init__(f"HTTP {status_code}: {body}")


def test_is_retryable_503():
    assert is_retryable(_FakeProviderError(503)) is True


def test_is_retryable_429():
    assert is_retryable(_FakeProviderError(429)) is True


def test_is_retryable_400():
    assert is_retryable(_FakeProviderError(400)) is False


def test_is_retryable_network_error():
    assert is_retryable(httpx.ConnectError("connection refused")) is True


@pytest.mark.anyio
async def test_retry_on_503_succeeds_on_second_attempt():
    """First call raises 503, second succeeds."""
    call_count = 0

    async def forward():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise _FakeProviderError(503, "Service Unavailable")
        return "success"

    result = await forward_with_retry(forward, max_attempts=3)
    assert result == "success"
    assert call_count == 2


@pytest.mark.anyio
async def test_no_retry_on_400():
    """400 errors are not retried."""
    call_count = 0

    async def forward():
        nonlocal call_count
        call_count += 1
        raise _FakeProviderError(400, "Bad Request")

    with pytest.raises(_FakeProviderError):
        await forward_with_retry(forward, max_attempts=3)
    assert call_count == 1


@pytest.mark.anyio
async def test_max_attempts_exhausted_raises():
    """All retries fail → raises last exception."""
    call_count = 0

    async def forward():
        nonlocal call_count
        call_count += 1
        raise _FakeProviderError(503, "Service Unavailable")

    with pytest.raises(_FakeProviderError):
        await forward_with_retry(forward, max_attempts=3)
    assert call_count == 3


@pytest.mark.anyio
async def test_retry_on_network_error():
    """Network errors trigger retry."""
    call_count = 0

    async def forward():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise httpx.ConnectError("connection refused")
        return "recovered"

    result = await forward_with_retry(forward, max_attempts=3)
    assert result == "recovered"
    assert call_count == 2


@pytest.mark.anyio
async def test_429_triggers_retry():
    """Rate limited (429) triggers retry."""
    call_count = 0

    async def forward():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise _FakeProviderError(429, "Rate limited")
        return "ok"

    result = await forward_with_retry(forward, max_attempts=3)
    assert result == "ok"
    assert call_count == 2
