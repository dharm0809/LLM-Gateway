"""Attestation-related models for gateway cache and sync."""

from __future__ import annotations

from pydantic import BaseModel, Field

from gateway.core.constants import AttestationStatus as AttestationStatusEnum


# Re-export for convenience
AttestationStatus = AttestationStatusEnum


class AttestationProofSummary(BaseModel):
    """Lightweight attestation entry for gateway cache."""

    attestation_id: str
    model_id: str
    revision: str
    provider: str = ""
    status: str = Field(..., description="verified, failed, revoked, tampered, pending")
    tenant_id: str = ""
    verification_level: str = "self_reported"
    last_verified_at: str | None = None
