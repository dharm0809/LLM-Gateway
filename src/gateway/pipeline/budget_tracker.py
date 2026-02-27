"""Phase 11: In-memory token budget tracking per tenant/user. Enforces daily/monthly caps."""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)


@dataclass
class BudgetState:
    period: str            # "daily" | "monthly"
    period_start: datetime
    tokens_used: int = 0
    max_tokens: int = 0    # 0 = unlimited


def _period_start(period: str, now: datetime) -> datetime:
    if period == "daily":
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    # monthly
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _period_expired(state: BudgetState, now: datetime) -> bool:
    start = _period_start(state.period, now)
    return state.period_start < start


class BudgetTracker:
    """
    Thread-safe in-memory token budget tracker.
    Budgets are keyed by (tenant_id, user_or_None).
    Period resets are lazy (checked on each call).
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # key: (tenant_id, user | "") -> BudgetState
        self._states: dict[tuple[str, str], BudgetState] = {}

    def configure(
        self,
        tenant_id: str,
        user: str | None,
        period: str,
        max_tokens: int,
    ) -> None:
        """Set or update a budget. max_tokens=0 means unlimited."""
        key = (tenant_id, user or "")
        now = datetime.now(timezone.utc)
        with self._lock:
            existing = self._states.get(key)
            if existing is None or existing.period != period:
                self._states[key] = BudgetState(
                    period=period,
                    period_start=_period_start(period, now),
                    tokens_used=0,
                    max_tokens=max_tokens,
                )
            else:
                existing.max_tokens = max_tokens

    async def check_and_reserve(
        self,
        tenant_id: str,
        user: str | None,
        estimated_tokens: int,
    ) -> tuple[bool, int]:
        """
        Atomically check and reserve estimated_tokens.
        Returns (allowed, remaining_after_reservation).
        If no budget configured for this key, always allowed.

        Reserves immediately (deducts estimated_tokens from tokens_used) so
        concurrent requests cannot both see the same remaining balance and
        over-spend the budget (Finding 2).
        """
        key = (tenant_id, user or "")
        now = datetime.now(timezone.utc)
        with self._lock:
            state = self._states.get(key)
            if state is None:
                # No budget configured — allow
                return True, -1  # -1 = unlimited
            if _period_expired(state, now):
                state.tokens_used = 0
                state.period_start = _period_start(state.period, now)
            if state.max_tokens == 0:
                return True, -1
            remaining = state.max_tokens - state.tokens_used
            if remaining <= 0 or estimated_tokens > remaining:
                return False, max(0, remaining)
            state.tokens_used += estimated_tokens  # reserve immediately
            return True, state.max_tokens - state.tokens_used

    async def record_usage(
        self,
        tenant_id: str,
        user: str | None,
        tokens: int,
        estimated: int = 0,
    ) -> None:
        """Adjust reservation to actual usage after a response is received.

        Applies the delta (actual - estimated) to tokens_used.  When
        actual == estimated the call is a no-op.  When actual > estimated the
        surplus is charged; when actual < estimated the over-reservation is
        refunded (Finding 2 companion fix).
        """
        delta = tokens - estimated
        if delta == 0:
            return
        key = (tenant_id, user or "")
        now = datetime.now(timezone.utc)
        with self._lock:
            state = self._states.get(key)
            if state is None:
                return
            if _period_expired(state, now):
                state.tokens_used = 0
                state.period_start = _period_start(state.period, now)
            state.tokens_used = max(0, state.tokens_used + delta)

    def get_snapshot(self, tenant_id: str, user: str | None = None) -> dict | None:
        """Return current usage snapshot for health/metrics. None if no budget configured."""
        key = (tenant_id, user or "")
        with self._lock:
            state = self._states.get(key)
            if state is None:
                return None
            return {
                "period": state.period,
                "period_start": state.period_start.isoformat(),
                "tokens_used": state.tokens_used,
                "max_tokens": state.max_tokens,
                "percent_used": (
                    round(state.tokens_used / state.max_tokens * 100, 1)
                    if state.max_tokens > 0 else 0.0
                ),
            }

    def all_snapshots(self) -> list[dict]:
        """All active budget states (for health endpoint)."""
        with self._lock:
            return [
                {
                    "tenant_id": k[0],
                    "user": k[1] or None,
                    "period": s.period,
                    "tokens_used": s.tokens_used,
                    "max_tokens": s.max_tokens,
                }
                for k, s in self._states.items()
            ]


_LUA_CHECK_AND_RESERVE = """
local key = KEYS[1]
local max_tokens = tonumber(ARGV[1])
local estimated = tonumber(ARGV[2])
local expire_secs = tonumber(ARGV[3])
if max_tokens == 0 then return {1, -1} end
local current = tonumber(redis.call('GET', key) or 0)
if current + estimated > max_tokens then
    return {0, max_tokens - current}
