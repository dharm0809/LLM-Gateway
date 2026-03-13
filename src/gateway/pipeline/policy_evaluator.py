"""Step 2: Pre-inference policy evaluation. Fail-closed when policy cache stale."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from starlette.responses import JSONResponse

from gateway.config import get_settings
from gateway.adapters.base import ModelCall
from gateway.cache.policy_cache import PolicyCache
from gateway.util.redact import RedactedString

logger = logging.getLogger(__name__)


@dataclass
class PolicyBlockDetail:
    """Structured explanation for a policy block decision."""

    policy_name: str
    policy_version: int
    blocking_rule: dict[str, Any]
    field: str
    expected: str
    actual: str

    def to_response_body(self) -> dict[str, Any]:
        return {
            "error": "Blocked by policy",
            "reason": (
                f"Policy '{self.policy_name}' v{self.policy_version} blocked: "
                f"field '{self.field}' is '{self.actual}', expected '{self.expected}'"
            ),
            "governance_decision": {
                "policy_name": self.policy_name,
                "policy_version": self.policy_version,
                "blocking_rule_field": self.field,
                "blocking_rule_operator": self.blocking_rule.get("operator", "equals"),
                "expected_value": self.expected,
                "actual_value": self.actual,
            },
        }


def _build_policy_block_response(results, version: int) -> dict[str, Any]:
    """Build a structured 403 response body from policy evaluation results.

    Finds the first failing blocking policy result and extracts detail from it.
    Falls back to a generic body if no details are available.
    """
    for r in results:
        if r.result != "fail":
            continue
        details = r.details or {}
        field = details.get("failed_field") or details.get("failed_check", "unknown")
        expected = str(details.get("expected", details.get("required", "unknown")))
        actual = str(details.get("actual", "unknown"))
        detail = PolicyBlockDetail(
            policy_name=r.policy_name or r.policy_id,
            policy_version=version,
            blocking_rule={"field": field, "operator": "equals"},
            field=field,
            expected=expected,
            actual=actual,
        )
        return detail.to_response_body()

    # Fallback — should not happen but be defensive
    return {"error": "Blocked by policy"}


def evaluate_pre_inference(
    policy_cache: PolicyCache,
    call: ModelCall,
    attestation_id: str,
    attestation_context: dict,
) -> tuple[bool, int, str, JSONResponse | None]:
    """
    Evaluate policies against (attestation + prompt context).
    Returns (blocked, policy_version, policy_result, error_response).
    If policy cache is stale, returns (True, 0, "fail_closed", 503 response).
    """
    if policy_cache.is_stale:
        return True, policy_cache.version, "fail_closed", JSONResponse(
            {"error": "Policy cache stale, control plane unreachable"},
            status_code=503,
        )

    context = dict(attestation_context)
    context["prompt"] = {"text": RedactedString(call.prompt_text)}

    tenant_id = attestation_context.get("tenant_id") or get_settings().gateway_tenant_id
    blocked, results, version = policy_cache.evaluate(context, tenant_id)
    policy_result = "blocked_by_policy" if blocked else "pass"
    if blocked:
        body = _build_policy_block_response(results, version)
        return True, version, policy_result, JSONResponse(body, status_code=403)
    return False, version, policy_result, None
