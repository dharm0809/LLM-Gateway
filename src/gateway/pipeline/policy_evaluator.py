"""Step 2: Pre-inference policy evaluation. Fail-closed when policy cache stale."""

from __future__ import annotations

import logging
from starlette.responses import JSONResponse

from gateway.config import get_settings
from gateway.adapters.base import ModelCall
from gateway.cache.policy_cache import PolicyCache
from gateway.util.redact import RedactedString

logger = logging.getLogger(__name__)


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
        return True, version, policy_result, JSONResponse(
            {"error": "Blocked by policy"},
            status_code=403,
        )
    return False, version, policy_result, None
