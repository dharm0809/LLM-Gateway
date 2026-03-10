"""Unit tests for per-model circuit breaker registry."""

from gateway.routing.circuit import CircuitBreakerRegistry


def test_breaker_trips_after_n_failures():
    """5 consecutive failures should trip the circuit."""
    reg = CircuitBreakerRegistry(fail_max=5, reset_timeout=30)
    for _ in range(5):
        reg.record_failure("gpt-4")
    assert reg.is_open("gpt-4")


def test_breaker_allows_after_reset_timeout():
    """After reset_timeout expires, success closes the circuit."""
    reg = CircuitBreakerRegistry(fail_max=2, reset_timeout=60)
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
