"""Admin services manifest endpoint.

Returns the list of optional modules and their current status so the
frontend can show / hide UI sections dynamically.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from llm_port_backend.settings import settings

logger = logging.getLogger(__name__)

router = APIRouter()

# ── Module definitions ────────────────────────────────────────────────
# Each entry describes an optional module the backend knows about.
# Adding a new module = append one dict here + add a settings flag.

_MODULE_DEFS: list[dict[str, Any]] = [
    {
        "name": "rag",
        "display_name": "RAG Engine",
        "description": (
            "Retrieval-Augmented Generation pipeline with document ingestion, "
            "chunking, embedding, and vector search."
        ),
        "settings_flag": "rag_enabled",
        "health_url_fn": lambda: f"{settings.rag_base_url}/health",
    },
    # Future modules (pii, auth) are managed by the API gateway and
    # reflected via its own /v1/services endpoint.  Only modules
    # consumed directly by the backend are listed here.
]


async def _probe_health(url: str) -> str:
    """Return ``"healthy"`` or ``"unhealthy"`` for a single URL."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url)
            return "healthy" if resp.status_code < 400 else "unhealthy"
    except Exception:
        logger.debug("Health check failed for %s", url, exc_info=True)
        return "unhealthy"


@router.get("/services")
async def list_services(request: Request) -> JSONResponse:
    """Return the manifest of optional backend modules.

    The frontend uses this to discover which features are available so
    it can show / hide navigation items and page sections dynamically.
    """
    result: list[dict[str, Any]] = []

    for mod in _MODULE_DEFS:
        enabled: bool = getattr(settings, mod["settings_flag"], False)
        status_val = "disabled"

        if enabled:
            health_url = mod["health_url_fn"]()
            status_val = await _probe_health(health_url)

        result.append(
            {
                "name": mod["name"],
                "display_name": mod["display_name"],
                "description": mod["description"],
                "enabled": enabled,
                "status": status_val,
            }
        )

    return JSONResponse(status_code=200, content={"services": result})
