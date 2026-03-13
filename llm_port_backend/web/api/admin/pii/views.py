"""Admin PII dashboard — serves stats/events from the backend DB
and proxies config options from the PII micro-service.

Requires the PII module to be enabled (``pii_enabled`` setting).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from starlette import status

from llm_port_backend.db.dao.pii_event_dao import PIIEventDAO
from llm_port_backend.db.dao.system_settings_dao import SystemSettingsDAO
from llm_port_backend.db.models.users import User
from llm_port_backend.settings import settings
from llm_port_backend.web.api.admin.dependencies import require_superuser

logger = logging.getLogger(__name__)

router = APIRouter()

_pii_proxy_client: httpx.AsyncClient | None = None


def _get_pii_client() -> httpx.AsyncClient:
    global _pii_proxy_client
    if _pii_proxy_client is None:
        _pii_proxy_client = httpx.AsyncClient(timeout=15.0)
    return _pii_proxy_client

_PII_BASE: str = ""


def _pii_url() -> str:
    """Return the PII service base URL from settings."""
    url = settings.pii_service_url.strip().rstrip("/")
    if url and not url.startswith(("http://", "https://")):
        url = f"http://{url}"
    return url


def _normalize_setting_value(value_json: Any) -> Any:
    """Return logical value from system-setting JSON wrapper."""
    if isinstance(value_json, dict):
        return value_json.get("value", value_json)
    return value_json


def _fallback_pii_options() -> dict[str, Any]:
    """Fallback options when PII service is unavailable."""
    return {
        "source": "fallback",
        "supported_entities": [
            "PERSON",
            "EMAIL_ADDRESS",
            "PHONE_NUMBER",
            "CREDIT_CARD",
            "IBAN_CODE",
            "IP_ADDRESS",
            "US_SSN",
            "LOCATION",
            "DATE_TIME",
            "NRP",
            "MEDICAL_LICENSE",
            "URL",
        ],
        "supported_languages": ["en", "de", "es", "zh"],
        "supported_sanitize_modes": ["redact", "tokenize"],
        "telemetry_modes": ["sanitized", "metrics_only"],
        "egress_modes": ["redact", "tokenize_reversible"],
        "fail_actions": ["block", "allow", "fallback_to_local"],
        "default_language": "en",
        "default_score_threshold": 0.35,
    }


async def _system_default_policy(
    dao: SystemSettingsDAO,
) -> dict[str, Any] | None:
    """Read system default PII policy from backend system settings."""
    row = await dao.get_value("llm_port_api.pii_default_policy")
    if row is None:
        return None
    value = _normalize_setting_value(row.value_json)
    if isinstance(value, dict):
        return value
    return None


class TenantPolicyUpdateRequest(BaseModel):
    """Request body for tenant PII policy override updates."""

    pii_config: dict[str, Any] = Field(default_factory=dict)


@router.get("/config/options", name="pii_config_options")
async def get_pii_config_options(
    _user: Annotated[User, Depends(require_superuser)] = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    """Return normalized PII options for UI configuration forms."""
    fallback = _fallback_pii_options()
    try:
        resp = await _get_pii_client().get(
            f"{_pii_url()}/v1/pii/options",
            timeout=8.0,
        )
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return fallback

    entities = data.get("supported_entities")
    languages = data.get("supported_languages")
    sanitize_modes = data.get("supported_sanitize_modes")
    default_language = data.get("default_language")
    default_score_threshold = data.get("default_score_threshold")

    return {
        "source": "pii_service",
        "supported_entities": entities if isinstance(entities, list) else fallback["supported_entities"],
        "supported_languages": languages if isinstance(languages, list) else fallback["supported_languages"],
        "supported_sanitize_modes": sanitize_modes if isinstance(sanitize_modes, list) else fallback["supported_sanitize_modes"],
        "telemetry_modes": ["sanitized", "metrics_only"],
        "egress_modes": ["redact", "tokenize_reversible"],
        "fail_actions": ["block", "allow", "fallback_to_local"],
        "default_language": default_language if isinstance(default_language, str) and default_language else fallback["default_language"],
        "default_score_threshold": (
            float(default_score_threshold)
            if isinstance(default_score_threshold, (int, float))
            else fallback["default_score_threshold"]
        ),
    }


@router.get("/policies/tenants", name="pii_policy_tenants")
async def list_policy_tenants(
    request: Request,
    query: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    _user: Annotated[User, Depends(require_superuser)] = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    """Search tenant IDs from policy rows and gateway request logs."""
    session_factory = getattr(request.app.state, "llm_graph_trace_session_factory", None)
    if session_factory is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Gateway trace DB is not configured.",
        )

    query_val = (query or "").strip()
    like_val = f"%{query_val}%"
    async with session_factory() as gateway_session:
        result = await gateway_session.execute(
            text(
                """
                SELECT tenant_id
                FROM (
                    SELECT tenant_id FROM tenant_llm_policy
                    UNION
                    SELECT tenant_id FROM llm_gateway_request_log
                ) tenants
                WHERE (:query = '' OR tenant_id ILIKE :like_query)
                ORDER BY tenant_id ASC
                LIMIT :limit
                """,
            ),
            {"query": query_val, "like_query": like_val, "limit": limit},
        )
        items = [str(row[0]) for row in result.fetchall() if row[0]]
    return {"items": items}


@router.get("/policies/tenants/{tenant_id}", name="pii_policy_tenant_get")
async def get_tenant_policy(
    tenant_id: str,
    request: Request,
    settings_dao: SystemSettingsDAO = Depends(),
    _user: Annotated[User, Depends(require_superuser)] = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    """Return tenant override policy and effective resolved policy."""
    session_factory = getattr(request.app.state, "llm_graph_trace_session_factory", None)
    if session_factory is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Gateway trace DB is not configured.",
        )

    async with session_factory() as gateway_session:
        row = await gateway_session.execute(
            text(
                """
                SELECT pii_config
                FROM tenant_llm_policy
                WHERE tenant_id = :tenant_id
                """,
            ),
            {"tenant_id": tenant_id},
        )
        override = row.scalar_one_or_none()

    default_policy = await _system_default_policy(settings_dao)
    override_policy = override if isinstance(override, dict) else None
    effective_policy = override_policy or default_policy
    return {
        "tenant_id": tenant_id,
        "has_override": override_policy is not None,
        "override_policy": override_policy,
        "default_policy": default_policy,
        "effective_policy": effective_policy,
    }


@router.put("/policies/tenants/{tenant_id}", name="pii_policy_tenant_upsert")
async def upsert_tenant_policy(
    tenant_id: str,
    body: TenantPolicyUpdateRequest,
    request: Request,
    settings_dao: SystemSettingsDAO = Depends(),
    _user: Annotated[User, Depends(require_superuser)] = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    """Create or update tenant PII policy override (pii_config)."""
    session_factory = getattr(request.app.state, "llm_graph_trace_session_factory", None)
    if session_factory is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Gateway trace DB is not configured.",
        )

    async with session_factory() as gateway_session:
        await gateway_session.execute(
            text(
                """
                INSERT INTO tenant_llm_policy (tenant_id, pii_config, created_at, updated_at)
                VALUES (:tenant_id, :pii_config::json, NOW(), NOW())
                ON CONFLICT (tenant_id) DO UPDATE
                SET pii_config = EXCLUDED.pii_config,
                    updated_at = NOW()
                """,
            ),
            {
                "tenant_id": tenant_id,
                "pii_config": json.dumps(body.pii_config) if body.pii_config else None,
            },
        )
        await gateway_session.commit()

    default_policy = await _system_default_policy(settings_dao)
    return {
        "tenant_id": tenant_id,
        "has_override": True,
        "override_policy": body.pii_config,
        "default_policy": default_policy,
        "effective_policy": body.pii_config or default_policy,
    }


@router.delete("/policies/tenants/{tenant_id}", name="pii_policy_tenant_delete")
async def clear_tenant_policy(
    tenant_id: str,
    request: Request,
    settings_dao: SystemSettingsDAO = Depends(),
    _user: Annotated[User, Depends(require_superuser)] = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    """Clear tenant PII policy override (set pii_config to NULL)."""
    session_factory = getattr(request.app.state, "llm_graph_trace_session_factory", None)
    if session_factory is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Gateway trace DB is not configured.",
        )

    async with session_factory() as gateway_session:
        await gateway_session.execute(
            text(
                """
                UPDATE tenant_llm_policy
                SET pii_config = NULL,
                    updated_at = NOW()
                WHERE tenant_id = :tenant_id
                """,
            ),
            {"tenant_id": tenant_id},
        )
        await gateway_session.commit()

    default_policy = await _system_default_policy(settings_dao)
    return {
        "tenant_id": tenant_id,
        "has_override": False,
        "override_policy": None,
        "default_policy": default_policy,
        "effective_policy": default_policy,
    }


@router.get("/stats", name="pii_stats")
async def get_pii_stats(
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    dao: PIIEventDAO = Depends(),
    _user: Annotated[User, Depends(require_superuser)] = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    """Return aggregate PII processing statistics from the backend DB."""
    return await dao.get_stats(since=since, until=until)


@router.get("/events", name="pii_events")
async def list_pii_events(
    operation: str | None = Query(default=None),
    source: str | None = Query(default=None),
    pii_only: bool = Query(default=False),
    since: datetime | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    dao: PIIEventDAO = Depends(),
    _user: Annotated[User, Depends(require_superuser)] = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    """Return paginated PII scan events from the backend DB."""
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
    return {
        "items": [
            {
                "id": str(e.id),
                "created_at": e.created_at.isoformat() if e.created_at else "",
                "operation": e.operation,
                "mode": e.mode,
                "language": e.language,
                "score_threshold": e.score_threshold,
                "pii_detected": e.pii_detected,
                "entities_found": e.entities_found,
                "entity_type_counts": e.entity_type_counts,
                "source": e.source,
                "request_id": e.request_id,
            }
            for e in items
        ],
        "total": total,
    }


# ── Internal event-log ingestion endpoint ────────────────────────


class PIIEventLogRequest(BaseModel):
    """Body accepted by the internal event-log endpoint."""

    operation: str
    mode: str | None = None
    language: str = "en"
    score_threshold: float = 0.6
    pii_detected: bool = False
    entities_found: int = 0
    entity_type_counts: dict[str, int] | None = None
    source: str = "api"
    request_id: str | None = None


@router.post("/events/log", name="pii_event_log")
async def log_pii_event(
    body: PIIEventLogRequest,
    dao: PIIEventDAO = Depends(),
) -> dict[str, str]:
    """Accept a PII scan event from the PII service or gateway.

    This is an **internal** endpoint — no superuser check so that the
    PII micro-service can call it without user credentials.
    """
    event = await dao.log_event(
        operation=body.operation,
        mode=body.mode,
        language=body.language,
        score_threshold=body.score_threshold,
        pii_detected=body.pii_detected,
        entities_found=body.entities_found,
        entity_type_counts=body.entity_type_counts,
        source=body.source,
        request_id=body.request_id,
    )
    return {"id": str(event.id)}
