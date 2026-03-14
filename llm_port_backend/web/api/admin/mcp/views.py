"""Admin MCP proxy routes.

Proxies MCP server management requests to the MCP micro-service.
All endpoints require superuser privileges.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Path

from llm_port_backend.db.models.users import User
from llm_port_backend.services.mcp.client import MCPServiceClient, get_mcp_client
from llm_port_backend.web.api.admin.dependencies import require_superuser

router = APIRouter()


# ── Servers ──────────────────────────────────────────────────────────────────


@router.get("/servers")
async def list_servers(
    _user: Annotated[User, Depends(require_superuser)],
    client: Annotated[MCPServiceClient, Depends(get_mcp_client)],
) -> list[dict[str, Any]]:
    return await client.list_servers()


@router.post("/servers", status_code=201)
async def register_server(
    _user: Annotated[User, Depends(require_superuser)],
    client: Annotated[MCPServiceClient, Depends(get_mcp_client)],
    payload: Annotated[dict[str, Any], Body()],
) -> dict[str, Any]:
    return await client.register_server(payload)


@router.get("/servers/{server_id}")
async def get_server(
    server_id: Annotated[str, Path()],
    _user: Annotated[User, Depends(require_superuser)],
    client: Annotated[MCPServiceClient, Depends(get_mcp_client)],
) -> dict[str, Any]:
    return await client.get_server(server_id)


@router.patch("/servers/{server_id}")
async def update_server(
    server_id: Annotated[str, Path()],
    _user: Annotated[User, Depends(require_superuser)],
    client: Annotated[MCPServiceClient, Depends(get_mcp_client)],
    payload: Annotated[dict[str, Any], Body()],
) -> dict[str, Any]:
    return await client.update_server(server_id, payload)


@router.delete("/servers/{server_id}", status_code=204)
async def delete_server(
    server_id: Annotated[str, Path()],
    _user: Annotated[User, Depends(require_superuser)],
    client: Annotated[MCPServiceClient, Depends(get_mcp_client)],
) -> None:
    await client.delete_server(server_id)


@router.post("/servers/{server_id}/refresh")
async def refresh_server(
    server_id: Annotated[str, Path()],
    _user: Annotated[User, Depends(require_superuser)],
    client: Annotated[MCPServiceClient, Depends(get_mcp_client)],
) -> dict[str, Any]:
    return await client.refresh_server(server_id)


@router.post("/servers/{server_id}/test")
async def test_server(
    server_id: Annotated[str, Path()],
    _user: Annotated[User, Depends(require_superuser)],
    client: Annotated[MCPServiceClient, Depends(get_mcp_client)],
) -> dict[str, Any]:
    return await client.test_server(server_id)


# ── Tools ────────────────────────────────────────────────────────────────────


@router.get("/servers/{server_id}/tools")
async def list_server_tools(
    server_id: Annotated[str, Path()],
    _user: Annotated[User, Depends(require_superuser)],
    client: Annotated[MCPServiceClient, Depends(get_mcp_client)],
) -> list[dict[str, Any]]:
    return await client.list_server_tools(server_id)


@router.patch("/tools/{tool_id}")
async def update_tool(
    tool_id: Annotated[str, Path()],
    _user: Annotated[User, Depends(require_superuser)],
    client: Annotated[MCPServiceClient, Depends(get_mcp_client)],
    payload: Annotated[dict[str, Any], Body()],
) -> dict[str, Any]:
    return await client.update_tool(tool_id, payload)
