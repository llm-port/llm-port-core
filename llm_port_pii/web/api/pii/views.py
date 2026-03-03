"""PII scan, redact, sanitize API endpoints, plus stats/events dashboard."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query, Request

from llm_port_pii.db.dao.pii_event_dao import PIIEventDAO
from llm_port_pii.services.pii.service import PIIService
from llm_port_pii.services.pii.service import DEFAULT_ENTITIES, SUPPORTED_LANGUAGES
from llm_port_pii.settings import settings
from llm_port_pii.web.api.pii.schema import (
    DetectedEntityDTO,
    PIIDetokenizeRequest,
    PIIDetokenizeResponse,
    PIIEventDTO,
    PIIEventsResponse,
    PIIRedactRequest,
    PIIRedactResponse,
    PIISanitizeRequest,
    PIISanitizeResponse,
    PIIPolicyOptionsResponse,
    PIIScanRequest,
    PIIScanResponse,
    PIIStatsResponse,
)

router = APIRouter()


def _get_pii_service(request: Request) -> PIIService:
    """Retrieve the PIIService singleton from app state."""
    return request.app.state.pii_service  # type: ignore[no-any-return]


@router.get("/options", response_model=PIIPolicyOptionsResponse)
async def get_policy_options() -> PIIPolicyOptionsResponse:
    """Return supported option values for policy configuration UIs."""
    return PIIPolicyOptionsResponse(
        supported_entities=list(DEFAULT_ENTITIES),
        supported_languages=list(SUPPORTED_LANGUAGES),
        supported_sanitize_modes=["redact", "tokenize"],
        default_language=settings.pii_default_language,
        default_score_threshold=settings.pii_score_threshold,
    )


@router.post("/scan", response_model=PIIScanResponse)
async def scan_text(
    body: PIIScanRequest,
    request: Request,
    dao: PIIEventDAO = Depends(),
) -> PIIScanResponse:
    """Detect PII entities in the provided text."""
    svc = _get_pii_service(request)
    result = await svc.scan(
        body.text,
        language=body.language,
        entities=body.entities,
        score_threshold=body.score_threshold,
    )
    # Build entity-type breakdown
    type_counts: dict[str, int] = {}
    for e in result.entities:
        type_counts[e.entity_type] = type_counts.get(e.entity_type, 0) + 1
    # Log event (no raw text stored)
    await dao.log_event(
        operation="scan",
        language=body.language or "en",
        score_threshold=body.score_threshold or 0.35,
        pii_detected=result.has_pii,
        entities_found=len(result.entities),
        entity_type_counts=type_counts or None,
        source=request.headers.get("x-pii-source", "api"),
        request_id=request.headers.get("x-request-id"),
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
    dao: PIIEventDAO = Depends(),
) -> PIIRedactResponse:
    """Detect and redact PII entities from the provided text."""
    svc = _get_pii_service(request)
    result = await svc.redact(
        body.text,
        language=body.language,
        entities=body.entities,
        score_threshold=body.score_threshold,
    )
    await dao.log_event(
        operation="redact",
        mode="redact",
        language=body.language or "en",
        score_threshold=body.score_threshold or 0.35,
        pii_detected=result.entities_found > 0,
        entities_found=result.entities_found,
        source=request.headers.get("x-pii-source", "api"),
        request_id=request.headers.get("x-request-id"),
    )
    return PIIRedactResponse(
        redacted_text=result.redacted_text,
        entities_found=result.entities_found,
    )


@router.post("/sanitize", response_model=PIISanitizeResponse)
async def sanitize_payload(
    body: PIISanitizeRequest,
    request: Request,
    dao: PIIEventDAO = Depends(),
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
    type_counts: dict[str, int] = {}
    for e in result.pii_report:
        type_counts[e.entity_type] = type_counts.get(e.entity_type, 0) + 1
    await dao.log_event(
        operation="sanitize",
        mode=body.mode,
        language=body.language or "en",
        score_threshold=body.score_threshold or 0.35,
        pii_detected=result.entities_found > 0,
        entities_found=result.entities_found,
        entity_type_counts=type_counts or None,
        source=request.headers.get("x-pii-source", "api"),
        request_id=request.headers.get("x-request-id"),
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


# ---------------------------------------------------------------
# Dashboard & event-log read endpoints
# ---------------------------------------------------------------


@router.get("/stats", response_model=PIIStatsResponse)
async def get_pii_stats(
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    dao: PIIEventDAO = Depends(),
) -> PIIStatsResponse:
    """Return aggregate PII processing statistics (no raw data)."""
    data = await dao.get_stats(since=since, until=until)
    return PIIStatsResponse(**data)


@router.get("/events", response_model=PIIEventsResponse)
async def list_pii_events(
    operation: str | None = Query(default=None),
    source: str | None = Query(default=None),
    pii_only: bool = Query(default=False),
    since: datetime | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    dao: PIIEventDAO = Depends(),
) -> PIIEventsResponse:
    """Return paginated PII scan events (metadata only, no raw text)."""
    items = await dao.list_events(
        operation=operation,
        source=source,
        pii_only=pii_only,
        since=since,
        limit=limit,
        offset=offset,
    )
    total = await dao.count_events(
        operation=operation,
        source=source,
        pii_only=pii_only,
        since=since,
    )
    return PIIEventsResponse(
        items=[
            PIIEventDTO(
                id=str(e.id),
                created_at=e.created_at.isoformat() if e.created_at else "",
                operation=e.operation,
                mode=e.mode,
                language=e.language,
                score_threshold=e.score_threshold,
                pii_detected=e.pii_detected,
                entities_found=e.entities_found,
                entity_type_counts=e.entity_type_counts,
                source=e.source,
                request_id=e.request_id,
            )
            for e in items
        ],
        total=total,
    )
