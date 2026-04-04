"""PII scan, redact, sanitize API endpoints.

The PII service is **stateless** — event telemetry is forwarded to the
backend service which owns the ``pii_scan_events`` table.  Stats and
event-log endpoints are served by the backend directly.
"""

from __future__ import annotations

import logging

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from llm_port_pii.services.pii.service import DEFAULT_ENTITIES, SUPPORTED_LANGUAGES
from llm_port_pii.services.pii.service import PIIService
from llm_port_pii.settings import settings
from llm_port_pii.web.api.pii.schema import (
    DetectedEntityDTO,
    PIIPolicyOptionsResponse,
    PIIRedactRequest,
    PIIRedactResponse,
    PIISanitizeRequest,
    PIISanitizeResponse,
    PIIScanRequest,
    PIIScanResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# ── Shared httpx client for fire-and-forget event logging ─────────
_event_client: httpx.AsyncClient | None = None


def _get_event_client() -> httpx.AsyncClient:
    global _event_client
    if _event_client is None:
        _event_client = httpx.AsyncClient(timeout=5.0)
    return _event_client


def _backend_event_url() -> str:
    """Build the backend event-log ingestion URL."""
    url = settings.backend_url.strip().rstrip("/")
    if url and not url.startswith(("http://", "https://")):
        url = f"http://{url}"
    return f"{url}/api/admin/pii/events/log"


async def _log_event_async(
    *,
    operation: str,
    mode: str | None = None,
    language: str = "en",
    score_threshold: float = 0.35,
    pii_detected: bool = False,
    entities_found: int = 0,
    entity_type_counts: dict[str, int] | None = None,
    source: str = "api",
    request_id: str | None = None,
) -> None:
    """Fire-and-forget POST to the backend event-log endpoint."""
    url = _backend_event_url()
    if not url:
        return
    payload = {
        "operation": operation,
        "mode": mode,
        "language": language,
        "score_threshold": score_threshold,
        "pii_detected": pii_detected,
        "entities_found": entities_found,
        "entity_type_counts": entity_type_counts,
        "source": source,
        "request_id": request_id,
    }
    try:
        await _get_event_client().post(url, json=payload)
    except Exception:
        logger.debug("Failed to forward PII event to backend", exc_info=True)


def _get_pii_service(request: Request) -> PIIService:
    """Retrieve the PIIService singleton from app state."""
    return request.app.state.pii_service  # type: ignore[no-any-return]


@router.get("/options", response_model=PIIPolicyOptionsResponse)
async def get_policy_options() -> PIIPolicyOptionsResponse:
    """Return supported option values for policy configuration UIs."""
    return PIIPolicyOptionsResponse(
        supported_entities=list(DEFAULT_ENTITIES),
        supported_languages=list(SUPPORTED_LANGUAGES),
        supported_sanitize_modes=["redact"],
        default_language=settings.pii_default_language,
        default_score_threshold=settings.pii_score_threshold,
    )


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
    # Build entity-type breakdown
    type_counts: dict[str, int] = {}
    for e in result.entities:
        type_counts[e.entity_type] = type_counts.get(e.entity_type, 0) + 1
    # Fire-and-forget event to backend (no raw text stored)
    await _log_event_async(
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
) -> PIIRedactResponse:
    """Detect and redact PII entities from the provided text."""
    svc = _get_pii_service(request)
    result = await svc.redact(
        body.text,
        language=body.language,
        entities=body.entities,
        score_threshold=body.score_threshold,
    )
    await _log_event_async(
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
) -> PIISanitizeResponse | JSONResponse:
    """Sanitize all text fields in an OpenAI-shaped payload.

    Walks ``messages[].content`` (string or multimodal array) and
    ``input`` (embeddings).  Supports ``redact`` mode only.

    The ``tokenize`` mode and ``/detokenize`` endpoint are available
    in the **PII Pro** enterprise module.
    """
    if body.mode != "redact":
        return JSONResponse(
            status_code=400,
            content={
                "detail": (
                    f"Unsupported sanitize mode '{body.mode}'. "
                    "Core PII supports 'redact' only. "
                    "Use the PII Pro module for tokenize/detokenize."
                ),
            },
        )
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
    await _log_event_async(
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
