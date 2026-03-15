"""Tests for the gRPC GovernanceServicer handler methods.

Each handler is tested directly via its async method, using mock pb2 messages
and a mock PipelineContext. No actual gRPC server infrastructure is required.
"""

from __future__ import annotations

import json
import sys
import types
from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

anyio_backend = ["asyncio"]


# ---------------------------------------------------------------------------
# Fake protobuf messages — lightweight stand-ins for generated pb2 classes.
# Each message class is a simple namespace that accepts keyword args and
# supports attribute access and `.append()` on repeated fields.
# ---------------------------------------------------------------------------


class _RepeatedField(list):
    """Mimics a protobuf repeated field that supports .append()."""
    pass


class _FakeMessage:
    """Base class for fake protobuf messages."""

    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)

    def __getattr__(self, name):
        # Auto-create repeated fields (lists) and maps (dicts) on first access.
        # This mirrors how protobuf messages behave — accessing a repeated
        # field that has never been set returns an empty container.
        if name.startswith("_"):
            raise AttributeError(name)
        # For fields we know are repeated or map, auto-init
        val = _RepeatedField()
        object.__setattr__(self, name, val)
        return val


class FakePreInferenceRequest(_FakeMessage):
    pass


class FakePreInferenceResult(_FakeMessage):
    pass


class FakePostInferenceRequest(_FakeMessage):
    pass


class FakePostInferenceResult(_FakeMessage):
    pass


class FakeExecutionRecord(_FakeMessage):
    pass


class FakeWriteResult(_FakeMessage):
    pass


class FakeChainRequest(_FakeMessage):
    pass


class FakeChainValues(_FakeMessage):
    pass


class FakeChainUpdate(_FakeMessage):
    pass


class FakeChainResult(_FakeMessage):
    pass


class FakeContentPayload(_FakeMessage):
    pass


class FakeAnalysisResult(_FakeMessage):
    pass


class FakeContentVerdict(_FakeMessage):
    pass


class FakeToolRequest(_FakeMessage):
    pass


class FakeToolResponse(_FakeMessage):
    pass


class FakeToolSource(_FakeMessage):
    pass


class FakeToolDefinition(_FakeMessage):
    pass


class FakeCacheKey(_FakeMessage):
    pass


class FakeCacheResponse(_FakeMessage):
    pass


class FakeCachePutRequest(_FakeMessage):
    pass


class FakeCacheResult(_FakeMessage):
    pass


class FakeEmpty(_FakeMessage):
    pass


class FakeHealthStatus(_FakeMessage):
    pass


class FakePolicyDecision(_FakeMessage):
    pass


# Build a fake governance_pb2 module
_fake_pb2 = types.ModuleType("gateway.grpc.governance_pb2")
_fake_pb2.PreInferenceRequest = FakePreInferenceRequest
_fake_pb2.PreInferenceResult = FakePreInferenceResult
_fake_pb2.PostInferenceRequest = FakePostInferenceRequest
_fake_pb2.PostInferenceResult = FakePostInferenceResult
_fake_pb2.ExecutionRecord = FakeExecutionRecord
_fake_pb2.WriteResult = FakeWriteResult
_fake_pb2.ChainRequest = FakeChainRequest
_fake_pb2.ChainValues = FakeChainValues
_fake_pb2.ChainUpdate = FakeChainUpdate
_fake_pb2.ChainResult = FakeChainResult
_fake_pb2.ContentPayload = FakeContentPayload
_fake_pb2.AnalysisResult = FakeAnalysisResult
_fake_pb2.ContentVerdict = FakeContentVerdict
_fake_pb2.ToolRequest = FakeToolRequest
_fake_pb2.ToolResponse = FakeToolResponse
_fake_pb2.ToolSource = FakeToolSource
_fake_pb2.ToolDefinition = FakeToolDefinition
_fake_pb2.CacheKey = FakeCacheKey
_fake_pb2.CacheResponse = FakeCacheResponse
_fake_pb2.CachePutRequest = FakeCachePutRequest
_fake_pb2.CacheResult = FakeCacheResult
_fake_pb2.Empty = FakeEmpty
_fake_pb2.HealthStatus = FakeHealthStatus
_fake_pb2.PolicyDecision = FakePolicyDecision

_fake_pb2_grpc = types.ModuleType("gateway.grpc.governance_pb2_grpc")

