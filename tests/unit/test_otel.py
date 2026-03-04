"""Unit tests for OpenTelemetry span emission (gateway.telemetry.otel)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from gateway.telemetry.otel import emit_inference_span, init_tracer


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture(params=["asyncio"])
def anyio_backend(request):
    return request.param


# ---------------------------------------------------------------------------
# init_tracer
# ---------------------------------------------------------------------------

def test_init_tracer_returns_none_without_sdk():
    """init_tracer must return None when opentelemetry is not installed."""
    with patch.dict("sys.modules", {
        "opentelemetry": None,
        "opentelemetry.sdk": None,
        "opentelemetry.sdk.resources": None,
        "opentelemetry.sdk.trace": None,
        "opentelemetry.sdk.trace.export": None,
        "opentelemetry.exporter.otlp.proto.grpc.trace_exporter": None,
    }):
        result = init_tracer("walacor-gateway", "http://localhost:4317")
    assert result is None


# ---------------------------------------------------------------------------
# emit_inference_span — noop when tracer is None
# ---------------------------------------------------------------------------

def test_emit_span_when_tracer_none_is_noop():
    """emit_inference_span must not raise when tracer is None."""
    # Should not raise
    emit_inference_span(
        tracer=None,
        provider="ollama",
        model_id="qwen3:4b",
        prompt_tokens=100,
        completion_tokens=50,
        execution_id="test-id",
        policy_result="pass",
        tenant_id="test-tenant",
        session_id="sess-1",
        tool_count=0,
        has_thinking=False,
    )


# ---------------------------------------------------------------------------
# emit_inference_span — correct attributes with in-memory exporter
# ---------------------------------------------------------------------------

def test_emit_span_attributes_set_correctly():
    """Verify all expected attributes are set on the span via in-memory exporter."""
    try:
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry import trace
    except ImportError:
        pytest.skip("opentelemetry-sdk not installed")

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")

    emit_inference_span(
        tracer=tracer,
        provider="ollama",
        model_id="qwen3:4b",
        prompt_tokens=120,
        completion_tokens=80,
        execution_id="exec-abc123",
        policy_result="pass",
        tenant_id="tenant-1",
        session_id="session-xyz",
        tool_count=2,
        has_thinking=True,
        provider_request_id="chatcmpl-999",
    )

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    attrs = dict(span.attributes or {})

    assert attrs["gen_ai.system"] == "ollama"
    assert attrs["gen_ai.request.model"] == "qwen3:4b"
    assert attrs["gen_ai.usage.input_tokens"] == 120
    assert attrs["gen_ai.usage.output_tokens"] == 80
    assert attrs["gen_ai.response.id"] == "chatcmpl-999"
    assert attrs["walacor.execution_id"] == "exec-abc123"
    assert attrs["walacor.policy_result"] == "pass"
    assert attrs["walacor.tenant_id"] == "tenant-1"
    assert attrs["walacor.session_id"] == "session-xyz"
    assert attrs["walacor.tool_count"] == 2
    assert attrs["walacor.has_thinking"] is True


def test_emit_span_no_session_omits_attribute():
    """session_id attribute should be absent when session_id is None."""
    try:
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    except ImportError:
        pytest.skip("opentelemetry-sdk not installed")

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")

    emit_inference_span(
        tracer=tracer,
        provider="openai",
        model_id="gpt-4o",
        session_id=None,
    )

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    attrs = dict(spans[0].attributes or {})
    assert "walacor.session_id" not in attrs
