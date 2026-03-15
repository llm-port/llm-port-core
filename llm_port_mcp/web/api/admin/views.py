"""Admin API endpoints for MCP server and tool management."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_mcp.db.session import get_db_session
from llm_port_mcp.services.connection_manager import MCPConnectionManager
from llm_port_mcp.services.dao import MCPDao
from llm_port_mcp.services.discovery import discover_tools
from llm_port_mcp.web.api.admin.schemas import (
    RegisterServerRequest,
    ServerListResponse,
    ServerResponse,
    ToolResponse,
    UpdateServerRequest,
    UpdateToolRequest,
)
from llm_port_mcp.web.api.auth import AuthContext, get_auth_context

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Helpers ───────────────────────────────────────────────────────────


def _connection_manager(request: Request) -> MCPConnectionManager:
    return request.app.state.connection_manager  # type: ignore[no-any-return]


def _tool_to_response(tool: Any) -> ToolResponse:
    return ToolResponse(
        id=str(tool.id),
        server_id=str(tool.server_id),
        qualified_name=tool.qualified_name,
        upstream_name=tool.upstream_name,
        display_name=tool.display_name,
        description=tool.description,
        enabled=tool.enabled,
        version=tool.version,
        schema_hash=tool.schema_hash,
        input_schema=tool.raw_schema_json,
        openai_schema=tool.openai_schema_json,
        last_seen_at=tool.last_seen_at,
    )


def _server_to_response(
    server: Any,
    *,
    warnings: list[str] | None = None,
) -> ServerResponse:
    tools = [_tool_to_response(t) for t in (server.tools or [])]
    return ServerResponse(
        id=str(server.id),
        name=server.name,
        transport=server.transport,
        status=server.status,
        url=server.url,
        command=server.command_json,
        args=server.args_json,
        working_dir=server.working_dir,
        tool_prefix=server.tool_prefix,
        pii_mode=server.pii_mode,
        enabled=server.enabled,
        timeout_sec=server.timeout_sec,
        heartbeat_interval_sec=server.heartbeat_interval_sec,
        tenant_id=server.tenant_id,
        discovered_tools=len(tools),
        has_settings=server.settings_schema_json is not None,
        created_at=server.created_at,
        updated_at=server.updated_at,
        last_discovery_at=server.last_discovery_at,
        tools=tools,
        warnings=warnings or [],
    )


async def _increment_catalog_version(
    request: Request,
    tenant_id: str,
) -> None:
    """Increment the tool catalog version in Redis."""
    redis_pool = getattr(request.app.state, "redis_pool", None)
    if redis_pool is None:
        return
    try:
        from redis.asyncio import Redis

        async with Redis(connection_pool=redis_pool) as redis:
            await redis.incr(f"mcp:tools:version:{tenant_id}")
            await redis.publish("mcp:pubsub:tool_reload", tenant_id)
    except Exception:
        logger.debug("Failed to increment catalog version in Redis", exc_info=True)


# ── Endpoints ─────────────────────────────────────────────────────────


@router.post("/servers", response_model=ServerResponse, status_code=201)
async def register_server(
    body: RegisterServerRequest,
    request: Request,
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db_session),
) -> ServerResponse:
    """Register a new MCP server and optionally discover its tools."""
    # Validate transport-specific requirements
    if body.transport == "stdio" and not body.command:
        raise HTTPException(
            status_code=422,
            detail="stdio transport requires 'command'.",
        )
    if body.transport == "sse" and not body.url:
        raise HTTPException(
            status_code=422,
            detail="SSE transport requires 'url'.",
        )
    if body.transport == "streamable_http" and not body.url:
        raise HTTPException(
            status_code=422,
            detail="Streamable HTTP transport requires 'url'.",
        )

    dao = MCPDao(session)

    # Check name uniqueness within tenant
    existing = await dao.list_servers(tenant_id=body.tenant_id)
    if any(s.name == body.name for s in existing):
        raise HTTPException(
            status_code=409,
            detail=f"Server name '{body.name}' already exists for tenant.",
        )
    if any(s.tool_prefix == body.tool_prefix for s in existing):
        raise HTTPException(
            status_code=409,
            detail=f"Tool prefix '{body.tool_prefix}' already exists for tenant.",
        )

    server = await dao.create_server(
        tenant_id=body.tenant_id,
        name=body.name,
        transport=body.transport,
        url=body.url,
        command_json=body.command,
        args_json=body.args,
        working_dir=body.working_dir,
        headers_json_encrypted=body.headers or None,
        env_json_encrypted=body.env or None,
        tool_prefix=body.tool_prefix,
        pii_mode=body.pii_mode,
        enabled=body.enabled,
        status="registering",
        timeout_sec=body.timeout_sec,
        heartbeat_interval_sec=body.heartbeat_interval_sec,
    )
    await session.commit()

    warnings: list[str] = []
    manager = _connection_manager(request)

    # Connect and optionally discover
    if body.enabled:
        try:
            # Reload from DB to get fully populated model
            server = await dao.get_server(server.id)
            await manager.start(server)  # type: ignore[arg-type]

            if body.auto_discover:
                adapter = manager.get(str(server.id))  # type: ignore[arg-type]
                if adapter is not None:
                    await discover_tools(
                        server_id=server.id,  # type: ignore[arg-type]
                        tenant_id=body.tenant_id,
                        adapter=adapter,
                        session=session,
                    )
                    await _increment_catalog_version(request, body.tenant_id)
            # Try to fetch settings schema from remote server
            if body.url:
                try:
                    schema = await _fetch_from_server(body.url, "/schema")
                    await dao.update_server(
                        server.id, settings_schema_json=schema,  # type: ignore[arg-type]
                    )
                    await session.commit()
                except Exception:
                    logger.debug("No settings schema from %s", body.url)
        except Exception as exc:
            logger.warning(
                "Failed to connect/discover server %s: %s",
                server.name,  # type: ignore[union-attr]
                exc,
            )
            warnings.append(f"Connection/discovery failed: {exc}")
            await dao.update_server_status(server.id, "error")  # type: ignore[arg-type]
            await session.commit()

    # Reload to get tools
    server = await dao.get_server(server.id)  # type: ignore[arg-type]
    return _server_to_response(server, warnings=warnings)


@router.get("/servers", response_model=ServerListResponse)
async def list_servers(
    request: Request,
    transport: str | None = None,
    enabled: bool | None = None,
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db_session),
) -> ServerListResponse:
    """List registered MCP servers."""
    dao = MCPDao(session)
    servers = await dao.list_servers(
        tenant_id=auth.tenant_id,
        transport=transport,
        enabled=enabled,
    )
    items = [_server_to_response(s) for s in servers]
    return ServerListResponse(servers=items, total=len(items))


@router.get("/servers/{server_id}", response_model=ServerResponse)
async def get_server(
    server_id: uuid.UUID,
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db_session),
) -> ServerResponse:
    """Get a single MCP server with its tools."""
    dao = MCPDao(session)
    server = await dao.get_server(server_id)
    if server is None:
        raise HTTPException(status_code=404, detail="Server not found.")
    return _server_to_response(server)


@router.patch("/servers/{server_id}", response_model=ServerResponse)
async def update_server(
    server_id: uuid.UUID,
    body: UpdateServerRequest,
    request: Request,
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db_session),
) -> ServerResponse:
    """Update an MCP server configuration."""
    dao = MCPDao(session)
    server = await dao.get_server(server_id)
    if server is None:
        raise HTTPException(status_code=404, detail="Server not found.")

    update_data: dict[str, Any] = {}
    for field_name in (
        "name",
        "url",
        "working_dir",
        "tool_prefix",
        "pii_mode",
        "timeout_sec",
        "heartbeat_interval_sec",
        "enabled",
    ):
        value = getattr(body, field_name, None)
        if value is not None:
            update_data[field_name] = value
    if body.command is not None:
        update_data["command_json"] = body.command
    if body.args is not None:
        update_data["args_json"] = body.args
    if body.headers is not None:
        update_data["headers_json_encrypted"] = body.headers
    if body.env is not None:
        update_data["env_json_encrypted"] = body.env

    if update_data:
        server = await dao.update_server(server_id, **update_data)
        await session.commit()

    # Reconnect if needed
    manager = _connection_manager(request)
    if body.enabled is False:
        await manager.stop(str(server_id))
        await dao.update_server_status(server_id, "disabled")
        await session.commit()
    elif body.enabled is True or update_data:
        server = await dao.get_server(server_id)
        if server and server.enabled:
            try:
                await manager.restart(server)
            except Exception:
                logger.warning("Reconnect failed for %s", server_id, exc_info=True)

    await _increment_catalog_version(request, server.tenant_id)  # type: ignore[union-attr]
    server = await dao.get_server(server_id)
    return _server_to_response(server)


@router.delete("/servers/{server_id}", status_code=204)
async def delete_server(
    server_id: uuid.UUID,
    request: Request,
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    """Deregister an MCP server and disconnect it."""
    dao = MCPDao(session)
    server = await dao.get_server(server_id)
    if server is None:
        raise HTTPException(status_code=404, detail="Server not found.")

    tenant_id = server.tenant_id
    manager = _connection_manager(request)
    await manager.stop(str(server_id))
    await dao.delete_server(server_id)
    await session.commit()
    await _increment_catalog_version(request, tenant_id)


@router.post("/servers/{server_id}/refresh", response_model=ServerResponse)
async def refresh_server(
    server_id: uuid.UUID,
    request: Request,
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db_session),
) -> ServerResponse:
    """Re-run tool discovery for a server."""
    dao = MCPDao(session)
    server = await dao.get_server(server_id)
    if server is None:
        raise HTTPException(status_code=404, detail="Server not found.")

    manager = _connection_manager(request)
    adapter = manager.get(str(server_id))
    if adapter is None:
        # Try to connect first
        try:
            adapter = await manager.start(server)
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Cannot connect to server: {exc}",
            ) from exc

    await discover_tools(
        server_id=server.id,
        tenant_id=server.tenant_id,
        adapter=adapter,
        session=session,
    )
    await _increment_catalog_version(request, server.tenant_id)

    # Refresh settings schema from remote
    if server.url:
        try:
            schema = await _fetch_from_server(server.url, "/schema")
            await dao.update_server(server_id, settings_schema_json=schema)
            await session.commit()
        except Exception:
            logger.debug("No settings schema from %s", server.url)

    server = await dao.get_server(server_id)
    return _server_to_response(server)


@router.post("/servers/{server_id}/test")
async def test_server(
    server_id: uuid.UUID,
    request: Request,
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Test connectivity to a registered MCP server."""
    dao = MCPDao(session)
    server = await dao.get_server(server_id)
    if server is None:
        raise HTTPException(status_code=404, detail="Server not found.")

    manager = _connection_manager(request)
    adapter = manager.get(str(server_id))

    if adapter is None:
        try:
            adapter = await manager.start(server)
        except Exception as exc:
            return {
                "status": "error",
                "message": f"Connection failed: {exc}",
                "tools_found": 0,
            }

    try:
        status = await adapter.healthcheck()
        tools = await adapter.list_tools()
        return {
            "status": status,
            "message": "Server is reachable.",
            "tools_found": len(tools),
            "tool_names": [t.upstream_name for t in tools],
        }
    except Exception as exc:
        return {
            "status": "error",
            "message": f"Health check failed: {exc}",
            "tools_found": 0,
        }