# Install fake modules so `from gateway.grpc import governance_pb2` works
sys.modules["gateway.grpc.governance_pb2"] = _fake_pb2
sys.modules["gateway.grpc.governance_pb2_grpc"] = _fake_pb2_grpc


from gateway.grpc.handlers import GovernanceServicer  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_settings():
    """Minimal mock Settings for handler construction."""
    s = MagicMock()
    s.gateway_tenant_id = "test-tenant"
    s.gateway_id = "gw-test"
    s.skip_governance = False
    s.token_budget_enabled = False
    s.pii_sanitization_enabled = False
    s.ab_tests_json = ""
    s.tool_aware_enabled = False
    s.tool_strategy = "none"
    s.grpc_port = 50051
    return s


@pytest.fixture
def mock_ctx():
    """Minimal mock PipelineContext."""
    ctx = MagicMock()
    ctx.attestation_cache = None
    ctx.policy_cache = None
    ctx.budget_tracker = None
    ctx.session_chain = None
    ctx.content_analyzers = []
    ctx.tool_registry = None
    ctx.semantic_cache = None
    ctx.walacor_client = None
    ctx.wal_writer = None
    ctx.audit_exporter = None
    ctx.skip_governance = False
    ctx.capability_registry = None
    return ctx


@pytest.fixture
def servicer(mock_ctx, mock_settings):
    return GovernanceServicer(mock_ctx, mock_settings)


@pytest.fixture
def grpc_context():
    """Mock gRPC context for handlers."""
    ctx = MagicMock()
    ctx.set_code = MagicMock()
    ctx.set_details = MagicMock()
    return ctx


# ---------------------------------------------------------------------------
# HealthCheck
# ---------------------------------------------------------------------------

class TestHealthCheck:

    @pytest.mark.anyio
    async def test_health_returns_healthy(self, servicer, grpc_context):
        req = FakeEmpty()
        result = await servicer.HealthCheck(req, grpc_context)
        assert result.healthy is True
        assert result.governance_enabled is True
        assert isinstance(result.uptime_seconds, float)
        assert result.version == "0.1.0"

    @pytest.mark.anyio
    async def test_health_with_session_chain(self, mock_ctx, mock_settings, grpc_context):
        mock_ctx.session_chain = MagicMock()
        mock_ctx.session_chain.active_session_count.return_value = 42
        servicer = GovernanceServicer(mock_ctx, mock_settings)

        result = await servicer.HealthCheck(FakeEmpty(), grpc_context)
        assert result.active_sessions == 42

    @pytest.mark.anyio
    async def test_health_with_analyzers(self, mock_ctx, mock_settings, grpc_context):
        mock_ctx.content_analyzers = [MagicMock(), MagicMock()]
        servicer = GovernanceServicer(mock_ctx, mock_settings)

        result = await servicer.HealthCheck(FakeEmpty(), grpc_context)
        assert result.content_analyzer_count == 2

    @pytest.mark.anyio
    async def test_health_skip_governance(self, mock_ctx, mock_settings, grpc_context):
        mock_ctx.skip_governance = True
        servicer = GovernanceServicer(mock_ctx, mock_settings)

        result = await servicer.HealthCheck(FakeEmpty(), grpc_context)
        assert result.governance_enabled is False


# ---------------------------------------------------------------------------
# EvaluatePreInference
# ---------------------------------------------------------------------------

