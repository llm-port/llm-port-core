"""Async HTTP client for proxying chat requests to the API gateway.

The backend uses cookie-based auth (``fapiauth`` httponly JWT).  This
client converts that to a ``Bearer`` token that the gateway expects and
proxies all chat-related requests, including SSE streams.

Usage::

    client = GatewayChatClient()
    models = await client.list_models(jwt="<token>")
    async for chunk in client.stream_chat(payload, jwt="<token>"):
        ...  # forward SSE bytes
"""

from __future__ import annotations

import logging
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from llm_port_backend.settings import settings

logger = logging.getLogger(__name__)

_CONNECT_TIMEOUT = 10.0
_READ_TIMEOUT = 300.0  # 5 min for long generation


class GatewayChatClient:
    """Thin async proxy for the API gateway chat endpoints."""

    def __init__(self, base_url: str | None = None) -> None:
        self._base_url = (base_url or settings.gateway_url).rstrip("/")
        self._client: httpx.AsyncClient | None = None

    # -- lifecycle ----------------------------------------------------------

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(
                    connect=_CONNECT_TIMEOUT,
                    read=_READ_TIMEOUT,
                    write=_READ_TIMEOUT,
                    pool=_CONNECT_TIMEOUT,
                ),
            )
        return self._client

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _headers(jwt: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {jwt}"}

    # -- chat completions (streaming) ---------------------------------------

    async def stream_chat(
        self,
        payload: dict[str, Any],
        jwt: str,
    ) -> AsyncIterator[bytes]:
        """Stream SSE chunks from the gateway ``/v1/chat/completions``."""
        client = self._ensure_client()
        try:
            async with client.stream(
                "POST",
                "/v1/chat/completions",
                json=payload,
                headers=self._headers(jwt),
            ) as resp:
                resp.raise_for_status()
                async for chunk in resp.aiter_bytes():
                    yield chunk
        except httpx.HTTPStatusError as exc:
            body_text = ""
            try:
                body_bytes = await exc.response.aread()
                body_text = body_bytes.decode("utf-8", errors="replace")
            except Exception:
                body_text = ""

            try:
                detail = json.loads(body_text) if body_text else {}
            except Exception:
                detail = {
                    "error": {
                        "message": body_text or str(exc),
                        "type": "gateway_error",
                        "code": "gateway_http_error",
                    },
                }

            if not isinstance(detail, dict):
                detail = {
                    "error": {
                        "message": str(detail),
                        "type": "gateway_error",
                        "code": "gateway_http_error",
                    },
                }

            error_obj = detail.get("error") if isinstance(detail.get("error"), dict) else {}
            error_obj = dict(error_obj)
            error_obj.setdefault(
                "message",
                exc.response.reason_phrase or "Gateway request failed",
            )
            error_obj.setdefault("type", "gateway_error")
            error_obj.setdefault("code", "gateway_http_error")
            error_obj.setdefault("status", exc.response.status_code)

            sse_payload = json.dumps({"error": error_obj}, ensure_ascii=False)
            yield f"data: {sse_payload}\n\n".encode("utf-8")
            yield b"data: [DONE]\n\n"
        except Exception:
            logger.exception("Streaming chat proxy error")
            sse_payload = json.dumps(
                {
                    "error": {
                        "message": "Gateway unavailable",
                        "type": "gateway_unavailable",
                        "code": "gateway_unavailable",
                    },
                },
                ensure_ascii=False,
            )
            yield f"data: {sse_payload}\n\n".encode("utf-8")
            yield b"data: [DONE]\n\n"

    # -- chat completions (non-streaming) -----------------------------------

    async def chat(
        self,
        payload: dict[str, Any],
        jwt: str,
    ) -> httpx.Response:
        """Non-streaming chat completions."""
        client = self._ensure_client()
        resp = await client.post(
            "/v1/chat/completions",
            json=payload,
            headers=self._headers(jwt),
        )
        return resp

    # -- stream reconnection ------------------------------------------------

    async def stream_status(self, session_id: str, jwt: str) -> Any:
        """Check if a stream is active for *session_id*."""
        client = self._ensure_client()
        resp = await client.get(
            f"/v1/sessions/{session_id}/stream/status",
            headers=self._headers(jwt),
        )
        resp.raise_for_status()
        return resp.json()

    async def stream_resume(
        self, session_id: str, jwt: str,
    ) -> AsyncIterator[bytes]:
        """Reconnect to an in-progress SSE stream."""
        client = self._ensure_client()
        async with client.stream(
            "GET",
            f"/v1/sessions/{session_id}/stream",
            headers=self._headers(jwt),
        ) as resp:
            resp.raise_for_status()
            async for chunk in resp.aiter_bytes():
                yield chunk

    # -- models -------------------------------------------------------------

    async def list_models(self, jwt: str) -> Any:
        client = self._ensure_client()
        resp = await client.get("/v1/models", headers=self._headers(jwt))
        resp.raise_for_status()
        return resp.json()

    # -- capacity -----------------------------------------------------------

    async def get_capacity(self, jwt: str) -> Any:
        client = self._ensure_client()
        resp = await client.get(
            "/v1/sessions/capacity", headers=self._headers(jwt),
        )
        resp.raise_for_status()
        return resp.json()

    # -- projects -----------------------------------------------------------

    async def list_projects(self, jwt: str) -> Any:
        client = self._ensure_client()
        resp = await client.get(
            "/v1/sessions/projects", headers=self._headers(jwt),
        )
        resp.raise_for_status()
        return resp.json()

    async def create_project(self, body: dict[str, Any], jwt: str) -> Any:
        client = self._ensure_client()
        resp = await client.post(
            "/v1/sessions/projects", json=body, headers=self._headers(jwt),
        )
        resp.raise_for_status()
        return resp.json()

    async def get_project(self, project_id: str, jwt: str) -> Any:
        client = self._ensure_client()
        resp = await client.get(
            f"/v1/sessions/projects/{project_id}", headers=self._headers(jwt),
        )
        resp.raise_for_status()
        return resp.json()

    async def update_project(
        self, project_id: str, body: dict[str, Any], jwt: str,
    ) -> Any:
        client = self._ensure_client()
        resp = await client.patch(
            f"/v1/sessions/projects/{project_id}",
            json=body,
            headers=self._headers(jwt),
        )
        resp.raise_for_status()
        return resp.json()

    async def delete_project(self, project_id: str, jwt: str) -> None:
        client = self._ensure_client()
        resp = await client.delete(
            f"/v1/sessions/projects/{project_id}", headers=self._headers(jwt),
        )
        resp.raise_for_status()

    # -- sessions -----------------------------------------------------------

    async def list_sessions(
        self, jwt: str, *, project_id: str | None = None,
    ) -> Any:
        client = self._ensure_client()
        params: dict[str, str] = {}
        if project_id:
            params["project_id"] = project_id
        resp = await client.get(
            "/v1/sessions", params=params, headers=self._headers(jwt),
        )
        resp.raise_for_status()
        return resp.json()

    async def create_session(self, body: dict[str, Any], jwt: str) -> Any:
        client = self._ensure_client()
        resp = await client.post(
            "/v1/sessions", json=body, headers=self._headers(jwt),
        )
        resp.raise_for_status()
        return resp.json()

    async def get_session(self, session_id: str, jwt: str) -> Any:
        client = self._ensure_client()
        resp = await client.get(
            f"/v1/sessions/{session_id}", headers=self._headers(jwt),
        )
        resp.raise_for_status()
        return resp.json()

    async def update_session(
        self, session_id: str, body: dict[str, Any], jwt: str,
    ) -> Any:
        client = self._ensure_client()
        resp = await client.patch(
            f"/v1/sessions/{session_id}",
            json=body,
            headers=self._headers(jwt),
        )
        resp.raise_for_status()
        return resp.json()

    async def delete_session(self, session_id: str, jwt: str) -> None:
        client = self._ensure_client()
        resp = await client.delete(
            f"/v1/sessions/{session_id}", headers=self._headers(jwt),
        )
        resp.raise_for_status()

    # -- messages -----------------------------------------------------------

    async def list_messages(
        self, session_id: str, jwt: str, *, limit: int = 100,
    ) -> Any:
        client = self._ensure_client()
        resp = await client.get(
            f"/v1/sessions/{session_id}/messages",
            params={"limit": limit},
            headers=self._headers(jwt),
        )
        resp.raise_for_status()
        return resp.json()

    # -- attachments --------------------------------------------------------

    async def upload_attachment(
        self,
        session_id: str,
        file_bytes: bytes,
        filename: str,
        content_type: str,
        jwt: str,
    ) -> Any:
        client = self._ensure_client()
        resp = await client.post(
            f"/v1/sessions/{session_id}/attachments",
            files={"file": (filename, file_bytes, content_type)},
            headers=self._headers(jwt),
        )
        resp.raise_for_status()
        return resp.json()

    async def list_attachments(self, session_id: str, jwt: str) -> Any:
        client = self._ensure_client()
        resp = await client.get(
            f"/v1/sessions/{session_id}/attachments",
            headers=self._headers(jwt),
        )
        resp.raise_for_status()
        return resp.json()

    async def delete_attachment(self, attachment_id: str, jwt: str) -> None:
        client = self._ensure_client()
        resp = await client.delete(
            f"/v1/sessions/{attachment_id}/attachments/{attachment_id}",
            headers=self._headers(jwt),
        )
        resp.raise_for_status()

    # -- tool availability / policy -----------------------------------------

    async def get_tool_catalog(
        self,
        jwt: str,
        *,
        execution_mode: str = "server_only",
    ) -> Any:
        """Fetch the global tool catalog (no session required)."""
        client = self._ensure_client()
        resp = await client.get(
            "/v1/tools/catalog",
            params={"execution_mode": execution_mode},
            headers=self._headers(jwt),
        )
        resp.raise_for_status()
        return resp.json()

    async def get_available_tools(
        self,
        session_id: str,
        jwt: str,
        *,
        include_disabled: bool = True,
        include_unavailable: bool = True,
    ) -> Any:
        client = self._ensure_client()
        resp = await client.get(
            "/v1/tools/available",
            params={
                "session_id": session_id,
                "include_disabled": str(include_disabled).lower(),
                "include_unavailable": str(include_unavailable).lower(),
            },
            headers=self._headers(jwt),
        )
        resp.raise_for_status()
        return resp.json()

    async def get_session_tool_policy(
        self, session_id: str, jwt: str,
    ) -> Any:
        client = self._ensure_client()
        resp = await client.get(
            f"/v1/sessions/{session_id}/tool-policy",
            headers=self._headers(jwt),
        )
        resp.raise_for_status()
        return resp.json()

    async def patch_session_tool_policy(
        self, session_id: str, body: dict[str, Any], jwt: str,
    ) -> Any:
        client = self._ensure_client()
        resp = await client.patch(
            f"/v1/sessions/{session_id}/tool-policy",
            json=body,
            headers=self._headers(jwt),
        )
        resp.raise_for_status()
        return resp.json()

    async def get_pii_defaults(self, jwt: str) -> Any:
        client = self._ensure_client()
        resp = await client.get(
            "/v1/pii-defaults",
            headers=self._headers(jwt),
        )
        resp.raise_for_status()
        return resp.json()

    async def get_session_pii_policy(
        self, session_id: str, jwt: str,
    ) -> Any:
        client = self._ensure_client()
        resp = await client.get(
            f"/v1/sessions/{session_id}/pii-policy",
            headers=self._headers(jwt),
        )
        resp.raise_for_status()
        return resp.json()

    async def patch_session_pii_policy(
        self, session_id: str, body: dict[str, Any], jwt: str,
        *, allow_weaken: bool = False,
    ) -> Any:
        client = self._ensure_client()
        headers = self._headers(jwt)
        if allow_weaken:
            headers["X-PII-Allow-Weaken"] = "true"
        resp = await client.patch(
            f"/v1/sessions/{session_id}/pii-policy",
            json=body,
            headers=headers,
        )
        resp.raise_for_status()
        return resp.json()

    async def delete_session_pii_policy(
        self, session_id: str, jwt: str,
    ) -> Any:
        client = self._ensure_client()
        resp = await client.delete(
            f"/v1/sessions/{session_id}/pii-policy",
            headers=self._headers(jwt),
        )
        resp.raise_for_status()
        if resp.status_code == 204:
            return None
        return resp.json()
