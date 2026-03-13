"""Unit tests for Power-of-Two-Choices (P2C) load balancer."""

from gateway.routing.balancer import Endpoint, LoadBalancer, ModelGroup


def _make_group(pattern="gpt-*", weights=None, count=2):
    weights = weights or [1.0] * count
    endpoints = [
        Endpoint(url=f"https://api{i}.example.com", api_key=f"sk-{i}", weight=w)
        for i, w in enumerate(weights)
    ]
    return ModelGroup(pattern=pattern, endpoints=endpoints)


# ------------------------------------------------------------------
# P2C selection
# ------------------------------------------------------------------


def test_p2c_prefers_less_loaded():
    """P2C should prefer the endpoint with fewer outstanding requests."""
    group = _make_group(count=2)
    lb = LoadBalancer([group])

    # Give endpoint-0 a high outstanding count
    group.endpoints[0].outstanding = 100
    group.endpoints[1].outstanding = 0

    # Over many selections, P2C should always pick the less-loaded one
    for _ in range(50):
        ep = lb.select_endpoint("gpt-4")
        assert ep is not None
        assert ep.url == "https://api1.example.com"


def test_p2c_single_healthy():
    """With only one healthy endpoint, return it directly."""
    group = _make_group(count=2)
    lb = LoadBalancer([group])

    lb.mark_unhealthy("gpt-4", "https://api0.example.com", cooldown_seconds=60)

    for _ in range(20):
        ep = lb.select_endpoint("gpt-4")
        assert ep is not None
        assert ep.url == "https://api1.example.com"


def test_outstanding_increment_decrement():
    """Outstanding counter tracks in-flight requests."""
    group = _make_group(count=1)
    lb = LoadBalancer([group])
    ep = group.endpoints[0]

    assert ep.outstanding == 0

    lb.increment_outstanding(ep)
    assert ep.outstanding == 1

    lb.increment_outstanding(ep)
    assert ep.outstanding == 2

    lb.decrement_outstanding(ep)
    assert ep.outstanding == 1

    lb.decrement_outstanding(ep)
    assert ep.outstanding == 0

    # Decrement below zero clamps to 0
    lb.decrement_outstanding(ep)
    assert ep.outstanding == 0


def test_p2c_equal_outstanding():
    """With equal outstanding counts, either endpoint is valid."""
    group = _make_group(count=2)
    lb = LoadBalancer([group])

    group.endpoints[0].outstanding = 5
    group.endpoints[1].outstanding = 5

    urls = set()
    for _ in range(50):
        ep = lb.select_endpoint("gpt-4")
        assert ep is not None
        urls.add(ep.url)

    # Both endpoints should be returned at some point (randomness)
    assert len(urls) == 2


def test_p2c_with_three_endpoints():
    """P2C with 3 endpoints: heavy-loaded one should rarely win."""
    group = _make_group(count=3)
    lb = LoadBalancer([group])

    group.endpoints[0].outstanding = 100  # heavily loaded
    group.endpoints[1].outstanding = 0
    group.endpoints[2].outstanding = 0

    counts = {ep.url: 0 for ep in group.endpoints}
    for _ in range(300):
        ep = lb.select_endpoint("gpt-4")
        assert ep is not None
        counts[ep.url] += 1

    # Endpoint-0 should be selected far less than the other two
    assert counts["https://api0.example.com"] < 100


# ------------------------------------------------------------------
# Health tracking (unchanged behaviour)
# ------------------------------------------------------------------


def test_unhealthy_endpoint_skipped():
    """Marking an endpoint unhealthy routes all traffic to the healthy one."""
    group = _make_group(count=2)
    lb = LoadBalancer([group])

    lb.mark_unhealthy("gpt-4", "https://api0.example.com", cooldown_seconds=60)

    for _ in range(20):
        ep = lb.select_endpoint("gpt-4")
        assert ep is not None
        assert ep.url == "https://api1.example.com"


def test_cooldown_expires():
    """After cooldown, endpoint becomes available again."""
    group = _make_group(count=2)
    lb = LoadBalancer([group])

    lb.mark_unhealthy("gpt-4", "https://api0.example.com", cooldown_seconds=0.0)
    lb.check_health()

    urls = set()
    for _ in range(50):
        ep = lb.select_endpoint("gpt-4")
        assert ep is not None
        urls.add(ep.url)

    assert "https://api0.example.com" in urls


def test_all_unhealthy_returns_none():
    """When all endpoints are in cooldown, returns None."""
    group = _make_group(count=2)
    lb = LoadBalancer([group])

    lb.mark_unhealthy("gpt-4", "https://api0.example.com", cooldown_seconds=60)
    lb.mark_unhealthy("gpt-4", "https://api1.example.com", cooldown_seconds=60)

    assert lb.select_endpoint("gpt-4") is None


def test_no_matching_group_returns_none():
    """Unmatched model_id returns None."""
    group = _make_group(pattern="gpt-*", count=1)
    lb = LoadBalancer([group])

    assert lb.select_endpoint("claude-3") is None
