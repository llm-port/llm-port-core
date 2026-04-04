from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(slots=True, frozen=True)
class UpstreamResult:
    """Non-stream upstream response."""

    status_code: int
    payload: dict[str, Any]
    headers: dict[str, str]


def create_shared_http_client(
    *,
    timeout_sec: float = 30.0,
    max_connections: int = 100,
    max_keepalive: int = 20,
) -> httpx.AsyncClient:
    """Create a shared httpx client with connection pooling."""
    return httpx.AsyncClient(
        limits=httpx.Limits(
            max_connections=max_connections,
            max_keepalive_connections=max_keepalive,
        ),
        timeout=httpx.Timeout(
            connect=5.0,
            read=timeout_sec,
            write=10.0,
            pool=10.0,
        ),
    )


class UpstreamProxy:
    """HTTP client wrapper for OpenAI-compatible upstream providers.

    Uses a shared :class:`httpx.AsyncClient` with connection pooling
    instead of creating a new client per request.
    """

    def __init__(self, *, client: httpx.AsyncClient) -> None:
        self._client = client

    async def post_json(
        self,
        *,
        base_url: str,
        path: str,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> UpstreamResult:
        """Proxy a non-streaming JSON request."""
        response = await self._client.post(
            f"{base_url}{path}",
            json=payload,
            headers=headers,
        )
        parsed: dict[str, Any]
        try:
            parsed = response.json()
        except ValueError:
            parsed = {
                "error": {
                    "type": "upstream_error",
                    "message": response.text or "Invalid JSON upstream response.",
                    "param": None,
                    "code": "invalid_upstream_json",
                },
            }
        return UpstreamResult(
            status_code=response.status_code,
            payload=parsed,
            headers={k.lower(): v for k, v in response.headers.items()},
        )

    async def get_json(
        self,
        *,
        base_url: str,
        path: str,
        headers: dict[str, str] | None = None,
    ) -> UpstreamResult:
        """Proxy a GET JSON request."""
        response = await self._client.get(f"{base_url}{path}", headers=headers)
        parsed = response.json()
        return UpstreamResult(
            status_code=response.status_code,
            payload=parsed,
            headers={k.lower(): v for k, v in response.headers.items()},
        )

    async def stream_post(
        self,
        *,
        base_url: str,
        path: str,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> AsyncIterator[bytes]:
        """Proxy a streaming request by yielding raw byte chunks."""
        async with self._client.stream(
            "POST",
            f"{base_url}{path}",
            json=payload,
            headers=headers,
        ) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                if chunk:
                    yield chunk
