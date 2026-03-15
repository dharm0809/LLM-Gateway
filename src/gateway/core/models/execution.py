"""Execution record models for gateway WAL and control plane API."""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from gateway.core.crypto import validate_sha3_512_hex


class ExecutionRecord(BaseModel):
    """Record written to gateway WAL and sent to control plane."""

    # ── Identity ──────────────────────────────────────────────────────────────
    execution_id: str = Field(..., description="UUID assigned by the gateway for this inference")
    tenant_id: str = Field(..., description="Tenant that owns this record")
    gateway_id: str = Field(..., description="Gateway instance that produced this record")
    timestamp: str = Field(..., description="ISO 8601 UTC timestamp of the inference")

    # ── Attestation ───────────────────────────────────────────────────────────
    model_attestation_id: str = Field(
        ..., description="Attestation proof ID from the control plane (att_* or ver_*)"
    )

    # ── Cryptographic hashes ──────────────────────────────────────────────────
    prompt_hash: str = Field(..., description="SHA3-512 hex digest of the prompt text (128 chars)")
    response_hash: str = Field(..., description="SHA3-512 hex digest of the response content (128 chars)")

    # ── Policy ────────────────────────────────────────────────────────────────
    policy_version: int = Field(..., description="Version of the policy set evaluated at inference time")
    policy_result: str = Field(..., description="Aggregate policy result: 'pass' or 'blocked_by_policy'")

    # ── Session chain (G5) ────────────────────────────────────────────────────
    session_id: str | None = Field(default=None, description="Caller-supplied session identifier")
    sequence_number: int | None = Field(
        default=None, description="Monotonically increasing turn number within the session"
    )
    previous_record_hash: str | None = Field(
        default=None, description="SHA3-512 hash of the preceding record in this session chain"
    )
    record_hash: str | None = Field(
        default=None,
        description="SHA3-512 hash of this record's canonical fields; links into the session chain",
    )

    # ── Full-fidelity content ─────────────────────────────────────────────────
    prompt_text: str | None = Field(default=None, description="Actual prompt text sent to the model")
    response_content: str | None = Field(default=None, description="Actual response content from the model")

    # ── Provider metadata ─────────────────────────────────────────────────────
    provider_request_id: str | None = Field(
        default=None,
        description="Request ID assigned by the provider (e.g. chatcmpl-xxx, msg_xxx)",
    )
    model_hash: str | None = Field(
        default=None,
        description="Cryptographic digest of the model weights/binary from the MEE (e.g. Ollama sha256 digest)",
    )

    # ── Caller context ────────────────────────────────────────────────────────
    user: str | None = Field(default=None, description="End-user identifier forwarded from the caller")
    metadata: dict | None = Field(default=None, description="Supplementary runtime metadata (token usage, policy details, etc.)")

    @field_validator("prompt_hash", "response_hash")
    @classmethod
    def validate_hashes(cls, v: str) -> str:
        out = validate_sha3_512_hex(v)
        if out is None:
            raise ValueError("Hash cannot be empty")
        return out


class GatewayExecutionRequest(ExecutionRecord):
    """Request body for POST /v1/gateway/executions (control plane delivery)."""