class TestEvaluatePreInference:

    @pytest.mark.anyio
    async def test_pass_through_no_caches(self, servicer, grpc_context):
        """With no attestation/policy caches, request should be allowed."""
        req = FakePreInferenceRequest(
            api_key="k", model_id="gpt-4o", provider="openai",
            prompt_text="hello", tenant_id="t", user_id="u",
            metadata={}, tools=[], request_type="", ab_tests_json="",
            session_id="s1",
        )
        result = await servicer.EvaluatePreInference(req, grpc_context)
        assert result.allowed is True
        assert result.model_id == "gpt-4o"
        assert result.policy_result == "pass"

    @pytest.mark.anyio
    async def test_auto_attest_when_no_entry(self, mock_ctx, mock_settings, grpc_context):
        """Auto-attestation when no control store and no sync client."""
        att_cache = MagicMock()
        att_cache.get.return_value = None
        mock_ctx.attestation_cache = att_cache
        mock_ctx.sync_client = None
        mock_ctx.control_store = None

        servicer = GovernanceServicer(mock_ctx, mock_settings)
        req = FakePreInferenceRequest(
            api_key="k", model_id="gpt-4o", provider="openai",
            prompt_text="hello", tenant_id="t", user_id="u",
            metadata={}, tools=[], request_type="", ab_tests_json="",
            session_id="s1",
        )
        result = await servicer.EvaluatePreInference(req, grpc_context)
        assert result.allowed is True
        assert result.attestation_id == "self-attested:gpt-4o"
        att_cache.set.assert_called_once()

    @pytest.mark.anyio
    async def test_denied_attestation_blocked(self, mock_ctx, mock_settings, grpc_context):
        """Denied when attestation entry is blocked."""
        entry = MagicMock()
        entry.is_blocked = True
        entry.attestation_id = "revoked-id"

        att_cache = MagicMock()
        att_cache.get.return_value = entry
        mock_ctx.attestation_cache = att_cache

        servicer = GovernanceServicer(mock_ctx, mock_settings)
        req = FakePreInferenceRequest(
            api_key="k", model_id="gpt-4o", provider="openai",
            prompt_text="hello", tenant_id="t", user_id="u",
            metadata={}, tools=[], request_type="", ab_tests_json="",
            session_id="s1",
        )
        result = await servicer.EvaluatePreInference(req, grpc_context)
        assert result.allowed is False
        assert result.denial_status_code == 403
        assert "revoked" in result.denial_reason

    @pytest.mark.anyio
    async def test_budget_exceeded(self, mock_ctx, mock_settings, grpc_context):
        """Denied when token budget is exceeded."""
        mock_settings.token_budget_enabled = True
        mock_ctx.budget_tracker = AsyncMock()
        mock_ctx.budget_tracker.check_and_reserve = AsyncMock(return_value=(False, 0))

        servicer = GovernanceServicer(mock_ctx, mock_settings)
        req = FakePreInferenceRequest(
            api_key="k", model_id="m", provider="p",
            prompt_text="hello", tenant_id="t", user_id="u",
            metadata={}, tools=[], request_type="", ab_tests_json="",
            session_id="s1",
        )
        result = await servicer.EvaluatePreInference(req, grpc_context)
        assert result.allowed is False
        assert result.denial_status_code == 429
        assert "budget" in result.denial_reason.lower()

    @pytest.mark.anyio
    async def test_budget_allowed(self, mock_ctx, mock_settings, grpc_context):
        """Allowed with remaining budget."""
        mock_settings.token_budget_enabled = True
        mock_ctx.budget_tracker = AsyncMock()
        mock_ctx.budget_tracker.check_and_reserve = AsyncMock(return_value=(True, 5000))

        servicer = GovernanceServicer(mock_ctx, mock_settings)
        req = FakePreInferenceRequest(
            api_key="k", model_id="m", provider="p",
            prompt_text="hello", tenant_id="t", user_id="u",
            metadata={}, tools=[], request_type="", ab_tests_json="",
            session_id="s1",
        )
        result = await servicer.EvaluatePreInference(req, grpc_context)
        assert result.allowed is True
        assert result.budget_remaining == 5000

    @pytest.mark.anyio
    async def test_pii_sanitization(self, mock_ctx, mock_settings, grpc_context):
        """PII sanitization replaces sensitive data with placeholders."""
        mock_settings.pii_sanitization_enabled = True

        servicer = GovernanceServicer(mock_ctx, mock_settings)
        req = FakePreInferenceRequest(
            api_key="k", model_id="m", provider="p",
            prompt_text="My SSN is 123-45-6789",
            tenant_id="t", user_id="u",
            metadata={}, tools=[], request_type="", ab_tests_json="",
            session_id="s1",
        )
        result = await servicer.EvaluatePreInference(req, grpc_context)
        assert result.allowed is True
        assert result.sanitized_prompt is not None
        assert "123-45-6789" not in result.sanitized_prompt
        assert result.pii_mapping  # non-empty JSON mapping
        mapping = json.loads(result.pii_mapping)
        assert any("123-45-6789" in v for v in mapping.values())

    @pytest.mark.anyio
    async def test_tool_definitions_injected(self, mock_ctx, mock_settings, grpc_context):
        """Tool definitions from the registry are included when tool_aware is on."""
        mock_settings.tool_aware_enabled = True
        mock_settings.tool_strategy = "active"

        registry = MagicMock()
        registry.get_tool_definitions.return_value = [
            {
                "type": "function",
                "function": {
                    "name": "web_search",
                    "description": "Search the web",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]
        mock_ctx.tool_registry = registry

        servicer = GovernanceServicer(mock_ctx, mock_settings)
        req = FakePreInferenceRequest(
            api_key="k", model_id="m", provider="p",
            prompt_text="hello", tenant_id="t", user_id="u",
            metadata={}, tools=[], request_type="", ab_tests_json="",
            session_id="s1",
        )
        result = await servicer.EvaluatePreInference(req, grpc_context)
        assert result.tool_strategy == "active"
        assert len(result.resolved_tools) == 1
        assert result.resolved_tools[0].name == "web_search"


# ---------------------------------------------------------------------------
# EvaluatePostInference
# ---------------------------------------------------------------------------

class TestEvaluatePostInference:

    @pytest.mark.anyio
    async def test_pass_no_analyzers(self, servicer, grpc_context):
        req = FakePostInferenceRequest(
            content="Hello world", thinking_content="", model_id="m",
            provider="p", audit_metadata={}, tool_interactions=[],
            prompt_tokens=10, completion_tokens=20, latency_ms=100.0,
            pii_mapping="", session_id="s1",
        )
        result = await servicer.EvaluatePostInference(req, grpc_context)
        assert result.policy_result == "pass"
        assert result.blocked is False

    @pytest.mark.anyio
    async def test_pii_restoration(self, mock_ctx, mock_settings, grpc_context):
        """PII placeholders are restored in post-inference."""
        servicer = GovernanceServicer(mock_ctx, mock_settings)
        mapping = {"[PII_SSN_1]": "123-45-6789"}
        req = FakePostInferenceRequest(
            content="Your SSN is [PII_SSN_1]", thinking_content="",
            model_id="m", provider="p", audit_metadata={},
            tool_interactions=[], prompt_tokens=0, completion_tokens=0,
            latency_ms=0, pii_mapping=json.dumps(mapping), session_id="s1",
        )
        result = await servicer.EvaluatePostInference(req, grpc_context)
        assert result.restored_content == "Your SSN is 123-45-6789"


# ---------------------------------------------------------------------------
# RecordExecution
# ---------------------------------------------------------------------------

class TestRecordExecution:

    @pytest.mark.anyio
    async def test_record_wal_write(self, mock_ctx, mock_settings, grpc_context):
        """Records are written to WAL writer."""
        mock_ctx.wal_writer = MagicMock()
        mock_ctx.wal_writer.write_and_fsync = MagicMock()

        servicer = GovernanceServicer(mock_ctx, mock_settings)
        req = FakeExecutionRecord(
            execution_id="exec-1", model_id="m", provider="p",
            prompt_text="hello", response_content="hi",
            thinking_content="", attestation_id="att-1",
            policy_version=1, policy_result="pass",
            latency_ms=50.0, prompt_tokens=5, completion_tokens=10,
            total_tokens=15, metadata={}, session_id="s1",
            sequence_number=0, previous_record_hash="",
            record_hash="", timestamp="2026-01-01T00:00:00Z",
            tenant_id="t", gateway_id="gw", user="u",
            provider_request_id="", model_hash="",
            variant_id="", retry_of="", timings_json="",
            cache_hit=False, cached_tokens=0, cache_creation_tokens=0,
            tool_interactions=[],
        )
        result = await servicer.RecordExecution(req, grpc_context)
        assert result.success is True
        assert result.execution_id == "exec-1"
        mock_ctx.wal_writer.write_and_fsync.assert_called_once()

    @pytest.mark.anyio
    async def test_record_wal_failure(self, mock_ctx, mock_settings, grpc_context):
        """WAL write failure returns success=False."""
        mock_ctx.wal_writer = MagicMock()
        mock_ctx.wal_writer.write_and_fsync = MagicMock(side_effect=IOError("disk full"))

        servicer = GovernanceServicer(mock_ctx, mock_settings)
        req = FakeExecutionRecord(
            execution_id="exec-2", model_id="m", provider="p",
            prompt_text="", response_content="", thinking_content="",
            attestation_id="", policy_version=0, policy_result="pass",
            latency_ms=0, prompt_tokens=0, completion_tokens=0,
            total_tokens=0, metadata={}, session_id="",
            sequence_number=0, previous_record_hash="",
            record_hash="", timestamp="", tenant_id="", gateway_id="",
            user="", provider_request_id="", model_hash="",
            variant_id="", retry_of="", timings_json="",
            cache_hit=False, cached_tokens=0, cache_creation_tokens=0,
            tool_interactions=[],
        )
        result = await servicer.RecordExecution(req, grpc_context)
        assert result.success is False
        assert "WAL write failed" in result.error_message

    @pytest.mark.anyio
    async def test_record_walacor_backend_write(self, mock_ctx, mock_settings, grpc_context):
        """Records are written to Walacor backend when client is configured."""
        mock_ctx.walacor_client = AsyncMock()
        mock_ctx.walacor_client.write_execution = AsyncMock()

        servicer = GovernanceServicer(mock_ctx, mock_settings)
        req = FakeExecutionRecord(
            execution_id="exec-3", model_id="m", provider="p",
            prompt_text="", response_content="", thinking_content="",
            attestation_id="", policy_version=0, policy_result="pass",
            latency_ms=0, prompt_tokens=0, completion_tokens=0,
            total_tokens=0, metadata={}, session_id="",
            sequence_number=0, previous_record_hash="",
            record_hash="", timestamp="", tenant_id="", gateway_id="",
            user="", provider_request_id="", model_hash="",
            variant_id="", retry_of="", timings_json="",
            cache_hit=False, cached_tokens=0, cache_creation_tokens=0,
            tool_interactions=[],
        )
        result = await servicer.RecordExecution(req, grpc_context)
        assert result.success is True
        mock_ctx.walacor_client.write_execution.assert_called_once()

    @pytest.mark.anyio
    async def test_record_no_writers_configured(self, mock_ctx, mock_settings, grpc_context):
        """No writers configured still returns success (nothing to fail)."""
        servicer = GovernanceServicer(mock_ctx, mock_settings)
        req = FakeExecutionRecord(
            execution_id="exec-4", model_id="m", provider="p",
            prompt_text="", response_content="", thinking_content="",
            attestation_id="", policy_version=0, policy_result="pass",
            latency_ms=0, prompt_tokens=0, completion_tokens=0,
            total_tokens=0, metadata={}, session_id="",
            sequence_number=0, previous_record_hash="",
            record_hash="", timestamp="", tenant_id="", gateway_id="",
            user="", provider_request_id="", model_hash="",
            variant_id="", retry_of="", timings_json="",
            cache_hit=False, cached_tokens=0, cache_creation_tokens=0,
            tool_interactions=[],
        )
        result = await servicer.RecordExecution(req, grpc_context)
        assert result.success is True


# ---------------------------------------------------------------------------
# NextChainValues
# ---------------------------------------------------------------------------

class TestNextChainValues:

    @pytest.mark.anyio
    async def test_no_session_chain(self, servicer, grpc_context):
        """Returns genesis hash when no session chain tracker."""
        req = FakeChainRequest(session_id="s1")
        result = await servicer.NextChainValues(req, grpc_context)
        assert result.sequence_number == 0
        assert result.previous_record_hash == "0" * 128

    @pytest.mark.anyio
    async def test_with_session_chain(self, mock_ctx, mock_settings, grpc_context):
        mock_ctx.session_chain = AsyncMock()
        mock_ctx.session_chain.next_chain_values = AsyncMock(return_value=(3, "abc123"))

        servicer = GovernanceServicer(mock_ctx, mock_settings)
        req = FakeChainRequest(session_id="s1")
        result = await servicer.NextChainValues(req, grpc_context)
        assert result.sequence_number == 3
        assert result.previous_record_hash == "abc123"

    @pytest.mark.anyio
    async def test_session_chain_error(self, mock_ctx, mock_settings, grpc_context):
        """Error in session chain sets gRPC error code."""
        mock_ctx.session_chain = AsyncMock()
        mock_ctx.session_chain.next_chain_values = AsyncMock(side_effect=RuntimeError("boom"))

        servicer = GovernanceServicer(mock_ctx, mock_settings)
        req = FakeChainRequest(session_id="s1")
        result = await servicer.NextChainValues(req, grpc_context)
        grpc_context.set_code.assert_called_once()
        grpc_context.set_details.assert_called_once()


# ---------------------------------------------------------------------------
# UpdateChain
# ---------------------------------------------------------------------------

class TestUpdateChain:

    @pytest.mark.anyio
    async def test_update_chain_computes_hash(self, mock_ctx, mock_settings, grpc_context):
        """UpdateChain computes a record hash and returns it."""
        mock_ctx.session_chain = AsyncMock()
        mock_ctx.session_chain.update = AsyncMock()

        servicer = GovernanceServicer(mock_ctx, mock_settings)
        req = FakeChainUpdate(
            session_id="s1", execution_id="exec-1",
            policy_version=1, policy_result="pass",
            timestamp="2026-01-01T00:00:00Z",
            sequence_number=0, previous_record_hash="0" * 128,
        )
        result = await servicer.UpdateChain(req, grpc_context)
        assert result.record_hash  # non-empty SHA3-512 hash
        assert len(result.record_hash) == 128  # SHA3-512 produces 128 hex chars
        assert result.sequence_number == 0
        mock_ctx.session_chain.update.assert_called_once()

    @pytest.mark.anyio
    async def test_update_chain_no_tracker(self, servicer, grpc_context):
        """Without a session chain tracker, still computes hash."""
        req = FakeChainUpdate(
            session_id="s1", execution_id="exec-1",
            policy_version=1, policy_result="pass",
            timestamp="2026-01-01T00:00:00Z",
            sequence_number=0, previous_record_hash="0" * 128,
        )
        result = await servicer.UpdateChain(req, grpc_context)
        assert result.record_hash
        assert len(result.record_hash) == 128


# ---------------------------------------------------------------------------
# AnalyzeContent
# ---------------------------------------------------------------------------

class TestAnalyzeContent:

    @pytest.mark.anyio
    async def test_no_analyzers(self, servicer, grpc_context):
        req = FakeContentPayload(text="hello", analysis_type="input", model_id="m")
        result = await servicer.AnalyzeContent(req, grpc_context)
        assert len(result.verdicts) == 0

    @pytest.mark.anyio
    async def test_with_pii_analyzer(self, mock_ctx, mock_settings, grpc_context):
        """PII analyzer flags PII in content."""
        from gateway.content.base import Decision, Verdict

        analyzer = MagicMock()
        analyzer.analyzer_id = "walacor.pii.v1"
        analyzer.timeout_ms = 50
        analyzer.analyze = AsyncMock(return_value=Decision(
            verdict=Verdict.WARN, confidence=0.9,
            analyzer_id="walacor.pii.v1", category="pii",
            reason="email_pattern_matched",
        ))
        mock_ctx.content_analyzers = [analyzer]

        servicer = GovernanceServicer(mock_ctx, mock_settings)
        req = FakeContentPayload(text="email: test@example.com", analysis_type="input", model_id="m")
        result = await servicer.AnalyzeContent(req, grpc_context)
        assert result.pii_detected is True
        assert len(result.verdicts) >= 1
        assert result.verdicts[0].analyzer_id == "walacor.pii.v1"

    @pytest.mark.anyio
    async def test_empty_text(self, mock_ctx, mock_settings, grpc_context):
        """Empty text returns no results."""
        mock_ctx.content_analyzers = [MagicMock()]
        servicer = GovernanceServicer(mock_ctx, mock_settings)
        req = FakeContentPayload(text="", analysis_type="input", model_id="m")
        result = await servicer.AnalyzeContent(req, grpc_context)
        assert len(result.verdicts) == 0


# ---------------------------------------------------------------------------
# ExecuteTool
# ---------------------------------------------------------------------------

class TestExecuteTool:

    @pytest.mark.anyio
    async def test_no_registry(self, servicer, grpc_context):
        """No tool registry returns error."""
        req = FakeToolRequest(name="web_search", arguments_json='{"q":"test"}', timeout_ms=5000)
        result = await servicer.ExecuteTool(req, grpc_context)
        assert result.is_error is True
        grpc_context.set_code.assert_called_once()

    @pytest.mark.anyio
    async def test_tool_success(self, mock_ctx, mock_settings, grpc_context):
        """Successful tool execution returns content."""
        from gateway.mcp.client import ToolResult

        registry = AsyncMock()
        registry.execute_tool = AsyncMock(return_value=ToolResult(
            content="search result text",
            is_error=False,
            duration_ms=150.0,
            sources=[{"title": "Wikipedia", "url": "https://en.wikipedia.org", "snippet": "text"}],
        ))
        mock_ctx.tool_registry = registry

        servicer = GovernanceServicer(mock_ctx, mock_settings)
        req = FakeToolRequest(name="web_search", arguments_json='{"q":"test"}', timeout_ms=5000)
        result = await servicer.ExecuteTool(req, grpc_context)
        assert result.is_error is False
        assert result.content == "search result text"
        assert result.duration_ms > 0
        assert len(result.sources) == 1
        assert result.sources[0].url == "https://en.wikipedia.org"

    @pytest.mark.anyio
    async def test_tool_error(self, mock_ctx, mock_settings, grpc_context):
        """Tool execution error returns is_error=True."""
        registry = AsyncMock()
        registry.execute_tool = AsyncMock(side_effect=RuntimeError("connection refused"))
        mock_ctx.tool_registry = registry

        servicer = GovernanceServicer(mock_ctx, mock_settings)
        req = FakeToolRequest(name="web_search", arguments_json='{}', timeout_ms=5000)
        result = await servicer.ExecuteTool(req, grpc_context)
        assert result.is_error is True
        assert "connection refused" in result.content


# ---------------------------------------------------------------------------
# CacheGet / CachePut
# ---------------------------------------------------------------------------

class TestCache:

    @pytest.mark.anyio
    async def test_cache_miss_no_cache(self, servicer, grpc_context):
        req = FakeCacheKey(prompt_text="hello", model_id="m")
        result = await servicer.CacheGet(req, grpc_context)
        assert result.hit is False

    @pytest.mark.anyio
    async def test_cache_hit(self, mock_ctx, mock_settings, grpc_context):
        """Cache hit returns response body."""
        entry = MagicMock()
        entry.response_body = b'{"choices": [{"text": "hi"}]}'
        entry.created_at = 1234567890.0

        cache = MagicMock()
        cache.get.return_value = entry
        mock_ctx.semantic_cache = cache

        servicer = GovernanceServicer(mock_ctx, mock_settings)
        req = FakeCacheKey(prompt_text="hello", model_id="m")
        result = await servicer.CacheGet(req, grpc_context)
        assert result.hit is True
        assert "choices" in result.response_body

    @pytest.mark.anyio
    async def test_cache_miss(self, mock_ctx, mock_settings, grpc_context):
        """Cache miss when no entry found."""
        cache = MagicMock()
        cache.get.return_value = None
        mock_ctx.semantic_cache = cache

        servicer = GovernanceServicer(mock_ctx, mock_settings)
        req = FakeCacheKey(prompt_text="hello", model_id="m")
        result = await servicer.CacheGet(req, grpc_context)
        assert result.hit is False

    @pytest.mark.anyio
    async def test_cache_put_no_cache(self, servicer, grpc_context):
        req = FakeCachePutRequest(prompt_text="hello", model_id="m", response_body='{"r":1}')
        result = await servicer.CachePut(req, grpc_context)
        assert result.success is False

    @pytest.mark.anyio
    async def test_cache_put_success(self, mock_ctx, mock_settings, grpc_context):
        cache = MagicMock()
        cache.put = MagicMock()
        mock_ctx.semantic_cache = cache

        servicer = GovernanceServicer(mock_ctx, mock_settings)
        req = FakeCachePutRequest(prompt_text="hello", model_id="m", response_body='{"r":1}')
        result = await servicer.CachePut(req, grpc_context)
        assert result.success is True
        cache.put.assert_called_once()


# ---------------------------------------------------------------------------
# Config and Context integration
# ---------------------------------------------------------------------------

class TestConfigFields:

    def test_grpc_config_defaults(self):
        """Verify gRPC config fields have correct defaults."""
        from gateway.config import Settings

        # Create with minimal valid config
        s = Settings(
            _env_file=None,
            skip_governance=True,
        )
        assert s.grpc_enabled is False
        assert s.grpc_port == 50051
        assert s.grpc_max_workers == 10


class TestContextField:

    def test_grpc_server_field_exists(self):
        """PipelineContext has grpc_server field."""
        from gateway.pipeline.context import PipelineContext
        ctx = PipelineContext()
        assert ctx.grpc_server is None
