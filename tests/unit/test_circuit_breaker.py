"""Unit tests for per-model circuit breaker registry."""

from gateway.routing.circuit import CircuitBreakerRegistry, _CircuitBreaker


# ── Existing tests (backward compat) ────────────────────────────────────────


def test_breaker_trips_after_n_failures():
    """5 consecutive failures should trip the circuit."""
    reg = CircuitBreakerRegistry(fail_max=5, reset_timeout=30)
    for _ in range(5):
        reg.record_failure("gpt-4")
    assert reg.is_open("gpt-4")


def test_breaker_allows_after_reset_timeout():
    """After reset_timeout expires, success closes the circuit."""
    reg = CircuitBreakerRegistry(fail_max=2, reset_timeout=60, jitter=0.0, half_open_max_probes=1)
    reg.record_failure("gpt-4")
    reg.record_failure("gpt-4")
    assert reg.is_open("gpt-4")
    # Simulate timeout expiry by directly setting opened_at in the past
    breaker = reg._get_breaker("gpt-4")
    breaker._opened_at = breaker._opened_at - 61
    # Now it should transition to half-open, and success should close it
    reg.record_success("gpt-4")
    assert not reg.is_open("gpt-4")


def test_separate_breakers_per_model():
    """Tripping model A doesn't affect model B."""
    reg = CircuitBreakerRegistry(fail_max=2, reset_timeout=30)
    reg.record_failure("gpt-4")
    reg.record_failure("gpt-4")
    assert reg.is_open("gpt-4")
    assert not reg.is_open("claude-3")


def test_success_resets_failure_count():
    """A success between failures resets the count."""
    reg = CircuitBreakerRegistry(fail_max=3, reset_timeout=30)
    reg.record_failure("gpt-4")
    reg.record_failure("gpt-4")
    reg.record_success("gpt-4")
    reg.record_failure("gpt-4")
    reg.record_failure("gpt-4")
    # Only 2 consecutive failures after reset, not 3
    assert not reg.is_open("gpt-4")


# ── Jitter tests ────────────────────────────────────────────────────────────


def test_jitter_varies_recovery_time():
    """Open->half-open transition includes random jitter."""
    # Use a generous jitter so the effective timeout is > base reset_timeout
    jitter = 100.0
    reset_timeout = 10.0
    results = set()

    for _ in range(20):
        cb = _CircuitBreaker(
            fail_max=1, reset_timeout=reset_timeout, jitter=jitter
        )
        cb.record_failure()  # trip open
        # effective_timeout should be reset_timeout + uniform(0, jitter)
        results.add(cb._effective_timeout)
        # Must be >= base timeout
        assert cb._effective_timeout >= reset_timeout
        # Must be <= base timeout + jitter
        assert cb._effective_timeout <= reset_timeout + jitter

    # With 20 samples and jitter=100, getting the *same* float is astronomically unlikely
    assert len(results) > 1, "Jitter should produce varying effective timeouts"


def test_jitter_zero_gives_exact_timeout():
    """With jitter=0 the effective timeout equals base reset_timeout."""
    cb = _CircuitBreaker(fail_max=1, reset_timeout=30.0, jitter=0.0)
    cb.record_failure()
    assert cb._effective_timeout == 30.0


def test_jitter_affects_half_open_transition():
    """The half-open transition respects effective_timeout (base + jitter), not just base."""
    cb = _CircuitBreaker(fail_max=1, reset_timeout=10.0, jitter=0.0)
    cb.record_failure()
    assert cb.current_state == _CircuitBreaker.STATE_OPEN

    # Move opened_at back by base timeout only — should transition
    cb._opened_at -= 11.0
    assert cb.current_state == _CircuitBreaker.STATE_HALF_OPEN

    # Now trip again, with jitter this time — second open doubles via backoff
    cb2 = _CircuitBreaker(fail_max=1, reset_timeout=10.0, jitter=0.0)
    cb2.record_failure()  # first open: effective = 10
    assert cb2._effective_timeout == 10.0
    # Manually move to half-open and fail again
    cb2._opened_at -= 11.0
    _ = cb2.current_state  # transition to half-open
    cb2.record_failure()  # re-open: consecutive_opens=2, effective = 10*2^1 = 20
    assert cb2._effective_timeout == 20.0


# ── Slow-call detection ────────────────────────────────────────────────────


def test_slow_call_counts_as_failure():
    """Call exceeding slow_call_threshold counts as failure."""
    cb = _CircuitBreaker(
        fail_max=2, reset_timeout=30.0, jitter=0.0, slow_call_threshold=5.0
    )
    # Two slow calls (even though they "succeeded") should trip the breaker
    cb.record_call_duration(duration=6.0, success=True)
    assert cb.current_state == _CircuitBreaker.STATE_CLOSED
    cb.record_call_duration(duration=6.0, success=True)
    assert cb.current_state == _CircuitBreaker.STATE_OPEN


def test_fast_successful_call_does_not_count_as_failure():
    """Call under slow_call_threshold with success does not trip."""
    cb = _CircuitBreaker(
        fail_max=2, reset_timeout=30.0, jitter=0.0, slow_call_threshold=5.0
    )
    cb.record_call_duration(duration=1.0, success=True)
    cb.record_call_duration(duration=4.9, success=True)
    assert cb.current_state == _CircuitBreaker.STATE_CLOSED


def test_slow_call_via_registry():
    """Registry-level record_call_duration triggers slow-call detection."""
    reg = CircuitBreakerRegistry(
        fail_max=1, reset_timeout=30, jitter=0.0, slow_call_threshold=2.0
    )
    reg.record_call_duration("gpt-4", duration=3.0, success=True)
    assert reg.is_open("gpt-4")


