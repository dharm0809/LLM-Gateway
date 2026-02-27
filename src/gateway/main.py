"""ASGI app entry point."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

import httpx
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from gateway.config import get_settings
from gateway.pipeline.orchestrator import handle_request
from gateway.pipeline.context import get_pipeline_context
from gateway.health import health_response, metrics_response
from gateway.auth.api_key import require_api_key_if_configured
from gateway.middleware.completeness import completeness_middleware

from gateway.util.json_logger import configure_json_logging
configure_json_logging(os.environ.get("WALACOR_LOG_LEVEL", "INFO"))
logger = logging.getLogger(__name__)


async def api_key_middleware(request: Request, call_next):
    """When WALACOR_GATEWAY_API_KEYS is set, require valid API key on proxy routes."""
    if request.url.path in ("/health", "/metrics"):
        return await call_next(request)
    settings = get_settings()
    err = require_api_key_if_configured(request, settings.api_keys_list)
    if err is not None:
        request.state.walacor_disposition = "denied_auth"
        return err
    return await call_next(request)


# CORS headers for browser clients (e.g. gateway-chat.html loaded from file://).
_CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, Authorization, X-API-Key",
    "Access-Control-Max-Age": "86400",
}


async def cors_middleware(request: Request, call_next):
    """Handle CORS preflight (OPTIONS) and add CORS headers to responses."""
    if request.method == "OPTIONS":
        return Response(status_code=200, headers=_CORS_HEADERS)
    response = await call_next(request)
    for key, value in _CORS_HEADERS.items():
        response.headers[key] = value
    return response


async def catch_all_post(request: Request):
    return await handle_request(request)


async def _self_test() -> None:
    """Verify critical subsystems before accepting traffic. Raises on failure."""
    from datetime import datetime, timezone
    from urllib.parse import urlparse

    settings = get_settings()
    ctx = get_pipeline_context()

    # Hash self-test: SHA3-512 available (used for session chain); gateway does not hash prompt/response.
    from walacor_core import compute_sha3_512_string
    h = compute_sha3_512_string("self-test")
    if len(h) != 128:
        raise RuntimeError(f"Hash self-test failed: expected length 128, got {len(h)}")

    # Control plane URL validation (governance mode only).
    if not settings.skip_governance and not settings.walacor_storage_enabled:
        parsed = urlparse(settings.control_plane_url)
        if parsed.scheme not in ("http", "https"):
            raise RuntimeError(f"Control plane URL invalid scheme: {parsed.scheme}")

    # WAL write/deliver smoke-test (WAL mode only). Record is dict (no prompt_hash/response_hash).
    if ctx.wal_writer:
        record = {
            "execution_id": "self-test-startup",
            "model_attestation_id": "self-test",
            "policy_version": 0,
            "policy_result": "pass",
            "tenant_id": settings.gateway_tenant_id,
            "gateway_id": settings.gateway_id,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        ctx.wal_writer.write_and_fsync(record)
        ctx.wal_writer.mark_delivered(record["execution_id"])

    logger.info("Startup self-test passed")


async def _init_governance(settings, ctx) -> None:
    """Phase 1-4: caches, sync client, startup sync."""
    from gateway.cache.attestation_cache import AttestationCache
    from gateway.cache.policy_cache import PolicyCache
    from gateway.sync.sync_client import SyncClient

    ctx.attestation_cache = AttestationCache(ttl_seconds=settings.attestation_cache_ttl)
    ctx.policy_cache = PolicyCache(staleness_threshold_seconds=settings.policy_staleness_threshold)
    control_plane_key = (settings.control_plane_api_key or "").strip() or None
    ctx.sync_client = SyncClient(
        control_plane_url=settings.control_plane_url,
        tenant_id=settings.gateway_tenant_id,
        attestation_cache=ctx.attestation_cache,
        policy_cache=ctx.policy_cache,
        api_key=control_plane_key,
    )
    await ctx.sync_client.startup_sync(provider=settings.gateway_provider)


def _init_wal(settings, ctx) -> None:
    """Phase 2: WAL writer and delivery worker."""
    from gateway.wal.writer import WALWriter
    from gateway.wal.delivery_worker import DeliveryWorker

    wal_dir = Path(settings.wal_path)
    wal_dir.mkdir(parents=True, exist_ok=True)
    ctx.wal_writer = WALWriter(str(wal_dir / "wal.db"))
    ctx.delivery_worker = DeliveryWorker(ctx.wal_writer)
    ctx.delivery_worker.start()


async def _init_walacor(settings, ctx) -> None:
    """Walacor backend storage: authenticate and warm up the client."""
    from gateway.walacor.client import WalacorClient

    ctx.walacor_client = WalacorClient(
        server=settings.walacor_server,
        username=settings.walacor_username,
        password=settings.walacor_password,
        executions_etid=settings.walacor_executions_etid,
        attempts_etid=settings.walacor_attempts_etid,
        tool_events_etid=settings.walacor_tool_events_etid,
    )
    await ctx.walacor_client.start()
    logger.info(
        "Walacor storage ready: executions_etid=%d attempts_etid=%d",
        settings.walacor_executions_etid, settings.walacor_attempts_etid,
    )


def _init_content_analyzers(settings, ctx) -> None:
    """Phase 10: PII and toxicity content analyzers."""
    from gateway.content.pii_detector import PIIDetector
    from gateway.content.toxicity_detector import ToxicityDetector

    if settings.pii_detection_enabled:
        ctx.content_analyzers.append(PIIDetector())
        logger.info("Content analyzer loaded: walacor.pii.v1")
    if settings.toxicity_detection_enabled:
        extra = [t.strip() for t in settings.toxicity_deny_terms.split(",") if t.strip()]
        ctx.content_analyzers.append(ToxicityDetector(extra_terms=extra or None))
        logger.info("Content analyzer loaded: walacor.toxicity.v1 (extra_terms=%d)", len(extra))


async def _init_redis(settings) -> "Any | None":
    """Phase 15: Redis client for shared state (multi-replica). Returns None when redis_url is empty."""
    if not settings.redis_url:
        return None
    try:
        import redis.asyncio as aioredis
    except ImportError:
        raise RuntimeError(
            "WALACOR_REDIS_URL is set but 'redis' package is not installed. "
            "Install with: pip install 'walacor-gateway[redis]'"
        )
    client = aioredis.from_url(settings.redis_url, decode_responses=False)
    await client.ping()  # fail fast at startup if unreachable
    logger.info("Redis connected: %s", settings.redis_url)
    return client


def _init_budget_tracker(settings, ctx) -> None:
    """Phase 11: token budget tracker (in-memory or Redis-backed)."""
    from gateway.pipeline.budget_tracker import make_budget_tracker

    ctx.budget_tracker = make_budget_tracker(ctx.redis_client, settings)
    if settings.token_budget_enabled and settings.token_budget_max_tokens > 0:
        if ctx.redis_client is None:
            # In-memory tracker supports synchronous configure
            ctx.budget_tracker.configure(
                settings.gateway_tenant_id, None,
                settings.token_budget_period, settings.token_budget_max_tokens,
            )
        logger.info(
            "Token budget enabled: period=%s max_tokens=%d",
            settings.token_budget_period, settings.token_budget_max_tokens,
        )


def _init_session_chain(settings, ctx) -> None:
    """Phase 13: Merkle session chain tracker (in-memory or Redis-backed)."""
    if not settings.session_chain_enabled:
        return
    from gateway.pipeline.session_chain import make_session_chain_tracker

    ctx.session_chain = make_session_chain_tracker(ctx.redis_client, settings)
    logger.info(
        "Session chain tracker enabled: max_sessions=%s ttl=%ds",
        getattr(ctx.session_chain, '_max', 'redis'),
        settings.session_chain_ttl,
    )


async def _init_tool_registry(settings, ctx) -> None:
    """Phase 14: MCP tool registry for the active tool strategy."""
    from gateway.mcp.registry import ToolRegistry, parse_mcp_server_configs

    configs = parse_mcp_server_configs(settings.mcp_servers_json)
    if not configs:
        logger.info("tool_aware_enabled=True but no MCP server configs found — tool registry not started")
        return
    ctx.tool_registry = ToolRegistry(configs)
    await ctx.tool_registry.startup()
    logger.info(
        "Tool registry ready: %d server(s), %d tool(s) — %s",
        len(ctx.tool_registry.server_names()),
        ctx.tool_registry.get_tool_count(),
        ctx.tool_registry.server_names(),
    )


def _next_backoff(current: float, cap: float) -> float:
    """Exponential backoff: 5 s initial, doubles each step, capped at cap."""
    step = current * 2 + 5.0 if current else 5.0
    return min(step, cap)


async def _sync_once(ctx, provider: str, current_backoff: float, backoff_max: float) -> float:
    """Run one sync cycle and return the updated backoff value."""
    try:
        a_ok = await ctx.sync_client.sync_attestations(provider=provider)
        p_ok = await ctx.sync_client.sync_policies()
        return 0.0 if (a_ok and p_ok) else _next_backoff(current_backoff, backoff_max)
    except asyncio.CancelledError:
        raise
    except Exception as e:
        logger.warning("Sync loop error: %s", e, exc_info=True)
        return _next_backoff(current_backoff, backoff_max)


async def _run_sync_loop(settings, ctx) -> None:
    """Periodic pull-sync with exponential backoff on failure."""
    backoff = 0.0
    backoff_max = 60.0
    while True:
        await asyncio.sleep(settings.sync_interval + backoff)
        if ctx.sync_client:
            backoff = await _sync_once(ctx, settings.gateway_provider, backoff, backoff_max)


async def on_startup() -> None:
    settings = get_settings()
    ctx = get_pipeline_context()

    # Walacor storage is mode-independent: init before the skip_governance shortcut
    # so completeness attempts are always written when credentials are configured.
    if settings.walacor_storage_enabled:
        await _init_walacor(settings, ctx)

    # Shared HTTP client for all modes. Without this, skip_governance would create
    # a new one-off httpx.AsyncClient per request (Finding 7).
    ctx.http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(60.0, connect=10.0),
        limits=httpx.Limits(max_connections=200, max_keepalive_connections=50),
        http2=True,
    )

    if settings.skip_governance:
        ctx.skip_governance = True
        logger.info("Gateway running in skip_governance (transparent proxy) mode")
        return

    await _init_governance(settings, ctx)
    if not settings.walacor_storage_enabled:
        _init_wal(settings, ctx)
    ctx.redis_client = await _init_redis(settings)
    _init_content_analyzers(settings, ctx)
    _init_budget_tracker(settings, ctx)
    _init_session_chain(settings, ctx)
    if settings.tool_aware_enabled and settings.mcp_servers_json:
        await _init_tool_registry(settings, ctx)
    await _self_test()
    ctx.sync_loop_task = asyncio.create_task(_run_sync_loop(settings, ctx))
    logger.info("Gateway startup complete: attestation and policy caches synced, WAL and delivery worker started")


async def on_shutdown() -> None:
    """Graceful shutdown: each step runs independently so one failure doesn't skip the rest."""
    ctx = get_pipeline_context()
    errors: list[str] = []

    if ctx.http_client:
        try:
            await ctx.http_client.aclose()
        except Exception as e:
            errors.append(f"http_client.aclose: {e}")
        ctx.http_client = None

    if ctx.sync_loop_task and not ctx.sync_loop_task.done():
        ctx.sync_loop_task.cancel()
        try:
            await ctx.sync_loop_task
        except asyncio.CancelledError:
            pass
        except Exception as e:
            errors.append(f"sync_loop_task: {e}")

    if ctx.delivery_worker:
        try:
            ctx.delivery_worker.stop()
        except Exception as e:
            errors.append(f"delivery_worker.stop: {e}")

    if ctx.sync_client:
        try:
            await ctx.sync_client.close()
        except Exception as e:
            errors.append(f"sync_client.close: {e}")

    if ctx.wal_writer:
        try:
            ctx.wal_writer.close()
        except Exception as e:
            errors.append(f"wal_writer.close: {e}")

    if ctx.walacor_client:
        try:
            await ctx.walacor_client.close()
        except Exception as e:
            errors.append(f"walacor_client.close: {e}")
        ctx.walacor_client = None

    if ctx.tool_registry:
        try:
            await ctx.tool_registry.shutdown()
        except Exception as e:
            errors.append(f"tool_registry.shutdown: {e}")
        ctx.tool_registry = None

    if ctx.redis_client:
        try:
            await ctx.redis_client.aclose()
        except Exception as e:
            errors.append(f"redis_client.aclose: {e}")
        ctx.redis_client = None

    if errors:
        logger.warning("Gateway shutdown completed with errors: %s", "; ".join(errors))
    else:
        logger.info("Gateway shutdown complete")


