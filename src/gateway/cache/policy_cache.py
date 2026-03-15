"""Policy cache: versioned policy set. Fail-closed when stale beyond threshold."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class PolicyCacheState:
    version: int
    policies: list[dict[str, Any]]
    fetched_at: datetime


class PolicyCache:
    """Versioned policy set. Staleness threshold triggers fail-closed."""

    def __init__(self, staleness_threshold_seconds: int = 900) -> None:
        self._threshold = staleness_threshold_seconds
        self._state: PolicyCacheState | None = None
        self._version_counter = 0

    def next_version(self) -> int:
        self._version_counter += 1
        return self._version_counter

    def set_policies(self, version: int, policies: list[dict]) -> None:
        self._state = PolicyCacheState(
            version=version,
            policies=list(policies),
            fetched_at=datetime.now(timezone.utc),
        )

    def get_policies(self) -> list[dict]:
        if self._state is None:
            return []
        return self._state.policies

    @property
    def version(self) -> int:
        if self._state is None:
            return 0
        return self._state.version

    @property
    def last_sync(self) -> datetime | None:
        if self._state is None:
            return None
        return self._state.fetched_at

    @property
    def is_stale(self) -> bool:
        if self._state is None:
            return True
        elapsed = (datetime.now(timezone.utc) - self._state.fetched_at).total_seconds()
        return elapsed > self._threshold

    def evaluate(self, attestation_context: dict, tenant_id: str) -> tuple[bool, list[Any], int]:
        """Run policy engine; returns (blocked, results, version). Uses gateway.core."""
        from gateway.core.policy_engine import evaluate_policies

        policies = self.get_policies()
        if not policies:
            return False, [], self.version
        blocked, results = evaluate_policies(attestation_context, policies)
        return blocked, results, self.version