@router.get("/servers/{server_id}/tools", response_model=list[ToolResponse])
async def list_server_tools(
    server_id: uuid.UUID,
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db_session),
) -> list[ToolResponse]:
    """List tools belonging to a specific server."""
    dao = MCPDao(session)
    server = await dao.get_server(server_id)
    if server is None:
        raise HTTPException(status_code=404, detail="Server not found.")

    tools = await dao.list_tools_by_server(server_id)
    return [_tool_to_response(t) for t in tools]


@router.patch("/tools/{tool_id}", response_model=ToolResponse)
async def update_tool(
    tool_id: uuid.UUID,
    body: UpdateToolRequest,
    request: Request,
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db_session),
) -> ToolResponse:
    """Update a tool (enable/disable, rename)."""
    dao = MCPDao(session)
    tool = await dao.get_tool(tool_id)
    if tool is None:
        raise HTTPException(status_code=404, detail="Tool not found.")

    update_data: dict[str, Any] = {}
    if body.enabled is not None:
        update_data["enabled"] = body.enabled
    if body.display_name is not None:
        update_data["display_name"] = body.display_name

    if update_data:
        tool = await dao.update_tool(tool_id, **update_data)
        await session.commit()
        await _increment_catalog_version(request, tool.tenant_id)  # type: ignore[union-attr]

    return _tool_to_response(tool)


