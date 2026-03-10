"""Per-model circuit breaker registry."""

from __future__ import annotations

import time


class _CircuitBreaker:
    """Simple circuit breaker with closed/open/half-open states."""

    STATE_CLOSED = "closed"
    STATE_OPEN = "open"
    STATE_HALF_OPEN = "half-open"

    def __init__(self, fail_max: int, reset_timeout: float):
        self._fail_max = fail_max
        self._reset_timeout = reset_timeout
        self._state = self.STATE_CLOSED
        self._fail_count = 0
        self._opened_at: float = 0.0

    @property
    def current_state(self) -> str:
        if self._state == self.STATE_OPEN:
            if time.monotonic() - self._opened_at >= self._reset_timeout:
                self._state = self.STATE_HALF_OPEN
        return self._state

    def record_success(self):
        if self._state in (self.STATE_HALF_OPEN, self.STATE_OPEN):
            # Check if timeout passed for open state
            if self._state == self.STATE_OPEN:
                if time.monotonic() - self._opened_at >= self._reset_timeout:
                    self._state = self.STATE_HALF_OPEN
            if self._state == self.STATE_HALF_OPEN:
                self._state = self.STATE_CLOSED
                self._fail_count = 0
                return
        self._fail_count = 0

    def record_failure(self):
        self._fail_count += 1
        if self._fail_count >= self._fail_max:
            self._state = self.STATE_OPEN
            self._opened_at = time.monotonic()


class CircuitBreakerRegistry:
    """Manages per-model circuit breakers for fault isolation."""

    def __init__(self, fail_max: int = 5, reset_timeout: int = 30):
        self._fail_max = fail_max
        self._reset_timeout = reset_timeout
        self._breakers: dict[str, _CircuitBreaker] = {}

    def _get_breaker(self, model_id: str) -> _CircuitBreaker:
        if model_id not in self._breakers:
            self._breakers[model_id] = _CircuitBreaker(
                fail_max=self._fail_max,
                reset_timeout=self._reset_timeout,
            )
        return self._breakers[model_id]

    def is_open(self, model_id: str) -> bool:
        """Check if circuit is open (tripped)."""
        return self._get_breaker(model_id).current_state == _CircuitBreaker.STATE_OPEN

    def record_success(self, model_id: str):
        """Record successful call."""
        self._get_breaker(model_id).record_success()

    def record_failure(self, model_id: str):
        """Record failed call."""
        self._get_breaker(model_id).record_failure()
