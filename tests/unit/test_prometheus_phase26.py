"""Unit tests for Phase 26 Prometheus metrics and alert/rate-limiter init."""

import pytest

from gateway.metrics.prometheus import (
    budget_utilization_ratio,
    content_blocks_total,
    rate_limit_hits_total,
)


@pytest.fixture(params=["asyncio"])
def anyio_backend(request):
    return request.param


def test_budget_utilization_ratio_gauge_exists():
    """budget_utilization_ratio is a Gauge with tenant_id label."""
    assert budget_utilization_ratio is not None
    # Gauges have a set method
    assert hasattr(budget_utilization_ratio.labels(tenant_id="t1"), "set")


def test_content_blocks_total_counter_exists():
    """content_blocks_total is a Counter with analyzer label."""
    assert content_blocks_total is not None
    assert hasattr(content_blocks_total.labels(analyzer="pii"), "inc")


def test_rate_limit_hits_total_counter_exists():
    """rate_limit_hits_total is a Counter with model label."""
    assert rate_limit_hits_total is not None
    assert hasattr(rate_limit_hits_total.labels(model="gpt-4"), "inc")


@pytest.mark.anyio
async def test_alert_bus_init_in_startup():
    """Alert bus is initialized and wired to budget tracker during startup."""
    from unittest.mock import patch, MagicMock
    from gateway.pipeline.context import PipelineContext

    ctx = PipelineContext()
    settings = MagicMock()
    settings.webhook_urls = "https://example.com/webhook"
    settings.pagerduty_routing_key = ""
    settings.alert_budget_thresholds = "70,90,100"
    settings.token_budget_enabled = True
    settings.token_budget_max_tokens = 1000
    settings.token_budget_period = "monthly"
    settings.gateway_tenant_id = "t1"
    settings.redis_url = ""

    from gateway.main import _init_alert_bus, _init_budget_tracker

    ctx.redis_client = None
    _init_alert_bus(settings, ctx)
    assert ctx.alert_bus is not None
    # Budget tracker should pick up the alert bus
    _init_budget_tracker(settings, ctx)
    assert ctx.budget_tracker is not None


@pytest.mark.anyio
async def test_rate_limiter_init_in_startup():
    """Rate limiter is initialized during startup when enabled."""
    from unittest.mock import MagicMock
    from gateway.pipeline.context import PipelineContext

    ctx = PipelineContext()
    settings = MagicMock()
    settings.rate_limit_enabled = True
    settings.redis_url = ""

    from gateway.main import _init_rate_limiter

    ctx.redis_client = None
    _init_rate_limiter(settings, ctx)
    assert ctx.rate_limiter is not None
