"""gRPC server startup and lifecycle for the governance intelligence sidecar.

Starts an async gRPC server on a configurable port (default 50051).
Can run standalone (``python -m gateway.grpc``) or alongside the ASGI app
when ``WALACOR_GRPC_ENABLED=true``.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)


async def start_grpc_server(settings: Any, ctx: Any) -> Any:
    """Start the async gRPC server, returning the server instance.

    The caller is responsible for calling ``await server.stop(grace=5)``
    on shutdown.

    Parameters
    ----------
    settings : gateway.config.Settings
        Gateway configuration (reads grpc_port, grpc_max_workers).
    ctx : gateway.pipeline.context.PipelineContext
        Shared pipeline state (caches, clients, trackers).
    """
    try:
        import grpc
        from grpc import aio as grpc_aio
    except ImportError as exc:
        raise ImportError(
            "grpcio is required for the gRPC sidecar. Install with: "
            "pip install 'walacor-gateway[grpc]'  "
            f"(original error: {exc})"
        ) from exc

    from gateway.grpc.handlers import GovernanceServicer

    try:
        from gateway.grpc import governance_pb2_grpc
    except ImportError as exc:
        raise ImportError(
            "gRPC stubs not generated. Run: cd proto && make python  "
            f"(original error: {exc})"
        ) from exc

    server = grpc_aio.server()
    servicer = GovernanceServicer(ctx, settings)
    governance_pb2_grpc.add_GovernanceEngineServicer_to_server(servicer, server)

    port = getattr(settings, "grpc_port", 50051)
    listen_addr = f"[::]:{port}"
    server.add_insecure_port(listen_addr)

    await server.start()
    logger.info("gRPC governance sidecar started on %s", listen_addr)

    return server


async def run_standalone() -> None:
    """Run the gRPC server as a standalone process.

    Initializes the PipelineContext minimally (same as on_startup but
    without the ASGI app) and keeps the server alive until interrupted.
    """
    import signal

    from gateway.config import get_settings
    from gateway.pipeline.context import get_pipeline_context

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    settings = get_settings()
    ctx = get_pipeline_context()

    # Run a minimal startup sequence for governance features.
    # Full startup (on_startup) is designed for the ASGI lifecycle;
    # standalone mode only needs the governance caches and trackers.
    try:
        import httpx

        ctx.http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.provider_timeout, connect=settings.provider_connect_timeout),
            http2=True,
        )

        if not settings.skip_governance:
            # Minimal governance init
            from gateway.cache.attestation_cache import AttestationCache
            from gateway.cache.policy_cache import PolicyCache

            ctx.attestation_cache = AttestationCache(ttl_seconds=settings.attestation_cache_ttl)
            ctx.policy_cache = PolicyCache(staleness_threshold_seconds=settings.policy_staleness_threshold)

            # Seed empty pass-all policy if no control plane
            if not settings.control_plane_url:
                v = ctx.policy_cache.next_version()
                ctx.policy_cache.set_policies(v, [])

            # Content analyzers
            from gateway.content.pii_detector import PIIDetector
            if settings.pii_detection_enabled:
                ctx.content_analyzers.append(PIIDetector())

            # Session chain
            if settings.session_chain_enabled:
                from gateway.pipeline.session_chain import SessionChainTracker
                ctx.session_chain = SessionChainTracker(
                    max_sessions=settings.session_chain_max_sessions,
                    ttl_seconds=settings.session_chain_ttl,
                )

            # WAL
            if settings.lineage_enabled:
                from pathlib import Path
                from gateway.wal.writer import WALWriter

                wal_dir = Path(settings.wal_path)
                wal_dir.mkdir(parents=True, exist_ok=True)
                ctx.wal_writer = WALWriter(str(wal_dir / "wal.db"))
                ctx.wal_writer.start()

        server = await start_grpc_server(settings, ctx)

        # Wait for termination
        stop_event = asyncio.Event()

        def _signal_handler():
            stop_event.set()

        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _signal_handler)

        logger.info("gRPC sidecar running (press Ctrl+C to stop)")
        await stop_event.wait()

        logger.info("Shutting down gRPC server...")
        await server.stop(grace=5)

    finally:
        if ctx.http_client:
            await ctx.http_client.aclose()
        if ctx.wal_writer:
            ctx.wal_writer.close()
        logger.info("gRPC sidecar stopped")
