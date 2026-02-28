"""Admin PII dashboard — proxies stats/events from the PII micro-service.

Requires the PII module to be enabled (``pii_enabled`` setting).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from starlette import status

from llm_port_backend.db.models.users import User
from llm_port_backend.settings import settings
from llm_port_backend.web.api.admin.dependencies import require_superuser

logger = logging.getLogger(__name__)

router = APIRouter()

_PII_BASE: str = ""


def _pii_url() -> str:
    """Return the PII service base URL from settings."""
    return settings.pii_service_url.rstrip("/")


@router.get("/stats", name="pii_stats")
async def get_pii_stats(
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    _user: Annotated[User, Depends(require_superuser)] = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    """Proxy PII processing statistics from the PII service."""
    params: dict[str, str] = {}
    if since:
        params["since"] = since.isoformat()
    if until:
        params["until"] = until.isoformat()

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{_pii_url()}/v1/pii/stats",
                params=params,
                timeout=10.0,
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as exc:
        logger.warning("PII stats request failed: %s", exc)
        raise HTTPException(
            status_code=exc.response.status_code,
            detail="PII service returned an error.",
        ) from exc
    except Exception as exc:
        logger.exception("Failed to reach PII service for stats")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="PII service unreachable.",
        ) from exc


@router.get("/events", name="pii_events")
async def list_pii_events(
    operation: str | None = Query(default=None),
    source: str | None = Query(default=None),
    pii_only: bool = Query(default=False),
    since: datetime | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    _user: Annotated[User, Depends(require_superuser)] = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    """Proxy paginated PII events from the PII service."""
    params: dict[str, Any] = {
        "limit": limit,
        "offset": offset,
    }
    if operation:
        params["operation"] = operation
    if source:
        params["source"] = source
    if pii_only:
        params["pii_only"] = "true"
    if since:
        params["since"] = since.isoformat()

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"{_pii_url()}/v1/pii/events",
                params=params,
                timeout=10.0,
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPStatusError as exc:
        logger.warning("PII events request failed: %s", exc)
        raise HTTPException(
            status_code=exc.response.status_code,
            detail="PII service returned an error.",
        ) from exc
    except Exception as exc:
        logger.exception("Failed to reach PII service for events")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="PII service unreachable.",
        ) from exc
