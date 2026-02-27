"""Pydantic request/response schemas for PII endpoints."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PIIScanRequest(BaseModel):
    """Request body for POST /api/v1/pii/scan."""

    text: str = Field(..., min_length=1, description="Text to scan for PII entities.")
    language: str | None = Field(
        default=None,
        description="ISO 639-1 language code (default: en).",
    )
    entities: list[str] | None = Field(
        default=None,
        description="Restrict detection to these entity types.",
    )
    score_threshold: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Minimum confidence score (0-1). Default: 0.35.",
    )


class DetectedEntityDTO(BaseModel):
    """One PII entity found in the scanned text."""

    entity_type: str
    start: int
    end: int
    score: float
    text: str


class PIIScanResponse(BaseModel):
    """Response body for POST /api/v1/pii/scan."""

    has_pii: bool
    entities: list[DetectedEntityDTO]


class PIIRedactRequest(BaseModel):
    """Request body for POST /api/v1/pii/redact."""

    text: str = Field(..., min_length=1, description="Text to redact PII from.")
    language: str | None = Field(
        default=None,
        description="ISO 639-1 language code (default: en).",
    )
    entities: list[str] | None = Field(
        default=None,
        description="Restrict redaction to these entity types.",
    )
    score_threshold: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Minimum confidence score (0-1). Default: 0.35.",
    )


class PIIRedactResponse(BaseModel):
    """Response body for POST /api/v1/pii/redact."""

    redacted_text: str
    entities_found: int
