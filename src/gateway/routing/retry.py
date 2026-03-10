"""Retry logic with tenacity for transient provider errors."""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

import httpx
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)

_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def is_retryable(exc: BaseException) -> bool:
    """Returns True for transient errors (503, 429, network errors)."""
    if isinstance(exc, httpx.ConnectError | httpx.ConnectTimeout | httpx.ReadTimeout):
        return True
    status = getattr(exc, "status_code", None)
    if status is not None and status in _RETRYABLE_STATUS_CODES:
        return True
    return False


async def forward_with_retry(
    forward_fn: Callable[[], Awaitable[Any]],
    max_attempts: int = 3,
) -> Any:
    """Call forward_fn with exponential backoff retry on transient errors.

    Non-retryable errors are raised immediately without retry.
    """

    @retry(
        retry=retry_if_exception(is_retryable),
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=0.1, min=0.05, max=2),
        reraise=True,
    )
    async def _inner():
        return await forward_fn()

    return await _inner()
