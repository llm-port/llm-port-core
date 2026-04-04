"""Internal API endpoints consumed by the llm_port_api gateway."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_mcp.db.session import get_db_session
from llm_port_mcp.services.connection_manager import MCPConnectionManager
from llm_port_mcp.services.dao import MCPDao
from llm_port_mcp.services.privacy_proxy import PrivacyProxy
from llm_port_mcp.web.api.auth import verify_service_token

logger = logging.getLogger(__name__)
router = APIRouter(dependencies=[Depends(verify_service_token)])


# ── Schemas ───────────────────────────────────────────────────────────


class ToolCatalogEntry(BaseModel):
    qualified_name: str
    openai_tool: dict[str, Any]
    server_id: str
    pii_mode: str


class ToolCatalogResponse(BaseModel):
    catalog_version: int = 0
    tools: list[ToolCatalogEntry] = Field(default_factory=list)


class ToolCallRequest(BaseModel):
    qualified_name: str = Field(min_length=1)
    arguments: dict[str, Any] = Field(default_factory=dict)
    tenant_id: str = Field(default="default")
    request_id: str | None = None


class ToolCallResponse(BaseModel):
    qualified_name: str
    content: list[dict[str, Any]] = Field(default_factory=list)
    is_error: bool = False
    redaction_summary: dict[str, Any] | None = None


# ── Helpers ───────────────────────────────────────────────────────────


def _connection_manager(request: Request) -> MCPConnectionManager:
    return request.app.state.connection_manager  # type: ignore[no-any-return]


async def _get_catalog_version(request: Request, tenant_id: str) -> int:
    """Read the current catalog version from Redis (or 0)."""
    redis_pool = getattr(request.app.state, "redis_pool", None)
    if redis_pool is None:
        return 0
    try:
        from redis.asyncio import Redis

        async with Redis(connection_pool=redis_pool) as redis:
            val = await redis.get(f"mcp:tools:version:{tenant_id}")
            return int(val) if val else 0
    except Exception:
        return 0


def _get_privacy_proxy(request: Request) -> PrivacyProxy | None:
    """Build a PrivacyProxy if PII service is configured."""
    from llm_port_mcp.settings import settings

    if not settings.pii_service_url:
        return None
    http_client = request.app.state.http_client
    return PrivacyProxy(pii_base_url=settings.pii_service_url, http_client=http_client)


# ── Endpoints ─────────────────────────────────────────────────────────


@router.get("/tools/catalog", response_model=ToolCatalogResponse)
async def get_tool_catalog(
    request: Request,
    tenant_id: str = "default",
    session: AsyncSession = Depends(get_db_session),
) -> ToolCatalogResponse:
    """Return the full OpenAI-compatible tool catalog for a tenant.

    Called by the gateway to merge MCP tools into chat completion requests.
    """
    dao = MCPDao(session)
    tools = await dao.list_tools_by_tenant(tenant_id, enabled_only=True)
    version = await _get_catalog_version(request, tenant_id)

    entries = []
    for tool in tools:
        if tool.openai_schema_json is None:
            continue

        # Fetch server to get pii_mode
        server = await dao.get_server(tool.server_id)
        pii_mode = server.pii_mode if server else "redact"

        entries.append(
            ToolCatalogEntry(
                qualified_name=tool.qualified_name,
                openai_tool=tool.openai_schema_json,
                server_id=str(tool.server_id),
                pii_mode=pii_mode,
            ),
        )

    return ToolCatalogResponse(catalog_version=version, tools=entries)


@router.get("/tools/catalog/version")
async def get_catalog_version(
    request: Request,
    tenant_id: str = "default",
) -> dict[str, Any]:
    """Return only the current catalog version (lightweight check)."""
    version = await _get_catalog_version(request, tenant_id)
    return {"catalog_version": version, "tenant_id": tenant_id}


@router.post("/tools/call", response_model=ToolCallResponse)
async def call_tool(
    body: ToolCallRequest,
    request: Request,
    session: AsyncSession = Depends(get_db_session),
) -> ToolCallResponse:
    """Execute an MCP tool call with privacy proxy enforcement.

    Called by the gateway when the LLM requests a tool with ``mcp.*`` prefix.
    """
    dao = MCPDao(session)

    # Resolve tool
    tool = await dao.get_tool_by_qualified_name(
        tenant_id=body.tenant_id,
        qualified_name=body.qualified_name,
    )
    if tool is None:
        raise HTTPException(status_code=404, detail="Tool not found.")
    if not tool.enabled:
        raise HTTPException(status_code=403, detail="Tool is disabled.")

    # Get server and connection
    server = await dao.get_server(tool.server_id)
    if server is None:
        raise HTTPException(status_code=404, detail="Server not found.")
    if not server.enabled:
        raise HTTPException(status_code=403, detail="Server is disabled.")

    manager = _connection_manager(request)
    adapter = manager.get(str(server.id))
    if adapter is None:
        raise HTTPException(
            status_code=502,
            detail="Server not connected.",
        )

    # Privacy proxy
    sanitized_args = body.arguments
    redaction_summary = None
    proxy = _get_privacy_proxy(request)
    if proxy is not None:
        decision = await proxy.check(
            arguments=body.arguments,
            pii_mode=server.pii_mode,
        )
        if not decision.allowed:
            raise HTTPException(
                status_code=451,
                detail="Tool call blocked by privacy policy.",
            )
        sanitized_args = decision.sanitized_args
        if decision.redaction_summary:
            redaction_summary = decision.redaction_summary

    # Execute the tool call via the upstream MCP server
    try:
        result = await adapter.call_tool(tool.upstream_name, sanitized_args)
    except Exception as exc:
        logger.warning(
            "Tool call failed: %s on server %s: %s",
            body.qualified_name,
            server.name,
            exc,
        )
        return ToolCallResponse(
            qualified_name=body.qualified_name,
            content=[{"type": "text", "text": f"Tool execution error: {exc}"}],
            is_error=True,
            redaction_summary=redaction_summary,
        )

    return ToolCallResponse(
        qualified_name=body.qualified_name,
        content=result.get("content", []),
        is_error=result.get("isError", False),
        redaction_summary=redaction_summary,
    )


@router.get("/health")
async def internal_health() -> dict[str, str]:
    """Internal health check."""
    return {"status": "ok"}
