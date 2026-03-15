"""Validate that governance.proto can be parsed and compiled by grpc_tools.protoc.

These tests verify the proto schema is syntactically valid, contains the expected
service/messages, and can generate Python stubs without error.  They do NOT
require a running gRPC server.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import textwrap

import pytest

PROTO_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "proto")
PROTO_FILE = os.path.join(PROTO_DIR, "governance.proto")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_proto() -> str:
    """Return the raw proto file contents."""
    with open(PROTO_FILE) as f:
        return f.read()


def _compile_proto(out_dir: str) -> subprocess.CompletedProcess:
    """Run grpc_tools.protoc on the proto file, writing stubs to *out_dir*."""
    return subprocess.run(
        [
            sys.executable, "-m", "grpc_tools.protoc",
            f"--proto_path={PROTO_DIR}",
            f"--python_out={out_dir}",
            f"--grpc_python_out={out_dir}",
            f"--pyi_out={out_dir}",
            "governance.proto",
        ],
        capture_output=True,
        text=True,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestProtoFileExists:
    """Basic file-level checks."""

    def test_proto_file_exists(self):
        assert os.path.isfile(PROTO_FILE), f"Proto file not found at {PROTO_FILE}"

    def test_proto_syntax_is_proto3(self):
        content = _read_proto()
        assert 'syntax = "proto3"' in content

    def test_proto_package(self):
        content = _read_proto()
        assert "package walacor.governance.v1;" in content

    def test_proto_go_package(self):
        content = _read_proto()
        assert 'option go_package = "proxy/pb"' in content


class TestProtoServiceDefinition:
    """Verify the service and RPC methods are defined."""

    def test_service_name(self):
        content = _read_proto()
        assert "service GovernanceEngine" in content

    @pytest.mark.parametrize("rpc_name", [
        "EvaluatePreInference",
        "EvaluatePostInference",
        "RecordExecution",
        "NextChainValues",
        "UpdateChain",
        "AnalyzeContent",
        "ExecuteTool",
        "CacheGet",
        "CachePut",
        "HealthCheck",
    ])
    def test_rpc_method_defined(self, rpc_name: str):
        content = _read_proto()
        assert f"rpc {rpc_name}" in content


class TestProtoMessages:
    """Verify all required message types are defined."""

    @pytest.mark.parametrize("message_name", [
        "PreInferenceRequest",
        "PreInferenceResult",
        "PostInferenceRequest",
        "PostInferenceResult",
        "ExecutionRecord",
        "ChainRequest",
        "ChainValues",
        "ChainUpdate",
        "ChainResult",
        "ContentPayload",
        "AnalysisResult",
        "DLPFinding",
        "ContentVerdict",
        "ToolDefinition",
        "ToolRequest",
        "ToolResponse",
        "ToolSource",
        "ToolInteraction",
        "CacheKey",
        "CacheResponse",
        "CachePutRequest",
        "CacheResult",
        "PolicyDecision",
        "Empty",
        "HealthStatus",
        "WriteResult",
    ])
    def test_message_defined(self, message_name: str):
        content = _read_proto()
        assert f"message {message_name}" in content


class TestProtoKeyFields:
    """Spot-check critical fields in key messages."""

    def test_pre_inference_has_api_key(self):
        content = _read_proto()
        assert "string api_key" in content

    def test_pre_inference_has_model_id(self):
        content = _read_proto()
        assert "string model_id" in content

    def test_pre_inference_result_has_allowed(self):
        content = _read_proto()
        assert "bool allowed" in content

    def test_pre_inference_result_has_optional_budget(self):
        content = _read_proto()
        assert "optional int64 budget_remaining" in content

    def test_pre_inference_result_has_optional_sanitized_prompt(self):
        content = _read_proto()
        assert "optional string sanitized_prompt" in content

    def test_execution_record_has_chain_fields(self):
        content = _read_proto()
        assert "string record_hash" in content
        assert "string previous_record_hash" in content
        assert "int32 sequence_number" in content

    def test_tool_interaction_has_hashes(self):
        content = _read_proto()
        assert "string input_hash" in content
        assert "string output_hash" in content

    def test_health_status_has_model_capabilities_map(self):
        content = _read_proto()
        assert "map<string, string> model_capabilities" in content

    def test_post_inference_result_has_optional_restored_content(self):
        content = _read_proto()
        assert "optional string restored_content" in content


class TestProtoCompilation:
    """Test that grpc_tools.protoc can compile the proto file."""

    @pytest.fixture(autouse=True)
    def _check_grpc_tools(self):
        """Skip if grpcio-tools is not installed."""
        try:
            import grpc_tools  # noqa: F401
        except ImportError:
            pytest.skip("grpcio-tools not installed — install with: pip install 'walacor-gateway[grpc]'")

    def test_protoc_succeeds(self):
        """Compile the proto and verify exit code 0."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = _compile_proto(tmpdir)
            assert result.returncode == 0, (
                f"protoc failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
            )

    def test_generated_pb2_importable(self):
        """Compile and verify the generated _pb2 module can be imported."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = _compile_proto(tmpdir)
            assert result.returncode == 0, f"protoc failed: {result.stderr}"

            # Import the generated module
            sys.path.insert(0, tmpdir)
            try:
                import importlib
                mod = importlib.import_module("governance_pb2")

                # Verify key message classes exist
                assert hasattr(mod, "PreInferenceRequest")
                assert hasattr(mod, "PreInferenceResult")
                assert hasattr(mod, "PostInferenceRequest")
                assert hasattr(mod, "PostInferenceResult")
                assert hasattr(mod, "ExecutionRecord")
                assert hasattr(mod, "HealthStatus")
                assert hasattr(mod, "ToolInteraction")
                assert hasattr(mod, "Empty")
            finally:
                sys.path.pop(0)
                # Clean up from sys.modules
                for key in list(sys.modules):
                    if key.startswith("governance_pb2"):
                        del sys.modules[key]

    def test_generated_grpc_importable(self):
        """Compile and verify the generated _pb2_grpc module can be imported."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = _compile_proto(tmpdir)
            assert result.returncode == 0, f"protoc failed: {result.stderr}"

            sys.path.insert(0, tmpdir)
            try:
                import importlib
                mod = importlib.import_module("governance_pb2_grpc")

                # Verify the service stub and servicer classes exist
                assert hasattr(mod, "GovernanceEngineStub")
                assert hasattr(mod, "GovernanceEngineServicer")
                assert hasattr(mod, "add_GovernanceEngineServicer_to_server")
            finally:
                sys.path.pop(0)
                for key in list(sys.modules):
                    if key.startswith("governance_pb2"):
                        del sys.modules[key]

    def test_generated_pyi_exists(self):
        """Compile and verify the .pyi type stub file is generated."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = _compile_proto(tmpdir)
            assert result.returncode == 0, f"protoc failed: {result.stderr}"
            pyi_path = os.path.join(tmpdir, "governance_pb2.pyi")
            assert os.path.isfile(pyi_path), f"Type stub not generated at {pyi_path}"

    def test_message_instantiation(self):
        """Verify a generated message can be instantiated with field values."""
        with tempfile.TemporaryDirectory() as tmpdir:
            result = _compile_proto(tmpdir)
            assert result.returncode == 0, f"protoc failed: {result.stderr}"

            sys.path.insert(0, tmpdir)
            try:
                import importlib
                mod = importlib.import_module("governance_pb2")

                # Create a PreInferenceRequest with some fields
                req = mod.PreInferenceRequest(
                    api_key="test-key",
                    model_id="gpt-4o",
                    provider="openai",
                    prompt_text="Hello world",
                    tenant_id="tenant-1",
                    user_id="user-1",
                    session_id="sess-abc",
                )
                assert req.api_key == "test-key"
                assert req.model_id == "gpt-4o"
                assert req.provider == "openai"
                assert req.session_id == "sess-abc"

                # Create an Empty message
                empty = mod.Empty()
                assert empty.ByteSize() == 0

                # Create a HealthStatus with a map field
                hs = mod.HealthStatus(
                    healthy=True,
                    version="0.1.0",
                    active_sessions=42,
                    governance_enabled=True,
                )
                hs.model_capabilities["qwen3:4b"] = '{"supports_tools": true}'
                assert hs.healthy is True
                assert hs.active_sessions == 42
                assert "qwen3:4b" in hs.model_capabilities
            finally:
                sys.path.pop(0)
                for key in list(sys.modules):
                    if key.startswith("governance_pb2"):
                        del sys.modules[key]
