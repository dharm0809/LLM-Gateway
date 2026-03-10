# tests/unit/test_governance_headers.py
import pytest


def test_governance_headers_added_to_non_streaming_response():
    """Non-streaming responses include X-Walacor-* governance headers."""
    from starlette.responses import JSONResponse
    from gateway.pipeline.orchestrator import _add_governance_headers

    response = JSONResponse({"choices": [{"message": {"content": "hello"}}]})
    _add_governance_headers(
        response,
        execution_id="exec-123",
        attestation_id="self-attested:qwen3:4b",
        chain_seq=5,
        policy_result="allowed",
    )

    assert response.headers["x-walacor-execution-id"] == "exec-123"
    assert response.headers["x-walacor-attestation-id"] == "self-attested:qwen3:4b"
    assert response.headers["x-walacor-chain-seq"] == "5"
    assert response.headers["x-walacor-policy-result"] == "allowed"


def test_governance_headers_missing_values_omitted():
    """Headers with None values are not added."""
    from starlette.responses import JSONResponse
    from gateway.pipeline.orchestrator import _add_governance_headers

    response = JSONResponse({})
    _add_governance_headers(response, execution_id="exec-1", attestation_id=None, chain_seq=None, policy_result="allowed")

    assert "x-walacor-execution-id" in response.headers
    assert "x-walacor-attestation-id" not in response.headers
    assert "x-walacor-chain-seq" not in response.headers