# ── Provider-settings proxy endpoints ────────────────────────────────


async def _fetch_from_server(
    server_url: str,
    path: str,
    *,
    method: str = "GET",
    json_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Make an HTTP request to a remote MCP server's REST API."""
    import httpx

    base = server_url.rstrip("/")
    url = f"{base}/api/settings{path}"
    async with httpx.AsyncClient(timeout=10.0) as client:
        if method == "PUT":
            resp = await client.put(url, json=json_body)
        else:
            resp = await client.get(url)
    resp.raise_for_status()
    return resp.json()


@router.get("/servers/{server_id}/settings/schema")
async def get_server_settings_schema(
    server_id: uuid.UUID,
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Return the provider settings JSON Schema for a server.

    Serves cached schema from DB if available, otherwise fetches from
    the remote MCP server and caches it.
    """
    dao = MCPDao(session)
    server = await dao.get_server(server_id)
    if server is None:
        raise HTTPException(status_code=404, detail="Server not found.")

    if server.settings_schema_json is not None:
        return server.settings_schema_json

    # Fetch from remote
    if not server.url:
        raise HTTPException(
            status_code=400,
            detail="Server has no URL — settings not available for stdio servers.",
        )
    try:
        schema = await _fetch_from_server(server.url, "/schema")
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to fetch settings schema: {exc}",
        ) from exc

    await dao.update_server(server_id, settings_schema_json=schema)
    await session.commit()
    return schema


@router.get("/servers/{server_id}/settings")
async def get_server_settings(
    server_id: uuid.UUID,
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Return current provider settings values (proxied from remote server)."""
    dao = MCPDao(session)
    server = await dao.get_server(server_id)
    if server is None:
        raise HTTPException(status_code=404, detail="Server not found.")
    if not server.url:
        raise HTTPException(
            status_code=400,
            detail="Server has no URL — settings not available for stdio servers.",
        )

    try:
        values = await _fetch_from_server(server.url, "")
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to fetch settings: {exc}",
        ) from exc

    # Cache in DB
    await dao.update_server(server_id, provider_settings_json=values)
    await session.commit()
    return values


@router.put("/servers/{server_id}/settings")
async def update_server_settings(
    server_id: uuid.UUID,
    body: dict[str, Any],
    auth: AuthContext = Depends(get_auth_context),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Update provider settings on the remote MCP server."""
    dao = MCPDao(session)
    server = await dao.get_server(server_id)
    if server is None:
        raise HTTPException(status_code=404, detail="Server not found.")
    if not server.url:
        raise HTTPException(
            status_code=400,
            detail="Server has no URL — settings not available for stdio servers.",
        )

    try:
        updated = await _fetch_from_server(
            server.url, "", method="PUT", json_body=body,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Failed to update settings: {exc}",
        ) from exc

    # Cache updated values
    await dao.update_server(server_id, provider_settings_json=updated)
    await session.commit()
    return updated