end
local new_val = redis.call('INCRBY', key, estimated)
redis.call('EXPIRE', key, expire_secs)
return {1, max_tokens - new_val}
"""


class RedisBudgetTracker:
    """Redis-backed token budget tracker for multi-replica deployments.

    Uses a Lua script for atomic check-and-reserve per period key.
    """

    def __init__(self, redis_client, period: str, max_tokens: int) -> None:
        self._r = redis_client
        self._period = period  # "daily" | "monthly"
        self._max_tokens = max_tokens
        # Stores (key, ttl) used in the most recent check_and_reserve per (tenant, user).
        # asyncio is single-threaded so this is safe; within a period all keys are identical
        # so concurrent requests for the same tenant don't produce incorrect results.
        self._reservation_keys: dict[tuple[str, str], tuple[str, int]] = {}

    def _period_key(self, tenant_id: str, user: str | None) -> tuple[str, int]:
        """Returns (redis_key, ttl_seconds)."""
        now = datetime.now(timezone.utc)
        if self._period == "daily":
            period_str = now.strftime("%Y%m%d")
            tomorrow = (now + timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            ttl = int((tomorrow - now).total_seconds()) + 3600  # +1h buffer
        else:  # monthly
            period_str = now.strftime("%Y%m")
            if now.month == 12:
                next_month = now.replace(
                    year=now.year + 1, month=1, day=1,
                    hour=0, minute=0, second=0, microsecond=0,
                )
            else:
                next_month = now.replace(
                    month=now.month + 1, day=1,
                    hour=0, minute=0, second=0, microsecond=0,
                )
            ttl = int((next_month - now).total_seconds()) + 3600
        key = f"gateway:budget:{tenant_id}:{user or ''}:{period_str}"
        return key, ttl

    async def configure(self, tenant_id: str, period: str, max_tokens: int) -> None:
        self._period = period
        self._max_tokens = max_tokens

    async def check_and_reserve(
        self, tenant_id: str, user: str | None, estimated: int
    ) -> tuple[bool, int]:
        key, ttl = self._period_key(tenant_id, user)
        # Store the key so record_usage applies the delta to the same period key,
        # avoiding a mismatch if the period rolls over between the two calls.
        self._reservation_keys[(tenant_id, user or "")] = (key, ttl)
        result = await self._r.eval(
            _LUA_CHECK_AND_RESERVE, 1, key,
            str(self._max_tokens), str(estimated), str(ttl),
        )
        return bool(result[0]), int(result[1])

    async def record_usage(
        self, tenant_id: str, user: str | None, actual_tokens: int, estimated: int = 0
    ) -> None:
        """Apply actual-vs-estimated delta to the Redis counter (Finding 4).

        check_and_reserve reserved `estimated` tokens via the Lua script.
        This corrects the counter when actual usage differs from the estimate.
        Uses the key captured at reservation time to avoid period-boundary mismatch.
        """
        delta = actual_tokens - estimated
        if delta == 0:
            return
        ukey = (tenant_id, user or "")
        key_ttl = self._reservation_keys.pop(ukey, None)
        if key_ttl:
            key, ttl = key_ttl
        else:
            key, ttl = self._period_key(tenant_id, user)
        try:
            async with self._r.pipeline(transaction=False) as pipe:
                if delta > 0:
                    pipe.incrby(key, delta)
                else:
                    pipe.decrby(key, -delta)
                pipe.expire(key, ttl)
                await pipe.execute()
        except Exception:
            logger.error(
                "Redis budget record_usage failed: tenant_id=%s user=%s delta=%d",
                tenant_id, user, delta, exc_info=True,
            )

    def get_snapshot(self, tenant_id: str, user: str | None = None) -> dict | None:
        return None  # not implemented for Redis tracker

    def all_snapshots(self) -> list[dict]:
        return []


def make_budget_tracker(redis_client, settings):
    """Return Redis-backed tracker if redis_client is provided, else in-memory."""
    if redis_client is not None:
        return RedisBudgetTracker(
            redis_client,
            settings.token_budget_period,
            settings.token_budget_max_tokens,
        )
    return BudgetTracker()
