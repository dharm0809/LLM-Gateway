"""Tests for hedged cross-provider requests."""
import asyncio

import pytest

from gateway.routing.hedge import hedge_request


@pytest.fixture(params=["asyncio"])
def anyio_backend(request):
    return request.param


@pytest.mark.anyio
async def test_primary_wins_before_delay(anyio_backend):
    """Primary completes before delay — secondary never starts."""
    secondary_started = False

    async def primary():
        await asyncio.sleep(0.01)
        return "primary_result"

    async def secondary():
        nonlocal secondary_started
        secondary_started = True
        await asyncio.sleep(0.01)
        return "secondary_result"

    result, winner = await hedge_request(primary, secondary, delay_seconds=0.5)
    assert result == "primary_result"
    assert winner == "primary"
    assert secondary_started is False


@pytest.mark.anyio
async def test_secondary_wins_when_primary_slow(anyio_backend):
    """Primary is slow — secondary starts and wins."""

    async def primary():
        await asyncio.sleep(10.0)  # Very slow
        return "primary_result"

    async def secondary():
        await asyncio.sleep(0.01)  # Fast
        return "secondary_result"

    result, winner = await hedge_request(primary, secondary, delay_seconds=0.05)
    assert result == "secondary_result"
    assert winner == "secondary"


@pytest.mark.anyio
async def test_primary_wins_race_after_hedge(anyio_backend):
    """Both running, primary finishes first."""

    async def primary():
        await asyncio.sleep(0.08)
        return "primary_result"

    async def secondary():
        await asyncio.sleep(0.5)
        return "secondary_result"

    result, winner = await hedge_request(primary, secondary, delay_seconds=0.05)
    assert result == "primary_result"
    assert winner == "primary"


@pytest.mark.anyio
async def test_secondary_exception_primary_wins(anyio_backend):
    """Secondary fails — primary still wins."""

    async def primary():
        await asyncio.sleep(0.1)
        return "primary_result"

    async def secondary():
        raise RuntimeError("secondary failed")

    result, winner = await hedge_request(primary, secondary, delay_seconds=0.05)
    assert result == "primary_result"
    assert winner == "primary"


@pytest.mark.anyio
async def test_zero_delay_starts_both_immediately(anyio_backend):
    """Zero delay starts both immediately."""

    async def primary():
        await asyncio.sleep(0.5)
        return "primary_result"

    async def secondary():
        await asyncio.sleep(0.01)
        return "secondary_result"

    result, winner = await hedge_request(primary, secondary, delay_seconds=0.0)
    assert result == "secondary_result"
    assert winner == "secondary"