def create_app() -> Starlette:
    routes = [
        Route("/health", health_response, methods=["GET"]),
        Route("/metrics", metrics_response, methods=["GET"]),
        Route("/v1/chat/completions", catch_all_post, methods=["POST"]),
        Route("/v1/chat/completions/", catch_all_post, methods=["POST"]),
        Route("/v1/completions", catch_all_post, methods=["POST"]),
        Route("/v1/completions/", catch_all_post, methods=["POST"]),
        Route("/v1/messages", catch_all_post, methods=["POST"]),
        Route("/v1/messages/", catch_all_post, methods=["POST"]),
        Route("/v1/custom", catch_all_post, methods=["POST"]),
        Route("/v1/custom/", catch_all_post, methods=["POST"]),
        Route("/generate", catch_all_post, methods=["POST"]),
    ]
    app = Starlette(debug=False, routes=routes)
    # Middleware order: last registered = outermost (first to run).
    # CORS first so OPTIONS preflight succeeds for browser clients.
    app.middleware("http")(cors_middleware)
    # api_key runs inside completeness so denied_auth attempts are always recorded.
    app.middleware("http")(api_key_middleware)
    app.middleware("http")(completeness_middleware)
    app.add_event_handler("startup", on_startup)
    app.add_event_handler("shutdown", on_shutdown)
    return app


app = create_app()


def main() -> None:
    import uvicorn
    settings = get_settings()

    try:
        import uvloop  # noqa: F401
        loop = "uvloop"
    except ImportError:
        loop = "auto"

    uvicorn.run(
        "gateway.main:app",
        host=settings.gateway_host,
        port=settings.gateway_port,
        log_level=settings.log_level.lower(),
        loop=loop,
        workers=settings.uvicorn_workers,
    )


if __name__ == "__main__":
    main()
