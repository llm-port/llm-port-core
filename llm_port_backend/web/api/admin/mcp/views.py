"""Admin MCP proxy routes.

Proxies MCP server management requests to the MCP micro-service.
All endpoints require superuser privileges.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Annotated, Any
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Body, Depends, HTTPException, Path
from pydantic import BaseModel, Field

from llm_port_backend.db.models.users import User
from llm_port_backend.services.docker.client import DockerService
from llm_port_backend.services.mcp.client import MCPServiceClient, get_mcp_client
from llm_port_backend.web.api.admin.dependencies import get_docker, require_superuser

logger = logging.getLogger(__name__)
router = APIRouter()

# Name of the MCP service container — used to discover its network.
_MCP_SERVICE_CONTAINER = "llm-port-mcp"


async def _ensure_container_network(
    docker: DockerService,
    url: str,
) -> None:
    """Connect the container referenced by *url* to the MCP service's network.

    Parses the hostname from the URL, looks up whether a Docker container
    with that name exists, and — if so — connects it to the same network
    as the ``llm-port-mcp`` container so they can communicate via DNS.
    This is a best-effort operation: failures are logged but never raised.
    """
    try:
        hostname = urlparse(url).hostname
        if not hostname or "." in hostname:
            # Not a bare container name (e.g. an IP or FQDN) — skip.
            return

        target = await docker.find_container_by_name(hostname)
        if target is None:
            logger.debug(
                "MCP network auto-join: container %r not found — skipping",
                hostname,
            )
            return

        mcp_container = await docker.find_container_by_name(_MCP_SERVICE_CONTAINER)
        if mcp_container is None:
            logger.debug("MCP network auto-join: %s container not found", _MCP_SERVICE_CONTAINER)
            return

        mcp_networks = (
            mcp_container.get("NetworkSettings", {}).get("Networks", {})
        )
        if not mcp_networks:
            return

        # Pick the first network the MCP container is on
        network_name = next(iter(mcp_networks))

        # Check if the target is already on that network
        target_networks = (
            target.get("NetworkSettings", {}).get("Networks", {})
        )
        if network_name in target_networks:
            logger.debug(
                "MCP network auto-join: %s already on %s",
                hostname,
                network_name,
            )
            return

        # Find the network ID
        all_nets = await docker.list_networks()
        net_id: str | None = None
        for n in all_nets:
            if n.get("Name") == network_name:
                net_id = n.get("Id")
                break

        if not net_id:
            logger.warning("MCP network auto-join: network %r not found", network_name)
            return

        target_id = target.get("Id", "")
        await docker.connect_container_to_network(net_id, target_id)
        logger.info(
            "MCP network auto-join: connected %s to %s",
            hostname,
            network_name,
        )
    except Exception:
        logger.warning("MCP network auto-join failed for %s", url, exc_info=True)


# ── Servers ──────────────────────────────────────────────────────────────────


@router.get("/servers")
async def list_servers(
    _user: Annotated[User, Depends(require_superuser)],
    client: Annotated[MCPServiceClient, Depends(get_mcp_client)],
) -> list[dict[str, Any]]:
    data = await client.list_servers()
    # MCP service returns {"servers": [...], "total": N}
    if isinstance(data, dict):
        return data.get("servers", [])
    return data


@router.post("/servers", status_code=201)
async def register_server(
    _user: Annotated[User, Depends(require_superuser)],
    client: Annotated[MCPServiceClient, Depends(get_mcp_client)],
    docker: Annotated[DockerService, Depends(get_docker)],
    payload: Annotated[dict[str, Any], Body()],
) -> dict[str, Any]:
    # Auto-join the MCP server's container to our Docker network
    # BEFORE registration, because the MCP service connects immediately.
    url = payload.get("url", "")
    if url:
        await _ensure_container_network(docker, url)
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
    docker: Annotated[DockerService, Depends(get_docker)],
    payload: Annotated[dict[str, Any], Body()],
) -> dict[str, Any]:
    url = payload.get("url", "")
    if url:
        await _ensure_container_network(docker, url)
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


# ── Provider Settings ────────────────────────────────────────────────────────


@router.get("/servers/{server_id}/settings/schema")
async def get_server_settings_schema(
    server_id: Annotated[str, Path()],
    _user: Annotated[User, Depends(require_superuser)],
    client: Annotated[MCPServiceClient, Depends(get_mcp_client)],
) -> dict[str, Any]:
    return await client.get_settings_schema(server_id)


@router.get("/servers/{server_id}/settings")
async def get_server_settings(
    server_id: Annotated[str, Path()],
    _user: Annotated[User, Depends(require_superuser)],
    client: Annotated[MCPServiceClient, Depends(get_mcp_client)],
) -> dict[str, Any]:
    return await client.get_settings(server_id)


@router.put("/servers/{server_id}/settings")
async def update_server_settings(
    server_id: Annotated[str, Path()],
    _user: Annotated[User, Depends(require_superuser)],
    client: Annotated[MCPServiceClient, Depends(get_mcp_client)],
    payload: Annotated[dict[str, Any], Body()],
) -> dict[str, Any]:
    return await client.update_settings(server_id, payload)


# ── Network scanner ──────────────────────────────────────────────────────────


class ScanRequest(BaseModel):
    host: str = Field(min_length=1, max_length=256)
    port_start: int = Field(default=8000, ge=1, le=65535)
    port_end: int = Field(default=9000, ge=1, le=65535)


def _normalize_scan_host(host: str) -> str:
    normalized = host.strip()
    normalized = normalized.replace("host.docker.internel", "host.docker.internal")
    if normalized.startswith(("http://", "https://")):
        normalized = urlparse(normalized).hostname or normalized
    if "/" in normalized:
        normalized = normalized.split("/", 1)[0]
    return normalized


class DiscoveredServer(BaseModel):
    host: str
    port: int
    url: str
    server_name: str
    protocol_version: str | None = None
    tools: list[str] = Field(default_factory=list)
    already_registered: bool = False


class ScanResponse(BaseModel):
    discovered: list[DiscoveredServer]
    scanned_ports: int


def _parse_jsonrpc_response(resp: httpx.Response) -> dict[str, Any] | None:
    """Parse a JSON-RPC response that may be plain JSON or SSE-wrapped."""
    content_type = resp.headers.get("content-type", "")
    if "text/event-stream" in content_type:
        # SSE format: lines like "event: message\ndata: {...}\n\n"
        for line in resp.text.splitlines():
            if line.startswith("data: "):
                return json.loads(line[6:])
        return None
    return resp.json()


async def _probe_mcp_port(
    host: str,
    port: int,
    *,
    timeout: float = 3.0,
) -> DiscoveredServer | None:
    """Try to handshake with an MCP server at host:port/mcp/."""
    url = f"http://{host}:{port}/mcp/"
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "llm-port-scanner", "version": "1.0"},
        },
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code != 200:
                return None

            body = _parse_jsonrpc_response(resp)
            if not body:
                return None
            result = body.get("result", {})
            server_info = result.get("serverInfo", {})
            server_name = server_info.get("name", f"unknown-{port}")
            protocol_version = result.get("protocolVersion")

            # Grab session id for tools/list call
            session_id = resp.headers.get("mcp-session-id")

            # Try to list tools (reuse same client & session)
            tools = await _probe_list_tools(
                client, url, session_id=session_id, timeout=timeout,
            )

        return DiscoveredServer(
            host=host,
            port=port,
            url=url,
            server_name=server_name,
            protocol_version=protocol_version,
            tools=tools,
        )
    except Exception:
        return None


async def _probe_list_tools(
    client: httpx.AsyncClient,
    url: str,
    *,
    session_id: str | None = None,
    timeout: float = 3.0,
) -> list[str]:
    """Try to list tools from a discovered MCP server."""
    payload = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/list",
        "params": {},
    }
    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    try:
        resp = await client.post(url, json=payload, headers=headers)
        if resp.status_code != 200:
            return []
        body = _parse_jsonrpc_response(resp)
        if not body:
            return []
        result = body.get("result", {})
        return [t.get("name", "") for t in result.get("tools", [])]
    except Exception:
        return []


@router.post("/scan", response_model=ScanResponse)
async def scan_for_servers(
    _user: Annotated[User, Depends(require_superuser)],
    body: ScanRequest,
    client: Annotated[MCPServiceClient, Depends(get_mcp_client)],
) -> ScanResponse:
    """Scan a host for running MCP servers on a port range."""
    host = _normalize_scan_host(body.host)

    if body.port_end < body.port_start:
        raise HTTPException(status_code=422, detail="port_end must be >= port_start.")
    port_range = body.port_end - body.port_start + 1
    if port_range > 1000:
        raise HTTPException(status_code=422, detail="Port range must not exceed 1000 ports.")

    # Get already-registered server URLs to flag duplicates
    registered_urls: set[str] = set()
    try:
        data = await client.list_servers()
        servers_list = data.get("servers", []) if isinstance(data, dict) else data
        for s in servers_list:
            if isinstance(s, dict) and s.get("url"):
                registered_urls.add(s["url"])
    except Exception:
        logger.debug("Could not fetch registered servers for duplicate check", exc_info=True)

    # Probe all ports concurrently with bounded parallelism
    sem = asyncio.Semaphore(50)

    async def probe(port: int) -> DiscoveredServer | None:
        async with sem:
            return await _probe_mcp_port(host, port)

    tasks = [probe(p) for p in range(body.port_start, body.port_end + 1)]
    results = await asyncio.gather(*tasks)

    discovered: list[DiscoveredServer] = []
    for result in results:
        if result is not None:
            result.already_registered = result.url in registered_urls
            discovered.append(result)

    return ScanResponse(discovered=discovered, scanned_ports=port_range)
