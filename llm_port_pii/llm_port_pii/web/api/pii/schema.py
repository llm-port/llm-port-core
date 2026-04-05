"""Pydantic request/response schemas for PII endpoints."""

from __future__ import annotations

from typing import Any

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


# ---------------------------------------------------------------
# OpenAI-shaped payload sanitization
# ---------------------------------------------------------------


class PIISanitizeRequest(BaseModel):
    """Request body for POST /api/v1/pii/sanitize.

    Accepts a full OpenAI-compatible payload (chat completions or
    embeddings).  All text-bearing fields (``messages[].content``,
    ``input``) are walked and sanitized.
    """

    payload: dict[str, Any] = Field(
        ...,
        description="Full OpenAI-shaped request payload to sanitize.",
    )
    mode: str = Field(
        default="redact",
        description=(
            "Sanitization mode. 'redact' replaces PII with entity-type "
            "tags like <PERSON>. 'tokenize' replaces PII with reversible "
            "surrogate tokens like [PERSON_1] and returns a token_mapping."
        ),
    )
    language: str | None = Field(default=None)
    entities: list[str] | None = Field(default=None)
    score_threshold: float | None = Field(default=None, ge=0.0, le=1.0)


class PIISanitizeResponse(BaseModel):
    """Response body for POST /api/v1/pii/sanitize."""

    sanitized_payload: dict[str, Any]
    entities_found: int
    pii_report: list[DetectedEntityDTO]
    token_mapping: dict[str, str] | None = Field(
        default=None,
        description="Only present when mode='tokenize'. Maps tokens to original text.",
    )


class PIIDetokenizeRequest(BaseModel):
    """Request body for POST /api/v1/pii/detokenize.

    Reverses tokenization on an OpenAI-shaped response payload using
    a previously returned ``token_mapping``.
    """

    payload: dict[str, Any] = Field(
        ...,
        description="OpenAI-shaped response payload containing PII tokens.",
    )
    token_mapping: dict[str, str] = Field(
        ...,
        description="Token-to-original-text mapping from a prior /sanitize call.",
    )


class PIIDetokenizeResponse(BaseModel):
    """Response body for POST /api/v1/pii/detokenize."""

    payload: dict[str, Any]


class PIIPolicyOptionsResponse(BaseModel):
    """Supported policy option values for admin configuration UIs."""

    supported_entities: list[str]
    supported_languages: list[str]
    supported_sanitize_modes: list[str]
    default_language: str
    default_score_threshold: float


# ---------------------------------------------------------------
# Dashboard stats & event log
# ---------------------------------------------------------------


class DailyVolumeDTO(BaseModel):
    """One day's scan counts."""

    date: str
    count: int
    pii_count: int


class PIIStatsResponse(BaseModel):
    """Aggregate PII processing statistics for the dashboard."""

    total_scans: int
    total_with_pii: int
    total_entities: int
    detection_rate: float
    entity_type_breakdown: dict[str, int]
    operation_breakdown: dict[str, int]
    source_breakdown: dict[str, int]
    daily_volume: list[DailyVolumeDTO]


class PIIEventDTO(BaseModel):
    """One PII scan event (no raw text stored)."""

    id: str
    created_at: str
    operation: str
    mode: str | None = None
    language: str
    score_threshold: float
    pii_detected: bool
    entities_found: int
    entity_type_counts: dict[str, int] | None = None
    source: str
    request_id: str | None = None


class PIIEventsResponse(BaseModel):
    """Paginated list of PII events."""

    items: list[PIIEventDTO]
    total: int
