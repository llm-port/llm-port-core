"""HTTP client for backend onboarding and credential endpoints."""

from __future__ import annotations

from typing import Any

import httpx

from llm_port_node_agent.config import AgentConfig


class BackendClient:
    """Thin REST client used outside the websocket stream."""

    def __init__(self, config: AgentConfig) -> None:
        self._config = config
        self._client = httpx.AsyncClient(
            base_url=config.backend_url,
            timeout=config.request_timeout_sec,
            verify=config.verify_tls,
        )

    async def close(self) -> None:
        """Close underlying HTTP client."""
        await self._client.aclose()

    async def enroll(
        self,
        *,
        enrollment_token: str,
        agent_id: str,
        host: str,
        capabilities: dict[str, Any],
        version: str,
    ) -> dict[str, Any]:
        """Exchange one-time enrollment token for node credential."""
        res = await self._client.post(
            "/api/admin/system/nodes/enroll",
            json={
                "enrollment_token": enrollment_token,
                "agent_id": agent_id,
                "host": host,
                "capabilities": capabilities,
                "version": version,
            },
        )
        res.raise_for_status()
        payload = res.json()
        if not isinstance(payload, dict):
            raise RuntimeError("Invalid enroll response payload.")
        return payload

    async def rotate_credential(self, *, credential: str) -> dict[str, Any]:
        """Rotate active credential using bearer auth."""
        res = await self._client.post(
            "/api/admin/system/nodes/credentials/rotate",
            headers={"Authorization": f"Bearer {credential}"},
        )
        res.raise_for_status()
        payload = res.json()
        if not isinstance(payload, dict):
            raise RuntimeError("Invalid rotate response payload.")
        return payload
