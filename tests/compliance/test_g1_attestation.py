"""G1 compliance: attestation gate. Evidence for ATO."""

from datetime import datetime, timezone, timedelta
import pytest
from unittest.mock import AsyncMock

from gateway.cache.attestation_cache import AttestationCache, CachedAttestation
from gateway.pipeline.model_resolver import resolve_attestation


@pytest.mark.asyncio
async def test_g1_attested_model_allowed():
    """G1: Request with attested, verified model is allowed (no error response)."""
    cache = AttestationCache(ttl_seconds=300)
    cache.set(CachedAttestation(
        attestation_id="att_20260216_001",
        model_id="gpt-4",
        provider="openai",
        status="verified",
        fetched_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        ttl_seconds=300,
    ))
    entry, err = await resolve_attestation(cache, "openai", "gpt-4")
    assert err is None
    assert entry is not None
    assert entry.attestation_id == "att_20260216_001"


@pytest.mark.asyncio
async def test_g1_unknown_model_blocked():
    """G1: Request with unknown (unattested) model returns 403."""
    cache = AttestationCache(ttl_seconds=300)
    entry, err = await resolve_attestation(cache, "openai", "unknown-model")
    assert err is not None
    assert err.status_code == 403
    assert entry is None


@pytest.mark.asyncio
async def test_g1_revoked_model_blocked():
    """G1: Request with revoked model returns 403."""
    cache = AttestationCache(ttl_seconds=300)
    cache.set(CachedAttestation(
        attestation_id="att_20260216_002",
        model_id="gpt-4-revoked",
        provider="openai",
        status="revoked",
        fetched_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        ttl_seconds=300,
    ))
    entry, err = await resolve_attestation(cache, "openai", "gpt-4-revoked")
    assert err is not None
    assert err.status_code == 403


@pytest.mark.asyncio
async def test_g1_fail_closed_stale_cache():
    """G1 fail-closed: When cache is expired and try_refresh fails (control plane unreachable), return 503."""
    cache = AttestationCache(ttl_seconds=60)
    # Entry that is already expired (fetched_at in the past)
    cache.set(CachedAttestation(
        attestation_id="att_expired",
        model_id="gpt-4",
        provider="openai",
        status="verified",
        fetched_at=datetime.now(timezone.utc) - timedelta(seconds=120),
        ttl_seconds=60,
    ))

    async def try_refresh_fail() -> bool:
        return False

    entry, err = await resolve_attestation(
        cache, "openai", "gpt-4", try_refresh=try_refresh_fail
    )
    assert err is not None
    assert err.status_code == 503
    assert entry is None


@pytest.mark.asyncio
async def test_g1_startup_fails_when_control_plane_unreachable():
    """G1 startup: When startup_sync fails (control plane unreachable), SyncClient.startup_sync raises."""
    from unittest.mock import AsyncMock, patch
    from gateway.cache.attestation_cache import AttestationCache
    from gateway.cache.policy_cache import PolicyCache
    from gateway.sync.sync_client import SyncClient

    cache_att = AttestationCache(ttl_seconds=300)
    cache_pol = PolicyCache(staleness_threshold_seconds=900)
    client = SyncClient(
        control_plane_url="http://controlplane.invalid",
        tenant_id="test-tenant",
        attestation_cache=cache_att,
        policy_cache=cache_pol,
        api_key=None,
    )
    with patch.object(client, "_client", new_callable=AsyncMock) as mock_client:
        mock_client.return_value.get = AsyncMock(side_effect=ConnectionError("unreachable"))
        with pytest.raises(RuntimeError, match="startup sync failed"):
            await client.startup_sync(provider="openai")
