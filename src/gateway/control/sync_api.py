"""Sync-contract endpoints: serve SyncClient format so other gateways can pull from this one."""

from __future__ import annotations

import logging

from starlette.requests import Request
from starlette.responses import JSONResponse

from gateway.pipeline.context import get_pipeline_context

logger = logging.getLogger(__name__)


async def sync_attestation_proofs(request: Request) -> JSONResponse:
    """GET /v1/attestation-proofs — return proofs in SyncClient format."""
    ctx = get_pipeline_context()
    store = ctx.control_store
    if store is None:
        return JSONResponse({"error": "Control plane not available"}, status_code=503)
    tenant_id = request.query_params.get("tenant_id", "")
    try:
        proofs = store.get_attestation_proofs(tenant_id)
        return JSONResponse({"proofs": proofs})
    except Exception as e:
        logger.error("sync_attestation_proofs error: %s", e, exc_info=True)
        return JSONResponse({"error": str(e)}, status_code=500)


async def sync_policies(request: Request) -> JSONResponse:
    """GET /v1/policies — return active policies in SyncClient format."""
    ctx = get_pipeline_context()
    store = ctx.control_store
    if store is None:
        return JSONResponse({"error": "Control plane not available"}, status_code=503)
    tenant_id = request.query_params.get("tenant_id", "")
    try:
        policies = store.get_active_policies(tenant_id)
        return JSONResponse({"policies": policies})
    except Exception as e:
        logger.error("sync_policies error: %s", e, exc_info=True)
        return JSONResponse({"error": str(e)}, status_code=500)
