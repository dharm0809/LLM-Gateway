"""Embedded control plane CRUD API route handlers."""

from __future__ import annotations

import logging
import time

from starlette.requests import Request
from starlette.responses import JSONResponse

from gateway.config import get_settings
from gateway.pipeline.context import get_pipeline_context

logger = logging.getLogger(__name__)

_start_time = time.time()


def _store_or_503():
    ctx = get_pipeline_context()
    if ctx.control_store is None:
        return None
    return ctx.control_store


def _tenant(request: Request) -> str:
    settings = get_settings()
    return request.query_params.get("tenant_id", settings.gateway_tenant_id or "")


# ── Cache refresh helpers ─────────────────────────────────────

def _refresh_attestation_cache() -> None:
    """Repopulate attestation cache from DB after mutation."""
    ctx = get_pipeline_context()
    settings = get_settings()
    store = ctx.control_store
    if store is None or ctx.attestation_cache is None:
        return
    tenant_id = settings.gateway_tenant_id
    ctx.attestation_cache.clear()
    proofs = store.get_attestation_proofs(tenant_id)
    for p in proofs:
        ctx.attestation_cache.set_from_proof(p.get("provider", "ollama"), p)
    logger.info("Attestation cache refreshed: %d entries", len(proofs))


def _refresh_policy_cache() -> None:
    """Repopulate policy cache from DB after mutation."""
    ctx = get_pipeline_context()
    settings = get_settings()
    store = ctx.control_store
    if store is None or ctx.policy_cache is None:
        return
    tenant_id = settings.gateway_tenant_id
    policies = store.get_active_policies(tenant_id)
    version = ctx.policy_cache.next_version()
    ctx.policy_cache.set_policies(version, policies)
    logger.info("Policy cache refreshed: %d active policies (version %d)", len(policies), version)


def _refresh_budget_tracker() -> None:
    """Sync budget tracker with DB after mutation."""
    ctx = get_pipeline_context()
    store = ctx.control_store
    if store is None or ctx.budget_tracker is None:
        return
    budgets = store.list_budgets()
    # Track which keys are in DB
    db_keys: set[tuple[str, str]] = set()
    for b in budgets:
        tid = b["tenant_id"]
        user = b.get("user", "")
        ctx.budget_tracker.configure(tid, user or None, b["period"], b["max_tokens"])
        db_keys.add((tid, user))
    # Remove budgets no longer in DB
    if hasattr(ctx.budget_tracker, "remove"):
        existing_keys = set(ctx.budget_tracker._states.keys()) if hasattr(ctx.budget_tracker, "_states") else set()
        for key in existing_keys - db_keys:
            ctx.budget_tracker.remove(key[0], key[1] or None)
    logger.info("Budget tracker refreshed: %d budgets", len(budgets))


# ── Attestation endpoints ─────────────────────────────────────

async def control_list_attestations(request: Request) -> JSONResponse:
    store = _store_or_503()
    if store is None:
        return JSONResponse({"error": "Control plane not available"}, status_code=503)
    tenant_id = _tenant(request)
    try:
        rows = store.list_attestations(tenant_id)
        return JSONResponse({"attestations": rows, "count": len(rows)})
    except Exception as e:
        logger.error("control_list_attestations error: %s", e, exc_info=True)
        return JSONResponse({"error": str(e)}, status_code=500)


async def control_upsert_attestation(request: Request) -> JSONResponse:
    store = _store_or_503()
    if store is None:
        return JSONResponse({"error": "Control plane not available"}, status_code=503)
    try:
        body = await request.json()
        settings = get_settings()
        if "tenant_id" not in body:
            body["tenant_id"] = settings.gateway_tenant_id or ""
        result = store.upsert_attestation(body)
        _refresh_attestation_cache()
        return JSONResponse(result, status_code=200)
    except Exception as e:
        logger.error("control_upsert_attestation error: %s", e, exc_info=True)
        return JSONResponse({"error": str(e)}, status_code=400)


async def control_delete_attestation(request: Request) -> JSONResponse:
    store = _store_or_503()
    if store is None:
        return JSONResponse({"error": "Control plane not available"}, status_code=503)
    attestation_id = request.path_params["id"]
    try:
        deleted = store.delete_attestation(attestation_id)
        if deleted:
            _refresh_attestation_cache()
        return JSONResponse({"deleted": deleted})
    except Exception as e:
        logger.error("control_delete_attestation error: %s", e, exc_info=True)
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Policy endpoints ──────────────────────────────────────────

async def control_list_policies(request: Request) -> JSONResponse:
    store = _store_or_503()
    if store is None:
        return JSONResponse({"error": "Control plane not available"}, status_code=503)
    tenant_id = _tenant(request)
    try:
        rows = store.list_policies(tenant_id)
        return JSONResponse({"policies": rows, "count": len(rows)})
    except Exception as e:
        logger.error("control_list_policies error: %s", e, exc_info=True)
        return JSONResponse({"error": str(e)}, status_code=500)


