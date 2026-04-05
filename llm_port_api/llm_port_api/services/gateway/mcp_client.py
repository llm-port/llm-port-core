"""HTTP client for the MCP micro-service.

Used by the gateway pipeline to fetch the MCP tool catalog and
execute tool calls via the MCP service's internal API.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ToolCatalog:
    """Snapshot of the MCP tool catalog for a tenant."""

    version: int
    tools: list[dict[str, Any]]


@dataclass(slots=True)
class ToolCallResult:
    """Result of executing an MCP tool call."""

    content: str
    is_error: bool = False
    redaction_summary: dict[str, Any] | None = None


class MCPClient:
    """Async client wrapping MCP service internal HTTP endpoints."""

    def __init__(
        self,
        *,
        base_url: str,
        http_client: httpx.AsyncClient,
        service_token: str,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._http = http_client
        self._headers = {"Authorization": f"Bearer {service_token}"}

    async def get_tool_catalog(self, tenant_id: str) -> ToolCatalog:
        """Fetch the full tool catalog for a tenant.

        Calls ``GET /api/internal/tools/catalog?tenant_id=<tid>``.
        """
        try:
            resp = await self._http.get(
                f"{self._base}/api/internal/tools/catalog",
                params={"tenant_id": tenant_id},
                headers=self._headers,
                timeout=10.0,
            )
            resp.raise_for_status()
            data = resp.json()
            return ToolCatalog(
                version=data.get("version", 0),
                tools=data.get("tools", []),
            )
        except Exception:
            logger.exception("MCP catalog fetch failed")
            return ToolCatalog(version=0, tools=[])

    async def get_catalog_version(self, tenant_id: str) -> int:
        """Fetch just the catalog version for cache validation.

        Calls ``GET /api/internal/tools/catalog/version?tenant_id=<tid>``.
        """
        try:
            resp = await self._http.get(
                f"{self._base}/api/internal/tools/catalog/version",
                params={"tenant_id": tenant_id},
                headers=self._headers,
                timeout=5.0,
            )
            resp.raise_for_status()
            return resp.json().get("version", 0)
        except Exception:
            logger.debug("MCP catalog version check failed", exc_info=True)
            return 0

    async def call_tool(
        self,
        *,
        qualified_name: str,
        arguments: dict[str, Any],
        tenant_id: str,
        request_id: str,
        pii_mode_override: str | None = None,
    ) -> ToolCallResult:
        """Execute an MCP tool call via the MCP service.

        Calls ``POST /api/internal/tools/call``.
        """
        body: dict[str, Any] = {
            "qualified_name": qualified_name,
            "arguments": arguments,
            "tenant_id": tenant_id,
            "request_id": request_id,
        }
        if pii_mode_override is not None:
            body["pii_mode_override"] = pii_mode_override
        try:
            resp = await self._http.post(
                f"{self._base}/api/internal/tools/call",
                json=body,
                headers=self._headers,
                timeout=30.0,
            )
            resp.raise_for_status()
            data = resp.json()
            return ToolCallResult(
                content=str(data.get("content", "")),
                is_error=data.get("is_error", False),
                redaction_summary=data.get("redaction_summary"),
            )
        except httpx.HTTPStatusError as exc:
            logger.warning(
                "MCP tool call failed: %s %s",
                qualified_name,
                exc.response.status_code,
            )
            return ToolCallResult(
                content=f"MCP tool call failed: {exc.response.status_code}",
                is_error=True,
            )
        except Exception:
            logger.exception("MCP tool call failed: %s", qualified_name)
            return ToolCallResult(
                content="MCP tool call failed: service unavailable",
                is_error=True,
            )
