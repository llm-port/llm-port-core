"""HTTP client for llm_port_mcp admin APIs."""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import HTTPException
from starlette import status

from llm_port_backend.settings import settings


class MCPServiceClient:
    """Typed proxy for the MCP micro-service admin endpoints."""

    def __init__(
        self,
        base_url: str,
        service_token: str,
        timeout_sec: float = 30.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.service_token = service_token
        self.timeout_sec = timeout_sec

    async def _request(
        self,
        method: str,
        path: str,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        if not self.service_token:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="MCP integration is not configured (missing service token).",
            )

        headers = {
            "Authorization": f"Bearer {self.service_token}",
            "Content-Type": "application/json",
        }
        url = f"{self.base_url}{path}"
        async with httpx.AsyncClient(timeout=self.timeout_sec) as client:
            try:
                response = await client.request(
                    method=method,
                    url=url,
                    json=json_body,
                    params=params,
                    headers=headers,
                )
            except httpx.TimeoutException as exc:
                raise HTTPException(
                    status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                    detail=f"MCP service timed out calling {path}.",
                ) from exc
            except httpx.HTTPError as exc:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"Failed to reach MCP service: {exc}",
                ) from exc

        if response.status_code >= 400:
            detail = response.text
            try:
                payload = response.json()
                if isinstance(payload, dict) and "detail" in payload:
                    detail = str(payload["detail"])
            except ValueError:
                pass
            raise HTTPException(status_code=response.status_code, detail=detail)

        if response.status_code == status.HTTP_204_NO_CONTENT:
            return None
        return response.json()

    # ── Server CRUD ──────────────────────────────────────────────────────

    async def list_servers(self) -> list[dict[str, Any]]:
        return await self._request("GET", "/api/admin/servers")

    async def get_server(self, server_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/api/admin/servers/{server_id}")

    async def register_server(self, payload: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/api/admin/servers", json_body=payload)

    async def update_server(
        self, server_id: str, payload: dict[str, Any],
    ) -> dict[str, Any]:
        return await self._request(
            "PATCH", f"/api/admin/servers/{server_id}", json_body=payload,
        )

    async def delete_server(self, server_id: str) -> None:
        await self._request("DELETE", f"/api/admin/servers/{server_id}")

    async def refresh_server(self, server_id: str) -> dict[str, Any]:
        return await self._request("POST", f"/api/admin/servers/{server_id}/refresh")

    async def test_server(self, server_id: str) -> dict[str, Any]:
        return await self._request("POST", f"/api/admin/servers/{server_id}/test")

    # ── Tools ────────────────────────────────────────────────────────────

    async def list_server_tools(self, server_id: str) -> list[dict[str, Any]]:
        return await self._request("GET", f"/api/admin/servers/{server_id}/tools")

    async def update_tool(
        self, tool_id: str, payload: dict[str, Any],
    ) -> dict[str, Any]:
        return await self._request(
            "PATCH", f"/api/admin/tools/{tool_id}", json_body=payload,
        )

    # ── Health ───────────────────────────────────────────────────────────

    async def health(self) -> dict[str, Any]:
        return await self._request("GET", "/api/health")


def get_mcp_client() -> MCPServiceClient:
    """Factory — raises 503 when the MCP module is disabled."""
    if not settings.mcp_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MCP module is not enabled.",
        )
    return MCPServiceClient(
        base_url=settings.mcp_service_url,
        service_token=settings.mcp_service_token,
    )