# ── Exponential backoff ─────────────────────────────────────────────────────


def test_exponential_backoff_on_consecutive_opens():
    """Recovery timeout doubles on each consecutive open."""
    cb = _CircuitBreaker(fail_max=1, reset_timeout=10.0, jitter=0.0)

    # First open: effective = 10 * 2^0 = 10
    cb.record_failure()
    assert cb._effective_timeout == 10.0
    assert cb._consecutive_opens == 1

    # Move to half-open, then fail again => second open
    cb._opened_at -= 11.0
    _ = cb.current_state  # half-open
    cb.record_failure()  # re-open
    assert cb._effective_timeout == 20.0  # 10 * 2^1
    assert cb._consecutive_opens == 2

    # Third open
    cb._opened_at -= 21.0
    _ = cb.current_state
    cb.record_failure()
    assert cb._effective_timeout == 40.0  # 10 * 2^2
    assert cb._consecutive_opens == 3


def test_exponential_backoff_capped_at_max():
    """Backoff is capped at _MAX_BACKOFF (300s)."""
    cb = _CircuitBreaker(fail_max=1, reset_timeout=100.0, jitter=0.0)

    # Force many consecutive opens
    for _ in range(10):
        cb.record_failure()
        # Move past timeout so next failure can re-open from half-open
        cb._opened_at -= cb._effective_timeout + 1
        _ = cb.current_state  # transition to half-open

    # The last trip
    cb.record_failure()
    assert cb._effective_timeout <= _CircuitBreaker._MAX_BACKOFF


# ── Half-open probe limit ───────────────────────────────────────────────────


def test_half_open_probe_limit():
    """Only half_open_max_probes requests allowed in half-open state."""
    cb = _CircuitBreaker(
        fail_max=1, reset_timeout=10.0, jitter=0.0, half_open_max_probes=3
    )
    cb.record_failure()
    assert cb.current_state == _CircuitBreaker.STATE_OPEN

    # Expire timeout
    cb._opened_at -= 11.0
    assert cb.current_state == _CircuitBreaker.STATE_HALF_OPEN

    # First 3 probes should be allowed
    assert cb.allow_request() is True  # probe 1
    assert cb.allow_request() is True  # probe 2
    assert cb.allow_request() is True  # probe 3
    # 4th should be rejected
    assert cb.allow_request() is False


def test_half_open_probes_all_succeed_closes_circuit():
    """If all probes succeed in half-open, circuit closes."""
    cb = _CircuitBreaker(
        fail_max=1, reset_timeout=10.0, jitter=0.0, half_open_max_probes=3
    )
    cb.record_failure()
    cb._opened_at -= 11.0
    _ = cb.current_state  # half-open

    # Simulate 3 probes
    cb.allow_request()
    cb.record_success()
    cb.allow_request()
    cb.record_success()
    cb.allow_request()
    cb.record_success()  # third success triggers close

    assert cb.current_state == _CircuitBreaker.STATE_CLOSED


def test_half_open_probe_failure_reopens():
    """A failure during half-open probe re-opens the circuit."""
    cb = _CircuitBreaker(
        fail_max=1, reset_timeout=10.0, jitter=0.0, half_open_max_probes=3
    )
    cb.record_failure()
    cb._opened_at -= 11.0
    _ = cb.current_state  # half-open

    cb.allow_request()
    cb.record_failure()  # immediate re-open

    assert cb.current_state == _CircuitBreaker.STATE_OPEN


def test_half_open_probe_limit_via_registry():
    """Registry-level allow_request respects half-open probe limit."""
    reg = CircuitBreakerRegistry(
        fail_max=1, reset_timeout=10, jitter=0.0, half_open_max_probes=2
    )
    reg.record_failure("m1")
    breaker = reg._get_breaker("m1")
    breaker._opened_at -= 11.0

    assert reg.allow_request("m1") is True   # probe 1
    assert reg.allow_request("m1") is True   # probe 2
    assert reg.allow_request("m1") is False  # blocked


# ── Consecutive opens reset on close ────────────────────────────────────────


def test_consecutive_opens_reset_on_close():
    """Successful close resets consecutive_opens counter."""
    cb = _CircuitBreaker(
        fail_max=1, reset_timeout=10.0, jitter=0.0, half_open_max_probes=1
    )

    # Open twice to accumulate consecutive_opens
    cb.record_failure()  # open #1
    assert cb._consecutive_opens == 1
    cb._opened_at -= 11.0
    _ = cb.current_state  # half-open
    cb.record_failure()  # open #2
    assert cb._consecutive_opens == 2

    # Now close successfully
    cb._opened_at -= 21.0
    _ = cb.current_state  # half-open
    cb.allow_request()  # consume probe
    cb.record_success()  # close
    assert cb.current_state == _CircuitBreaker.STATE_CLOSED
    assert cb._consecutive_opens == 0

    # Next open should start fresh at base timeout
    cb.record_failure()
    assert cb._effective_timeout == 10.0
    assert cb._consecutive_opens == 1


def test_allow_request_closed_always_true():
    """In closed state, allow_request always returns True."""
    cb = _CircuitBreaker(fail_max=5, reset_timeout=30.0, jitter=0.0)
    for _ in range(10):
        assert cb.allow_request() is True


def test_allow_request_open_always_false():
    """In open state, allow_request always returns False."""
    cb = _CircuitBreaker(fail_max=1, reset_timeout=30.0, jitter=0.0)
    cb.record_failure()
    assert cb.current_state == _CircuitBreaker.STATE_OPEN
    assert cb.allow_request() is False
    assert cb.allow_request() is False
