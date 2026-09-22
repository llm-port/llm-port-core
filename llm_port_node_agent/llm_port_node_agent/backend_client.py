"""HTTP client for backend onboarding and credential endpoints."""

from __future__ import annotations

from typing import Any

import httpx

from llm_port_node_agent.config import AgentConfig
from llm_port_node_agent.tls import httpx_cert, httpx_verify


class BackendClient:
    """Thin REST client used outside the websocket stream."""

    def __init__(self, config: AgentConfig) -> None:
        self._config = config
        self._client = httpx.AsyncClient(
            base_url=config.backend_url,
            timeout=config.request_timeout_sec,
            verify=httpx_verify(config),
            cert=httpx_cert(config),
        )

    async def close(self) -> None:
        """Close underlying HTTP client."""
        await self._client.aclose()

    @property
    def http(self) -> httpx.AsyncClient:
        """Expose the underlying httpx client for streaming downloads."""
        return self._client

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

    async def request_join(
        self,
        *,
        agent_id: str,
        host: str,
        capabilities: dict[str, Any],
        version: str,
    ) -> dict[str, Any]:
        """Ask to join, and get back a code for a human to compare.

        The other half of onboarding: no token travels to this machine, so
        nothing long has to be typed on it.
        """
        res = await self._client.post(
            "/api/admin/system/nodes/join-requests",
            json={
                "agent_id": agent_id,
                "host": host,
                "capabilities": capabilities,
                "version": version,
            },
        )
        res.raise_for_status()
        payload = res.json()
        if not isinstance(payload, dict):
            raise RuntimeError("Invalid join-request response payload.")
        return payload

    async def collect_join(self, *, request_id: str, poll_secret: str) -> dict[str, Any]:
        """Ask whether an administrator has decided yet."""
        res = await self._client.post(
            f"/api/admin/system/nodes/join-requests/{request_id}/collect",
            json={"poll_secret": poll_secret},
        )
        res.raise_for_status()
        payload = res.json()
        if not isinstance(payload, dict):
            raise RuntimeError("Invalid join-collect response payload.")
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