async def control_create_policy(request: Request) -> JSONResponse:
    store = _store_or_503()
    if store is None:
        return JSONResponse({"error": "Control plane not available"}, status_code=503)
    try:
        body = await request.json()
        settings = get_settings()
        if "tenant_id" not in body:
            body["tenant_id"] = settings.gateway_tenant_id or ""
        result = store.create_policy(body)
        _refresh_policy_cache()
        return JSONResponse(result, status_code=201)
    except Exception as e:
        logger.error("control_create_policy error: %s", e, exc_info=True)
        return JSONResponse({"error": str(e)}, status_code=400)


async def control_update_policy(request: Request) -> JSONResponse:
    store = _store_or_503()
    if store is None:
        return JSONResponse({"error": "Control plane not available"}, status_code=503)
    policy_id = request.path_params["id"]
    try:
        body = await request.json()
        updated = store.update_policy(policy_id, body)
        if updated:
            _refresh_policy_cache()
        return JSONResponse({"updated": updated})
    except Exception as e:
        logger.error("control_update_policy error: %s", e, exc_info=True)
        return JSONResponse({"error": str(e)}, status_code=400)


async def control_delete_policy(request: Request) -> JSONResponse:
    store = _store_or_503()
    if store is None:
        return JSONResponse({"error": "Control plane not available"}, status_code=503)
    policy_id = request.path_params["id"]
    try:
        deleted = store.delete_policy(policy_id)
        if deleted:
            _refresh_policy_cache()
        return JSONResponse({"deleted": deleted})
    except Exception as e:
        logger.error("control_delete_policy error: %s", e, exc_info=True)
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Budget endpoints ──────────────────────────────────────────

async def control_list_budgets(request: Request) -> JSONResponse:
    store = _store_or_503()
    if store is None:
        return JSONResponse({"error": "Control plane not available"}, status_code=503)
    tenant_id = _tenant(request)
    try:
        rows = store.list_budgets(tenant_id)
        return JSONResponse({"budgets": rows, "count": len(rows)})
    except Exception as e:
        logger.error("control_list_budgets error: %s", e, exc_info=True)
        return JSONResponse({"error": str(e)}, status_code=500)


async def control_upsert_budget(request: Request) -> JSONResponse:
    store = _store_or_503()
    if store is None:
        return JSONResponse({"error": "Control plane not available"}, status_code=503)
    try:
        body = await request.json()
        settings = get_settings()
        if "tenant_id" not in body:
            body["tenant_id"] = settings.gateway_tenant_id or ""
        result = store.upsert_budget(body)
        _refresh_budget_tracker()
        return JSONResponse(result, status_code=200)
    except Exception as e:
        logger.error("control_upsert_budget error: %s", e, exc_info=True)
        return JSONResponse({"error": str(e)}, status_code=400)


async def control_delete_budget(request: Request) -> JSONResponse:
    store = _store_or_503()
    if store is None:
        return JSONResponse({"error": "Control plane not available"}, status_code=503)
    budget_id = request.path_params["id"]
    try:
        deleted = store.delete_budget(budget_id)
        if deleted:
            _refresh_budget_tracker()
        return JSONResponse({"deleted": deleted})
    except Exception as e:
        logger.error("control_delete_budget error: %s", e, exc_info=True)
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Status endpoint ───────────────────────────────────────────

async def control_status(request: Request) -> JSONResponse:
    """Comprehensive gateway status for the control dashboard."""
    store = _store_or_503()
    if store is None:
        return JSONResponse({"error": "Control plane not available"}, status_code=503)
    settings = get_settings()
    ctx = get_pipeline_context()

    status: dict = {
        "gateway_id": settings.gateway_id,
        "tenant_id": settings.gateway_tenant_id,
        "enforcement_mode": settings.enforcement_mode,
        "skip_governance": settings.skip_governance,
        "uptime_seconds": int(time.time() - _start_time),
        "control_plane_enabled": True,
    }

    if ctx.attestation_cache:
        status["attestation_cache"] = {
            "entries": ctx.attestation_cache.entry_count,
        }
    if ctx.policy_cache:
        status["policy_cache"] = {
            "version": ctx.policy_cache.version,
            "stale": ctx.policy_cache.is_stale,
            "last_sync": ctx.policy_cache.last_sync.isoformat() if ctx.policy_cache.last_sync else None,
        }
    if ctx.wal_writer:
        status["wal"] = {
            "pending_records": ctx.wal_writer.pending_count(),
            "disk_usage_bytes": ctx.wal_writer.disk_usage_bytes(),
        }
    if ctx.sync_client:
        status["sync_mode"] = "remote"
        status["control_plane_url"] = settings.control_plane_url
    else:
        status["sync_mode"] = "local"

    # Model capabilities
    try:
        from gateway.pipeline.orchestrator import _model_capabilities
        if _model_capabilities:
            status["model_capabilities"] = dict(_model_capabilities)
    except Exception:
        pass

    return JSONResponse(status)
