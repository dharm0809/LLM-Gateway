"""Unit tests for Redis-backed session chain and budget trackers (mocked)."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

# Pin anyio tests to asyncio backend (AsyncMock is asyncio-specific)
@pytest.fixture(params=["asyncio"])
def anyio_backend(request):
    return request.param

from gateway.pipeline.session_chain import (
    RedisSessionChainTracker,
    SessionChainTracker,
    make_session_chain_tracker,
    GENESIS_HASH,
)
from gateway.pipeline.budget_tracker import (
    RedisBudgetTracker,
    BudgetTracker,
    make_budget_tracker,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_redis():
    """Build a minimal async Redis mock with pipeline support."""
    client = MagicMock()
    pipe = AsyncMock()
    pipe.__aenter__ = AsyncMock(return_value=pipe)
    pipe.__aexit__ = AsyncMock(return_value=False)
    client.pipeline = MagicMock(return_value=pipe)
    return client, pipe


# ---------------------------------------------------------------------------
# RedisSessionChainTracker
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_redis_session_next_chain_values_first_call_returns_genesis():
    client, pipe = _mock_redis()
    # First call: no existing seq or hash (HGET returns None for both)
    # New implementation: HGET seq, HGET hash, EXPIRE
    pipe.execute = AsyncMock(return_value=[None, None, True])

    tracker = RedisSessionChainTracker(client, ttl=3600)
    seq, prev = await tracker.next_chain_values("sess-abc")

    # First record should be seq=0, matching in-memory SessionChainTracker (Finding 1 fix)
    assert seq == 0
    assert prev == GENESIS_HASH


@pytest.mark.anyio
async def test_redis_session_next_chain_values_subsequent_call_returns_stored_hash():
    client, pipe = _mock_redis()
    stored_hash = "a" * 128
    # Redis stores seq=1 (last written); next call returns (2, stored_hash)
    pipe.execute = AsyncMock(return_value=[b"1", stored_hash.encode(), True])

    tracker = RedisSessionChainTracker(client, ttl=3600)
    seq, prev = await tracker.next_chain_values("sess-abc")

    assert seq == 2
    assert prev == stored_hash


@pytest.mark.anyio
async def test_redis_session_update_writes_seq_hash_and_expire():
    """update() must write BOTH seq and hash atomically (Finding 3 fix)."""
    client, pipe = _mock_redis()
    pipe.execute = AsyncMock(return_value=[1, 1, 1])

    tracker = RedisSessionChainTracker(client, ttl=3600)
    await tracker.update("sess-abc", 1, "hash123")

    # Both seq and hash must be written
    pipe.hset.assert_any_call("gateway:session:sess-abc", "seq", 1)
    pipe.hset.assert_any_call("gateway:session:sess-abc", "hash", "hash123")
    assert pipe.hset.call_count == 2
    pipe.expire.assert_called_once_with("gateway:session:sess-abc", 3600)
    pipe.execute.assert_awaited_once()


@pytest.mark.anyio
async def test_redis_session_active_session_count_returns_sentinel():
    client, _ = _mock_redis()
    tracker = RedisSessionChainTracker(client, ttl=3600)
    assert tracker.active_session_count() == -1


# ---------------------------------------------------------------------------
# RedisSessionChainTracker key format
# ---------------------------------------------------------------------------

def test_redis_session_key_format():
    client, _ = _mock_redis()
    tracker = RedisSessionChainTracker(client, ttl=600)
    assert tracker._key("my-session") == "gateway:session:my-session"


# ---------------------------------------------------------------------------
# RedisBudgetTracker
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_redis_budget_check_and_reserve_calls_eval_with_correct_args():
    client = MagicMock()
    client.eval = AsyncMock(return_value=[1, 900])

    tracker = RedisBudgetTracker(client, period="monthly", max_tokens=1000)
    allowed, remaining = await tracker.check_and_reserve("tenant-1", None, 100)

    assert allowed is True
    assert remaining == 900
    client.eval.assert_awaited_once()
    call_args = client.eval.call_args
    # KEYS[1] should start with "gateway:budget:tenant-1::"
    assert call_args.args[2].startswith("gateway:budget:tenant-1::")
    # max_tokens arg
    assert call_args.args[3] == "1000"
    # estimated arg
    assert call_args.args[4] == "100"


@pytest.mark.anyio
async def test_redis_budget_check_and_reserve_blocked():
    client = MagicMock()
    client.eval = AsyncMock(return_value=[0, 50])  # blocked, 50 remaining

    tracker = RedisBudgetTracker(client, period="daily", max_tokens=100)
    allowed, remaining = await tracker.check_and_reserve("tenant-1", "user-a", 200)

    assert allowed is False
    assert remaining == 50


def _mock_budget_redis_pipeline():
    """Build a Redis mock that supports .pipeline() for the refactored record_usage."""
    client = MagicMock()
    pipe = AsyncMock()
    pipe.__aenter__ = AsyncMock(return_value=pipe)
    pipe.__aexit__ = AsyncMock(return_value=False)
    pipe.execute = AsyncMock(return_value=[1, True])
    client.pipeline = MagicMock(return_value=pipe)
    client.eval = AsyncMock(return_value=[1, 900])
    return client, pipe


@pytest.mark.anyio
async def test_redis_budget_record_usage_applies_positive_delta():
    """record_usage with actual > estimated applies INCRBY delta via pipeline (Finding 4 fix)."""
    client, pipe = _mock_budget_redis_pipeline()

    tracker = RedisBudgetTracker(client, period="monthly", max_tokens=1000)
    # Seed the reservation key so record_usage uses it
    await tracker.check_and_reserve("tenant-1", None, 80)
    await tracker.record_usage("tenant-1", None, 120, estimated=80)

    # delta = 120 - 80 = 40 → pipe.incrby(key, 40)
    pipe.incrby.assert_called_once()
    assert pipe.incrby.call_args.args[1] == 40
    pipe.execute.assert_awaited()


@pytest.mark.anyio
async def test_redis_budget_record_usage_applies_negative_delta():
    """record_usage with actual < estimated applies DECRBY to refund over-reservation."""
    client, pipe = _mock_budget_redis_pipeline()

    tracker = RedisBudgetTracker(client, period="monthly", max_tokens=1000)
    await tracker.check_and_reserve("tenant-1", None, 100)
    await tracker.record_usage("tenant-1", None, 60, estimated=100)

    # delta = 60 - 100 = -40 → pipe.decrby(key, 40)
    pipe.decrby.assert_called_once()
    assert pipe.decrby.call_args.args[1] == 40
    pipe.execute.assert_awaited()


@pytest.mark.anyio
async def test_redis_budget_record_usage_zero_delta_is_noop():
    """record_usage when actual == estimated makes no Redis pipeline calls."""
    client, pipe = _mock_budget_redis_pipeline()

    tracker = RedisBudgetTracker(client, period="monthly", max_tokens=1000)
    await tracker.record_usage("tenant-1", None, 100, estimated=100)

    pipe.incrby.assert_not_called()
    pipe.decrby.assert_not_called()
    pipe.execute.assert_not_awaited()


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------

def test_make_session_chain_tracker_no_redis_returns_in_memory():
    settings = MagicMock()
    settings.session_chain_max_sessions = 1000
    settings.session_chain_ttl = 3600
    tracker = make_session_chain_tracker(None, settings)
    assert isinstance(tracker, SessionChainTracker)


def test_make_session_chain_tracker_with_redis_returns_redis_tracker():
    client = MagicMock()
    settings = MagicMock()
    settings.session_chain_ttl = 3600
    tracker = make_session_chain_tracker(client, settings)
    assert isinstance(tracker, RedisSessionChainTracker)


def test_make_budget_tracker_no_redis_returns_in_memory():
    settings = MagicMock()
    settings.token_budget_period = "monthly"
    settings.token_budget_max_tokens = 1000
    tracker = make_budget_tracker(None, settings)
    assert isinstance(tracker, BudgetTracker)


def test_make_budget_tracker_with_redis_returns_redis_tracker():
    client = MagicMock()
    settings = MagicMock()
    settings.token_budget_period = "monthly"
    settings.token_budget_max_tokens = 1000
    tracker = make_budget_tracker(client, settings)
    assert isinstance(tracker, RedisBudgetTracker)


# ---------------------------------------------------------------------------
# In-memory SessionChainTracker — async interface
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_in_memory_session_chain_async_interface():
    tracker = SessionChainTracker(max_sessions=100, ttl_seconds=3600)
    seq, prev = await tracker.next_chain_values("s1")
    assert seq == 0
    assert prev == GENESIS_HASH

    await tracker.update("s1", 0, "hash-abc")

    seq2, prev2 = await tracker.next_chain_values("s1")
    assert seq2 == 1
    assert prev2 == "hash-abc"


# ---------------------------------------------------------------------------
# In-memory BudgetTracker — reservation semantics
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_in_memory_budget_tracker_reserves_on_check():
    """check_and_reserve deducts estimated immediately (Finding 2 fix)."""
    tracker = BudgetTracker()
    tracker.configure("t1", None, "monthly", 1000)

    allowed, remaining = await tracker.check_and_reserve("t1", None, 100)
    assert allowed is True
    # Reservation is immediate: 1000 - 100 = 900 remaining
    assert remaining == 900


@pytest.mark.anyio
async def test_in_memory_budget_tracker_delta_correction():
    """record_usage applies actual-estimated delta to adjust the reservation."""
    tracker = BudgetTracker()
    tracker.configure("t1", None, "monthly", 1000)

    # Reserve 100 estimated
    await tracker.check_and_reserve("t1", None, 100)
    # Actual was 120: delta = 120 - 100 = +20
    await tracker.record_usage("t1", None, 120, estimated=100)

    # tokens_used = 100 (reserved) + 20 (delta) = 120
    # Next reservation of 100: remaining = 1000 - 120 - 100 = 780
    allowed2, remaining2 = await tracker.check_and_reserve("t1", None, 100)
    assert allowed2 is True
    assert remaining2 == 780


@pytest.mark.anyio
async def test_in_memory_budget_tracker_refund_when_actual_less():
    """record_usage refunds when actual < estimated."""
    tracker = BudgetTracker()
    tracker.configure("t1", None, "monthly", 1000)

    # Reserve 200 estimated
    await tracker.check_and_reserve("t1", None, 200)
    # Actual was only 50: delta = 50 - 200 = -150 (refund)
    await tracker.record_usage("t1", None, 50, estimated=200)

    # tokens_used = 200 - 150 = 50
    allowed2, remaining2 = await tracker.check_and_reserve("t1", None, 100)
    assert allowed2 is True
    assert remaining2 == 850  # 1000 - 50 - 100


@pytest.mark.anyio
async def test_in_memory_budget_tracker_blocks_when_exhausted():
    """check_and_reserve returns (False, remaining) when budget is exhausted."""
    tracker = BudgetTracker()
    tracker.configure("t1", None, "monthly", 100)

    # Reserve nearly all budget
    allowed, _ = await tracker.check_and_reserve("t1", None, 90)
    assert allowed is True

    # Next request wants 20 but only 10 remain → blocked
    allowed2, remaining2 = await tracker.check_and_reserve("t1", None, 20)
    assert allowed2 is False
    assert remaining2 == 10


@pytest.mark.anyio
async def test_in_memory_budget_tracker_no_budget_configured_always_allows():
    """When no budget is configured for a tenant, all requests are allowed."""
    tracker = BudgetTracker()
    # No configure() call
    allowed, remaining = await tracker.check_and_reserve("t1", None, 1_000_000)
    assert allowed is True
    assert remaining == -1  # -1 = unlimited


# ---------------------------------------------------------------------------
# Redis error resilience (new error handling)
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_redis_session_next_chain_values_redis_error_returns_fallback():
    """On Redis failure, next_chain_values returns (0, GENESIS_HASH) so the request proceeds."""
    client, pipe = _mock_redis()
    pipe.execute = AsyncMock(side_effect=Exception("Redis connection refused"))

    tracker = RedisSessionChainTracker(client, ttl=3600)
    seq, prev = await tracker.next_chain_values("sess-err")

    assert seq == 0
    assert prev == GENESIS_HASH


@pytest.mark.anyio
async def test_redis_session_update_redis_error_does_not_raise():
    """On Redis failure, update() logs and swallows the error (chain breaks but request succeeds)."""
    client, pipe = _mock_redis()
    pipe.execute = AsyncMock(side_effect=Exception("Redis timeout"))

    tracker = RedisSessionChainTracker(client, ttl=3600)
    # Should not raise
    await tracker.update("sess-err", 1, "hash123")


@pytest.mark.anyio
async def test_redis_session_next_chain_values_str_hash_decoded_correctly():
    """prev_hash is returned correctly whether Redis returns bytes or str."""
    client, pipe = _mock_redis()
    stored_hash = "b" * 128
    # Return str instead of bytes (decode_responses=True Redis client)
    pipe.execute = AsyncMock(return_value=[b"2", stored_hash, True])  # hash is already str

    tracker = RedisSessionChainTracker(client, ttl=3600)
    seq, prev = await tracker.next_chain_values("sess-str")

    assert seq == 3
    assert prev == stored_hash


@pytest.mark.anyio
async def test_redis_budget_record_usage_uses_reservation_key():
    """record_usage uses the key stored by check_and_reserve to avoid period-boundary mismatch."""
    client = MagicMock()
    client.eval = AsyncMock(return_value=[1, 900])
    pipe = AsyncMock()
    pipe.__aenter__ = AsyncMock(return_value=pipe)
    pipe.__aexit__ = AsyncMock(return_value=False)
    pipe.execute = AsyncMock(return_value=[1, True])
    client.pipeline = MagicMock(return_value=pipe)

    tracker = RedisBudgetTracker(client, period="monthly", max_tokens=1000)

    # Reserve tokens — this stores the period key internally
    await tracker.check_and_reserve("t1", None, 100)

    # Confirm the reservation key was stored
    assert ("t1", "") in tracker._reservation_keys

    # Call record_usage — should pop the stored key
    await tracker.record_usage("t1", None, 120, estimated=100)

    # Key should be consumed
    assert ("t1", "") not in tracker._reservation_keys
    pipe.execute.assert_awaited_once()


@pytest.mark.anyio
async def test_redis_budget_record_usage_redis_error_does_not_raise():
    """On Redis pipeline failure, record_usage logs and does not propagate the error."""
    client = MagicMock()
    pipe = AsyncMock()
    pipe.__aenter__ = AsyncMock(return_value=pipe)
    pipe.__aexit__ = AsyncMock(return_value=False)
    pipe.execute = AsyncMock(side_effect=Exception("Redis down"))
    client.pipeline = MagicMock(return_value=pipe)

    tracker = RedisBudgetTracker(client, period="monthly", max_tokens=1000)
    # Should not raise
    await tracker.record_usage("t1", None, 120, estimated=100)
