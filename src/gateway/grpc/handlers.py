"""GovernanceServicer — implements all 10 gRPC RPCs by wrapping existing pipeline logic.

Each handler reads from the PipelineContext singleton and delegates to the
corresponding Python module (policy evaluator, content analyzers, session chain,
budget tracker, semantic cache, tool registry, etc.).

The generated pb2/pb2_grpc modules are imported lazily so the handler code
can be tested without running protoc first.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


def _import_pb2():
    """Lazy import of generated protobuf modules.

    Returns (governance_pb2, governance_pb2_grpc) or raises ImportError with
    a helpful message when stubs have not been generated yet.
    """
    try:
        from gateway.grpc import governance_pb2, governance_pb2_grpc
        return governance_pb2, governance_pb2_grpc
    except ImportError as exc:
        raise ImportError(
            "gRPC stubs not generated. Run: cd proto && make python  "
            f"(original error: {exc})"
        ) from exc


class GovernanceServicer:
    """Implements the GovernanceEngine gRPC service.

    Accepts PipelineContext and Settings at construction time so all handlers
    can access shared caches, clients, and configuration without global lookups.
    """

    def __init__(self, ctx: Any, settings: Any) -> None:
        self._ctx = ctx
        self._settings = settings
        self._start_time = time.time()

    # ── EvaluatePreInference ──────────────────────────────────────────────

    async def EvaluatePreInference(self, request, context):
        """Pre-inference: attestation, policy, budget, PII sanitization, A/B test."""
        pb2, _ = _import_pb2()
        logger.debug("EvaluatePreInference: model=%s provider=%s", request.model_id, request.provider)

        result = pb2.PreInferenceResult(
            allowed=True,
            model_id=request.model_id,
            policy_result="pass",
            tool_strategy="none",
        )

        ctx = self._ctx
        settings = self._settings

        # 1. Attestation check
        attestation_id = ""
        att_ctx: dict[str, Any] = {}
        if ctx.attestation_cache is not None:
            from datetime import datetime, timezone
            from gateway.cache.attestation_cache import CachedAttestation

            entry = ctx.attestation_cache.get(request.provider, request.model_id)

            # Auto-attest if no entry and no control plane / control store
            if entry is None and ctx.sync_client is None and ctx.control_store is None:
                entry = CachedAttestation(
                    attestation_id=f"self-attested:{request.model_id}",
                    model_id=request.model_id,
                    provider=request.provider,
                    status="active",
                    fetched_at=datetime.now(timezone.utc),
                    ttl_seconds=settings.attestation_cache_ttl
                    if hasattr(settings, "attestation_cache_ttl") else 300,
                    verification_level="self_attested",
                )
                ctx.attestation_cache.set(entry)

            if entry is None or (hasattr(entry, "is_blocked") and entry.is_blocked):
                result.allowed = False
                result.denial_reason = "Model not attested or attestation revoked"
                result.denial_status_code = 403
                result.policy_result = "denied_attestation"
                return result

            attestation_id = entry.attestation_id
            att_ctx = {
                "model_id": request.model_id,
                "provider": request.provider,
                "status": getattr(entry, "status", "active"),
                "verification_level": getattr(entry, "verification_level", ""),
                "tenant_id": request.tenant_id or settings.gateway_tenant_id,
            }

        result.attestation_id = attestation_id

        # 2. Policy evaluation
        if ctx.policy_cache is not None:
            from gateway.adapters.base import ModelCall
            from gateway.pipeline.policy_evaluator import evaluate_pre_inference

            call = ModelCall(
                provider=request.provider,
                model_id=request.model_id,
                prompt_text=request.prompt_text,
                raw_body=b"{}",
                is_streaming=False,
                metadata=dict(request.metadata),
            )
            blocked, version, pol_result, _err_resp = evaluate_pre_inference(
                ctx.policy_cache, call, attestation_id, att_ctx,
            )
            result.policy_version = version
            result.policy_result = pol_result
            if blocked:
                result.allowed = False
                result.denial_reason = f"Policy blocked: {pol_result}"
                result.denial_status_code = 403
                return result

        # 3. Budget check
        if ctx.budget_tracker is not None and settings.token_budget_enabled:
            try:
                tenant = request.tenant_id or settings.gateway_tenant_id
                user = request.user_id or None
                allowed, remaining = await ctx.budget_tracker.check_and_reserve(
                    tenant, user, estimated_tokens=500,
                )
                if not allowed:
                    result.allowed = False
                    result.denial_reason = "Token budget exceeded"
                    result.denial_status_code = 429
                    result.policy_result = "budget_exceeded"
                    return result
                result.budget_remaining = remaining
            except Exception as exc:
                logger.warning("Budget check failed (fail-open): %s", exc)

        # 4. PII sanitization
        if settings.pii_sanitization_enabled and request.prompt_text:
            try:
                from gateway.content.pii_sanitizer import get_default_sanitizer

                sanitizer = get_default_sanitizer()
                san_result = sanitizer.sanitize(request.prompt_text)
                if san_result.pii_count > 0:
                    result.sanitized_prompt = san_result.sanitized_text
                    result.pii_mapping = json.dumps(san_result.mapping)
            except Exception as exc:
                logger.warning("PII sanitization failed (pass-through): %s", exc)

        # 5. A/B test resolution
        if request.ab_tests_json or settings.ab_tests_json:
            try:
                from gateway.routing.ab_test import load_ab_tests, resolve_ab_model

                ab_json = request.ab_tests_json or settings.ab_tests_json
                ab_tests = load_ab_tests(ab_json)
                if ab_tests:
                    resolved_model, _test_name = resolve_ab_model(request.model_id, ab_tests)
                    result.model_id = resolved_model
            except Exception as exc:
                logger.warning("A/B test resolution failed: %s", exc)

        # 6. Tool strategy / definitions
        if ctx.tool_registry is not None and settings.tool_aware_enabled:
            result.tool_strategy = settings.tool_strategy or "auto"
            try:
                tool_defs = ctx.tool_registry.get_tool_definitions()
                for td in tool_defs:
                    func = td.get("function", {})
                    result.resolved_tools.append(pb2.ToolDefinition(
                        name=func.get("name", ""),
                        description=func.get("description", ""),
                        input_schema_json=json.dumps(func.get("parameters", {})),
                    ))
            except Exception as exc:
                logger.warning("Tool definition gathering failed: %s", exc)

        return result

    # ── EvaluatePostInference ─────────────────────────────────────────────

    async def EvaluatePostInference(self, request, context):
        """Post-inference: content analysis, policy evaluation, PII restore."""
        pb2, _ = _import_pb2()
        logger.debug("EvaluatePostInference: model=%s", request.model_id)

        result = pb2.PostInferenceResult(
            policy_result="pass",
            blocked=False,
        )

        ctx = self._ctx

        # 1. Content analysis
        text_to_analyze = request.content or request.thinking_content
        analyzer_decisions: list[dict] = []

        if ctx.content_analyzers and text_to_analyze:
            try:
                from gateway.pipeline.response_evaluator import analyze_text

                analyzer_decisions = await analyze_text(text_to_analyze, ctx.content_analyzers)
            except Exception as exc:
                logger.warning("Content analysis failed (pass-through): %s", exc)

        # 2. Post-inference policy evaluation
        if ctx.policy_cache is not None and ctx.content_analyzers and text_to_analyze:
            try:
                from gateway.adapters.base import ModelResponse
                from gateway.pipeline.response_evaluator import evaluate_post_inference

                mr = ModelResponse(
                    content=request.content,
                    usage=None,
                    raw_body=b"",
                    provider_request_id="",
                    model_hash="",
                    thinking_content=request.thinking_content,
                )
                blocked, _version, pol_result, decisions_raw, _err = await evaluate_post_inference(
                    ctx.policy_cache, mr, ctx.content_analyzers,
                )
                result.policy_result = pol_result
                result.blocked = blocked
                if blocked and _err is not None:
                    result.block_reason = pol_result

                for d in decisions_raw:
                    result.decisions.append(pb2.PolicyDecision(
                        policy_name=d.get("analyzer_id", ""),
                        result=d.get("verdict", "pass"),
                        details=d.get("reason", ""),
                    ))
            except Exception as exc:
                logger.warning("Post-inference evaluation failed (pass-through): %s", exc)
        elif analyzer_decisions:
            # No policy cache but we have raw analyzer decisions
            for d in analyzer_decisions:
                result.decisions.append(pb2.PolicyDecision(
                    policy_name=d.get("analyzer_id", ""),
                    result=d.get("verdict", "pass"),
                    details=d.get("reason", ""),
                ))
                if d.get("verdict") == "block":
                    result.blocked = True
                    result.block_reason = d.get("reason", "Content blocked")
                    result.policy_result = "blocked"

        # 3. PII restoration
        if request.pii_mapping and request.content:
            try:
                from gateway.content.pii_sanitizer import get_default_sanitizer

                mapping = json.loads(request.pii_mapping)
                sanitizer = get_default_sanitizer()
                result.restored_content = sanitizer.restore(request.content, mapping)
            except Exception as exc:
                logger.warning("PII restoration failed: %s", exc)

        return result

    # ── RecordExecution ───────────────────────────────────────────────────

    async def RecordExecution(self, request, context):
        """Persist execution record to WAL + Walacor backend (dual-write)."""
        pb2, _ = _import_pb2()
        logger.debug("RecordExecution: execution_id=%s", request.execution_id)

        record = {
            "execution_id": request.execution_id,
            "model_attestation_id": request.attestation_id,
            "model_id": request.model_id,
            "provider": request.provider,
            "policy_version": request.policy_version,
            "policy_result": request.policy_result,
            "tenant_id": request.tenant_id,
            "gateway_id": request.gateway_id,
            "timestamp": request.timestamp,
            "user": request.user or None,
            "session_id": request.session_id or None,
            "metadata": dict(request.metadata) if request.metadata else None,
            "prompt_text": request.prompt_text or None,
            "response_content": request.response_content or None,
            "thinking_content": request.thinking_content or None,
            "provider_request_id": request.provider_request_id or None,
            "model_hash": request.model_hash or None,
            "latency_ms": request.latency_ms if request.latency_ms else None,
            "prompt_tokens": request.prompt_tokens,
            "completion_tokens": request.completion_tokens,
            "total_tokens": request.total_tokens,
            "sequence_number": request.sequence_number,
            "previous_record_hash": request.previous_record_hash or None,
            "record_hash": request.record_hash or None,
            "retry_of": request.retry_of or None,
            "variant_id": request.variant_id or None,
            "cache_hit": request.cache_hit,
            "cached_tokens": request.cached_tokens,
            "cache_creation_tokens": request.cache_creation_tokens,
        }

        ctx = self._ctx
        success = True
        error_message = ""

        # Write to WAL
        if ctx.wal_writer:
            try:
                ctx.wal_writer.write_and_fsync(record)
            except Exception as exc:
                logger.error("WAL write failed: %s", exc)
                success = False
                error_message = f"WAL write failed: {exc}"

        # Write to Walacor backend
        if ctx.walacor_client:
            try:
                await ctx.walacor_client.write_execution(record)
            except Exception as exc:
                logger.error("Walacor backend write failed: %s", exc)
                if not error_message:
                    error_message = f"Walacor write failed: {exc}"
                # success stays True if WAL succeeded (dual-write pattern)

        # Export to audit exporter
        if ctx.audit_exporter:
            try:
                await ctx.audit_exporter.export(record)
            except Exception as exc:
                logger.warning("Audit export failed: %s", exc)

        return pb2.WriteResult(
            success=success,
            execution_id=request.execution_id,
            error_message=error_message,
        )

    # ── NextChainValues ───────────────────────────────────────────────────

    async def NextChainValues(self, request, context):
        """Return next (sequence_number, previous_record_hash) for a session chain."""
        pb2, _ = _import_pb2()
        logger.debug("NextChainValues: session_id=%s", request.session_id)

        ctx = self._ctx
        if ctx.session_chain is None:
            return pb2.ChainValues(sequence_number=0, previous_record_hash="0" * 128)

        try:
            seq, prev_hash = await ctx.session_chain.next_chain_values(request.session_id)
            return pb2.ChainValues(sequence_number=seq, previous_record_hash=prev_hash)
        except Exception as exc:
            logger.error("next_chain_values failed: %s", exc)
            import grpc as grpc_lib
            context.set_code(grpc_lib.StatusCode.INTERNAL)
            context.set_details(f"Session chain error: {exc}")
            return pb2.ChainValues()

    # ── UpdateChain ───────────────────────────────────────────────────────

    async def UpdateChain(self, request, context):
        """Commit a new link in the session chain after writing execution record."""
        pb2, _ = _import_pb2()
        logger.debug("UpdateChain: session_id=%s seq=%d", request.session_id, request.sequence_number)

        from gateway.pipeline.session_chain import compute_record_hash

        record_hash = compute_record_hash(
            execution_id=request.execution_id,
            policy_version=request.policy_version,
            policy_result=request.policy_result,
            previous_record_hash=request.previous_record_hash,
            sequence_number=request.sequence_number,
            timestamp=request.timestamp,
        )

        ctx = self._ctx
        if ctx.session_chain is not None:
            try:
                await ctx.session_chain.update(
                    request.session_id, request.sequence_number, record_hash,
                )
            except Exception as exc:
                logger.error("Session chain update failed: %s", exc)
                import grpc as grpc_lib
                context.set_code(grpc_lib.StatusCode.INTERNAL)
                context.set_details(f"Chain update failed: {exc}")
                return pb2.ChainResult()

        return pb2.ChainResult(record_hash=record_hash, sequence_number=request.sequence_number)

    # ── AnalyzeContent ────────────────────────────────────────────────────

    async def AnalyzeContent(self, request, context):
        """Run all configured content analyzers on arbitrary text."""
        pb2, _ = _import_pb2()
        logger.debug("AnalyzeContent: type=%s len=%d", request.analysis_type, len(request.text))

        result = pb2.AnalysisResult()
        ctx = self._ctx

        if not ctx.content_analyzers or not request.text:
            return result

        try:
            from gateway.pipeline.response_evaluator import analyze_text

            decisions = await analyze_text(request.text, ctx.content_analyzers)

            for d in decisions:
                verdict_obj = pb2.ContentVerdict(
                    analyzer_id=d.get("analyzer_id", ""),
                    verdict=d.get("verdict", "pass"),
                    confidence=d.get("confidence", 0.0),
                    category=d.get("category", ""),
                    reason=d.get("reason", ""),
                )
                result.verdicts.append(verdict_obj)

                # Aggregate flags
                category = d.get("category", "")
                if category == "pii" and d.get("verdict") != "pass":
                    result.pii_detected = True
                if category == "toxicity" and d.get("verdict") != "pass":
                    result.toxicity_flagged = True
                if "llama_guard" in d.get("analyzer_id", ""):
                    result.llama_guard_result = d.get("reason", "")

        except Exception as exc:
            logger.warning("Content analysis failed: %s", exc)

        return result

    # ── ExecuteTool ───────────────────────────────────────────────────────

    async def ExecuteTool(self, request, context):
        """Execute a tool via the tool registry."""
        pb2, _ = _import_pb2()
        logger.debug("ExecuteTool: name=%s timeout=%dms", request.name, request.timeout_ms)

        ctx = self._ctx
        if ctx.tool_registry is None:
            import grpc as grpc_lib
            context.set_code(grpc_lib.StatusCode.UNAVAILABLE)
            context.set_details("Tool registry not initialized")
            return pb2.ToolResponse(content="", is_error=True)

        start = time.monotonic()
        try:
            arguments = json.loads(request.arguments_json) if request.arguments_json else {}
            tool_result = await ctx.tool_registry.execute_tool(
                request.name,
                arguments,
                timeout_ms=request.timeout_ms or 30000,
            )
            duration = (time.monotonic() - start) * 1000

            response = pb2.ToolResponse(
                content=str(tool_result.content) if tool_result.content else "",
                is_error=tool_result.is_error,
                duration_ms=duration,
            )

            if tool_result.sources:
                for src in tool_result.sources:
                    response.sources.append(pb2.ToolSource(
                        title=src.get("title", ""),
                        url=src.get("url", ""),
                        snippet=src.get("snippet", ""),
                    ))

            return response
        except Exception as exc:
            duration = (time.monotonic() - start) * 1000
            logger.error("Tool execution failed: %s", exc)
            return pb2.ToolResponse(
                content=f"Tool execution error: {exc}",
                is_error=True,
                duration_ms=duration,
            )

    # ── CacheGet ──────────────────────────────────────────────────────────

    async def CacheGet(self, request, context):
        """Look up a cached response by prompt + model."""
        pb2, _ = _import_pb2()
        logger.debug("CacheGet: model=%s", request.model_id)

        ctx = self._ctx
        if ctx.semantic_cache is None:
            return pb2.CacheResponse(hit=False)

        try:
            entry = ctx.semantic_cache.get(request.model_id, request.prompt_text)
            if entry is None:
                return pb2.CacheResponse(hit=False)
            return pb2.CacheResponse(
                hit=True,
                response_body=entry.response_body.decode("utf-8", errors="replace")
                if isinstance(entry.response_body, bytes) else str(entry.response_body),
                cached_at=str(entry.created_at),
            )
        except Exception as exc:
            logger.warning("Cache get failed: %s", exc)
            return pb2.CacheResponse(hit=False)

    # ── CachePut ──────────────────────────────────────────────────────────

    async def CachePut(self, request, context):
        """Store a response in the semantic cache."""
        pb2, _ = _import_pb2()
        logger.debug("CachePut: model=%s", request.model_id)

        ctx = self._ctx
        if ctx.semantic_cache is None:
            return pb2.CacheResult(success=False)

        try:
            body = request.response_body.encode("utf-8") if isinstance(request.response_body, str) else request.response_body
            ctx.semantic_cache.put(request.model_id, request.prompt_text, body)
            return pb2.CacheResult(success=True)
        except Exception as exc:
            logger.warning("Cache put failed: %s", exc)
            return pb2.CacheResult(success=False)

    # ── HealthCheck ───────────────────────────────────────────────────────

    async def HealthCheck(self, request, context):
        """Return sidecar health status."""
        pb2, _ = _import_pb2()
        logger.debug("HealthCheck")

        ctx = self._ctx
        settings = self._settings

        uptime = time.time() - self._start_time
        active_sessions = 0
        if ctx.session_chain is not None:
            count = ctx.session_chain.active_session_count()
            active_sessions = count if count >= 0 else 0

        # Model capabilities
        model_caps: dict[str, str] = {}
        if ctx.capability_registry:
            try:
                caps = ctx.capability_registry.all_capabilities()
                model_caps = {k: json.dumps(v) for k, v in caps.items()}
            except Exception:
                pass
        else:
            try:
                from gateway.pipeline.orchestrator import _model_capabilities
                model_caps = {k: json.dumps(v) for k, v in _model_capabilities.items()}
            except Exception:
                pass

        from gateway import __version__

        return pb2.HealthStatus(
            healthy=True,
            version=__version__,
            model_capabilities=model_caps,
            active_sessions=active_sessions,
            uptime_seconds=uptime,
            content_analyzer_count=len(ctx.content_analyzers),
            governance_enabled=not ctx.skip_governance,
        )
