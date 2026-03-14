"""Hedged cross-provider requests for tail latency reduction."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Coroutine, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


async def hedge_request(
    primary: Callable[[], Coroutine[Any, Any, T]],
    secondary: Callable[[], Coroutine[Any, Any, T]],
    delay_seconds: float,
) -> tuple[T, str]:
    """Race primary against a delayed secondary request.

    Returns (result, winner) where winner is "primary" or "secondary".
    The secondary only starts after delay_seconds. If primary completes
    before the delay, secondary never starts.
    """
    primary_task = asyncio.create_task(primary())

    try:
        # Wait for primary to complete within the delay window
        result = await asyncio.wait_for(asyncio.shield(primary_task), timeout=delay_seconds)
        return result, "primary"
    except asyncio.TimeoutError:
        pass  # Primary too slow, start secondary

    # Primary is still running — start secondary and race them
    secondary_task = asyncio.create_task(secondary())

    done, pending = await asyncio.wait(
        {primary_task, secondary_task},
        return_when=asyncio.FIRST_COMPLETED,
    )

    # If the first-completed task raised an exception, fall back to the other
    winner_task = done.pop()
    if winner_task.exception() is not None and pending:
        # The "winner" failed — wait for the other task instead
        logger.debug(
            "Hedge %s failed: %s — waiting for other",
            "primary" if winner_task is primary_task else "secondary",
            winner_task.exception(),
        )
        fallback_tasks = pending  # still running
        done2, pending2 = await asyncio.wait(
            fallback_tasks, return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending2:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        winner_task = done2.pop()

    # Cancel remaining pending tasks
    for task in pending:
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    winner = "primary" if winner_task is primary_task else "secondary"

    return winner_task.result(), winner
