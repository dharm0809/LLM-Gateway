"""Tests for shadow policy mode."""
import pytest
from gateway.pipeline.shadow_policy import run_shadow_policies


@pytest.fixture(params=["asyncio"])
def anyio_backend(request):
    return request.param


@pytest.mark.anyio
async def test_shadow_returns_would_block(anyio_backend):
    policies = [{"name": "test-shadow", "version": 1, "rules": [
        {"field": "status", "operator": "equals", "value": "active"}
    ]}]
    context = {"status": "revoked", "model_id": "gpt-4"}
    results = await run_shadow_policies(policies, context)
    assert len(results) == 1
    assert results[0]["policy_name"] == "test-shadow"
    assert results[0]["would_block"] is True


@pytest.mark.anyio
async def test_shadow_returns_would_pass(anyio_backend):
    policies = [{"name": "test-shadow", "version": 1, "rules": [
        {"field": "status", "operator": "equals", "value": "active"}
    ]}]
    context = {"status": "active", "model_id": "gpt-4"}
    results = await run_shadow_policies(policies, context)
    assert results[0]["would_block"] is False


@pytest.mark.anyio
async def test_shadow_empty_policies(anyio_backend):
    results = await run_shadow_policies([], {"status": "active"})
    assert results == []


@pytest.mark.anyio
async def test_shadow_never_raises(anyio_backend):
    """Shadow mode must never raise -- it is observation-only."""
    results = await run_shadow_policies(
        [{"name": "bad", "version": 1, "rules": "invalid"}],
        {"status": "active"},
    )
    assert len(results) == 1
    assert "error" in results[0]


@pytest.mark.anyio
async def test_shadow_multiple_policies(anyio_backend):
    """Multiple policies evaluated independently."""
    policies = [
        {"name": "pol-a", "version": 1, "rules": [
            {"field": "status", "operator": "equals", "value": "active"}
        ]},
        {"name": "pol-b", "version": 2, "rules": [
            {"field": "model_id", "operator": "contains", "value": "gpt"}
        ]},
    ]
    context = {"status": "active", "model_id": "gpt-4"}
    results = await run_shadow_policies(policies, context)
    assert len(results) == 2
    assert results[0]["would_block"] is False
    assert results[1]["would_block"] is False


@pytest.mark.anyio
async def test_shadow_not_equals_operator(anyio_backend):
    policies = [{"name": "ne-test", "version": 1, "rules": [
        {"field": "status", "operator": "not_equals", "value": "active"}
    ]}]
    context = {"status": "active"}
    results = await run_shadow_policies(policies, context)
    # not_equals("active", "active") is False -> rule fails -> would_block
    assert results[0]["would_block"] is True


@pytest.mark.anyio
async def test_shadow_greater_than_operator(anyio_backend):
    policies = [{"name": "gt-test", "version": 1, "rules": [
        {"field": "score", "operator": "greater_than", "value": "0.5"}
    ]}]
    # score=0.8 > 0.5 -> rule passes
    results = await run_shadow_policies(policies, {"score": "0.8"})
    assert results[0]["would_block"] is False
    # score=0.3 > 0.5 is False -> rule fails
    results = await run_shadow_policies(policies, {"score": "0.3"})
    assert results[0]["would_block"] is True


@pytest.mark.anyio
async def test_shadow_unknown_operator(anyio_backend):
    policies = [{"name": "unk-op", "version": 1, "rules": [
        {"field": "status", "operator": "regex_match", "value": ".*"}
    ]}]
    results = await run_shadow_policies(policies, {"status": "active"})
    assert results[0]["would_block"] is True
    assert results[0]["failed_rules"][0]["reason"] == "unknown operator: regex_match"


@pytest.mark.anyio
async def test_shadow_missing_context_field(anyio_backend):
    """Field not in context results in failed rule (actual=None)."""
    policies = [{"name": "missing", "version": 1, "rules": [
        {"field": "nonexistent", "operator": "equals", "value": "anything"}
    ]}]
    results = await run_shadow_policies(policies, {})
    assert results[0]["would_block"] is True
    assert results[0]["failed_rules"][0]["actual"] == "None"
