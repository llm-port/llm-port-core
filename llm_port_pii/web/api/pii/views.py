"""PII scan, redact, and sanitize API endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Request

from llm_port_pii.services.pii.service import PIIService
from llm_port_pii.web.api.pii.schema import (
    DetectedEntityDTO,
    PIIDetokenizeRequest,
    PIIDetokenizeResponse,
    PIIRedactRequest,
    PIIRedactResponse,
    PIISanitizeRequest,
    PIISanitizeResponse,
    PIIScanRequest,
    PIIScanResponse,
)

router = APIRouter()


def _get_pii_service(request: Request) -> PIIService:
    """Retrieve the PIIService singleton from app state."""
    return request.app.state.pii_service  # type: ignore[no-any-return]


@router.post("/scan", response_model=PIIScanResponse)
async def scan_text(
    body: PIIScanRequest,
    request: Request,
) -> PIIScanResponse:
    """Detect PII entities in the provided text."""
    svc = _get_pii_service(request)
    result = await svc.scan(
        body.text,
        language=body.language,
        entities=body.entities,
        score_threshold=body.score_threshold,
    )
    return PIIScanResponse(
        has_pii=result.has_pii,
        entities=[
            DetectedEntityDTO(
                entity_type=e.entity_type,
                start=e.start,
                end=e.end,
                score=e.score,
                text=e.text,
            )
            for e in result.entities
        ],
    )


@router.post("/redact", response_model=PIIRedactResponse)
async def redact_text(
    body: PIIRedactRequest,
    request: Request,
) -> PIIRedactResponse:
    """Detect and redact PII entities from the provided text."""
    svc = _get_pii_service(request)
    result = await svc.redact(
        body.text,
        language=body.language,
        entities=body.entities,
        score_threshold=body.score_threshold,
    )
    return PIIRedactResponse(
        redacted_text=result.redacted_text,
        entities_found=result.entities_found,
    )


@router.post("/sanitize", response_model=PIISanitizeResponse)
async def sanitize_payload(
    body: PIISanitizeRequest,
    request: Request,
) -> PIISanitizeResponse:
    """Sanitize all text fields in an OpenAI-shaped payload.

    Walks ``messages[].content`` (string or multimodal array) and
    ``input`` (embeddings).  Supports ``redact`` and ``tokenize`` modes.
    """
    svc = _get_pii_service(request)
    result = await svc.sanitize_payload(
        body.payload,
        mode=body.mode,
        language=body.language,
        entities=body.entities,
        score_threshold=body.score_threshold,
    )
    return PIISanitizeResponse(
        sanitized_payload=result.payload,
        entities_found=result.entities_found,
        pii_report=[
            DetectedEntityDTO(
                entity_type=e.entity_type,
                start=e.start,
                end=e.end,
                score=e.score,
                text=e.text,
            )
            for e in result.pii_report
        ],
        token_mapping=result.token_mapping,
    )


@router.post("/detokenize", response_model=PIIDetokenizeResponse)
async def detokenize_payload(
    body: PIIDetokenizeRequest,
    request: Request,
) -> PIIDetokenizeResponse:
    """Reverse tokenization on an OpenAI-shaped response payload."""
    svc = _get_pii_service(request)
    restored = svc.detokenize_payload(body.payload, body.token_mapping)
    return PIIDetokenizeResponse(payload=restored)
