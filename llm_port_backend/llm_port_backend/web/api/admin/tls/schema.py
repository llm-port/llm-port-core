"""DTOs for /admin/tls endpoints."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class TlsCertSummaryDTO(BaseModel):
    """Lightweight metadata about a parsed PEM certificate."""

    subject: str
    issuer: str
    serial_hex: str
    fingerprint_sha256: str
    not_before: datetime
    not_after: datetime
    sans: list[str] = Field(default_factory=list)
    is_self_signed: bool


class TlsStatusDTO(BaseModel):
    """Current edge-TLS status as seen by the backend."""

    has_uploaded_material: bool
    has_activated_material: bool
    activated_cert: TlsCertSummaryDTO | None = None
    activated_paths: dict[str, str] | None = None


class TlsValidationDTO(BaseModel):
    """Result of validating an uploaded cert/key pair."""

    valid: bool
    errors: list[str] = Field(default_factory=list)
    cert: TlsCertSummaryDTO | None = None


class TlsActivateResponseDTO(BaseModel):
    """Response for POST /admin/tls/activate."""

    activated: bool
    paths: dict[str, str]
    restart_required: bool = True
