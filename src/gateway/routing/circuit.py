"""Per-model circuit breaker registry."""

from __future__ import annotations

import random
import time


class _CircuitBreaker:
    """Circuit breaker with closed/open/half-open states.

    Enhanced with jitter on recovery, slow-call detection, exponential
    backoff on consecutive opens, and a half-open probe limit.
    """

    STATE_CLOSED = "closed"
    STATE_OPEN = "open"
    STATE_HALF_OPEN = "half-open"

    _MAX_BACKOFF = 300.0  # cap exponential backoff at 5 minutes

    def __init__(
        self,
        fail_max: int,
        reset_timeout: float,
        *,
        jitter: float = 5.0,
        slow_call_threshold: float = 10.0,
        half_open_max_probes: int = 3,
    ):
        self._fail_max = fail_max
        self._reset_timeout = reset_timeout
        self._state = self.STATE_CLOSED
        self._fail_count = 0
        self._opened_at: float = 0.0

        # ── New fields ──────────────────────────────────────────────────
        self._jitter = jitter  # seconds of uniform random jitter
        self._slow_call_threshold = slow_call_threshold  # seconds
        self._half_open_max_probes = half_open_max_probes
        self._half_open_probe_count = 0
        self._half_open_success_count = 0
        self._consecutive_opens = 0
        self._effective_timeout: float = reset_timeout  # includes backoff+jitter

    # ── helpers ──────────────────────────────────────────────────────────

    def _compute_effective_timeout(self) -> float:
        """Reset timeout with exponential backoff + random jitter."""
        backoff = self._reset_timeout * (2 ** self._consecutive_opens)
        if backoff > self._MAX_BACKOFF:
            backoff = self._MAX_BACKOFF
        return backoff + random.uniform(0, self._jitter)

    def _trip_open(self) -> None:
        """Transition to OPEN state, updating backoff and effective timeout."""
        self._state = self.STATE_OPEN
        self._opened_at = time.monotonic()
        self._effective_timeout = self._compute_effective_timeout()
        self._consecutive_opens += 1
        self._half_open_probe_count = 0
        self._half_open_success_count = 0

    def _try_transition_to_half_open(self) -> bool:
        """If enough time has passed, move from OPEN to HALF-OPEN.

        Returns True if the breaker is now HALF-OPEN.
        """
        if self._state == self.STATE_OPEN:
            if time.monotonic() - self._opened_at >= self._effective_timeout:
                self._state = self.STATE_HALF_OPEN
                self._half_open_probe_count = 0
                self._half_open_success_count = 0
                return True
        return self._state == self.STATE_HALF_OPEN

    # ── public API ──────────────────────────────────────────────────────

    @property
    def current_state(self) -> str:
        if self._state == self.STATE_OPEN:
            self._try_transition_to_half_open()
        return self._state

    def allow_request(self) -> bool:
        """Return True if a request should be allowed through.

        In HALF-OPEN state, only ``half_open_max_probes`` requests are
        permitted.  In OPEN state requests are blocked.  CLOSED always
        allows.
        """
        state = self.current_state
        if state == self.STATE_CLOSED:
            return True
        if state == self.STATE_OPEN:
            return False
        # half-open
        if self._half_open_probe_count < self._half_open_max_probes:
            self._half_open_probe_count += 1
            return True
        return False

    def record_success(self) -> None:
        if self._state in (self.STATE_HALF_OPEN, self.STATE_OPEN):
            # Check if timeout passed for open state
            self._try_transition_to_half_open()
            if self._state == self.STATE_HALF_OPEN:
                self._half_open_success_count += 1
                # If all probes used and enough succeeded, close
                if self._half_open_probe_count >= self._half_open_max_probes:
                    if self._half_open_success_count > self._half_open_max_probes // 2:
                        self._close()
                    else:
                        self._trip_open()
                elif self._half_open_success_count >= self._half_open_max_probes:
                    # All probes succeeded early
                    self._close()
                return
        self._fail_count = 0

    def record_failure(self) -> None:
        self._fail_count += 1
        if self._state == self.STATE_HALF_OPEN:
            # In half-open, a failure immediately re-opens
            self._trip_open()
            return
        if self._fail_count >= self._fail_max:
            self._trip_open()

    def record_slow_call(self) -> None:
        """A call that completed but exceeded slow_call_threshold.

        Treated as a failure for circuit-breaking purposes.
        """
        self.record_failure()

    def record_call_duration(self, duration: float, success: bool) -> None:
        """Record a call with its duration.

        If the call took longer than ``slow_call_threshold``, it is
        counted as a failure regardless of ``success``.
        """
        if duration >= self._slow_call_threshold:
            self.record_slow_call()
        elif success:
            self.record_success()
        else:
            self.record_failure()

    def _close(self) -> None:
        """Transition to CLOSED — resets all counters."""
        self._state = self.STATE_CLOSED
        self._fail_count = 0
        self._consecutive_opens = 0
        self._half_open_probe_count = 0
        self._half_open_success_count = 0
        self._effective_timeout = self._reset_timeout


class CircuitBreakerRegistry:
    """Manages per-model circuit breakers for fault isolation."""

    def __init__(
        self,
        fail_max: int = 5,
        reset_timeout: int = 30,
        *,
        jitter: float = 5.0,
        slow_call_threshold: float = 10.0,
        half_open_max_probes: int = 3,
    ):
        self._fail_max = fail_max
        self._reset_timeout = reset_timeout
        self._jitter = jitter
        self._slow_call_threshold = slow_call_threshold
        self._half_open_max_probes = half_open_max_probes
        self._breakers: dict[str, _CircuitBreaker] = {}

    def _get_breaker(self, model_id: str) -> _CircuitBreaker:
        if model_id not in self._breakers:
            self._breakers[model_id] = _CircuitBreaker(
                fail_max=self._fail_max,
                reset_timeout=self._reset_timeout,
                jitter=self._jitter,
                slow_call_threshold=self._slow_call_threshold,
                half_open_max_probes=self._half_open_max_probes,
            )
        return self._breakers[model_id]

    def is_open(self, model_id: str) -> bool:
        """Check if circuit is open (tripped)."""
        return self._get_breaker(model_id).current_state == _CircuitBreaker.STATE_OPEN

    def allow_request(self, model_id: str) -> bool:
        """Check if a request is allowed (respects half-open probe limit)."""
        return self._get_breaker(model_id).allow_request()

    def record_success(self, model_id: str) -> None:
        """Record successful call."""
        self._get_breaker(model_id).record_success()

    def record_failure(self, model_id: str) -> None:
        """Record failed call."""
        self._get_breaker(model_id).record_failure()

    def record_call_duration(self, model_id: str, duration: float, success: bool) -> None:
        """Record a call with its duration for slow-call detection."""
        self._get_breaker(model_id).record_call_duration(duration, success)
