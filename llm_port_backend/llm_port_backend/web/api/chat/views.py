"""User-facing chat proxy endpoints.

Proxies requests from the frontend (cookie auth) to the API gateway
(Bearer token), converting the ``fapiauth`` httponly cookie to an
``Authorization: Bearer`` header.  All endpoints require the user to
be authenticated (cookie present).

Streaming chat completions are forwarded as SSE passthrough using
:class:`~starlette.responses.StreamingResponse`.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from starlette import status as http_status

from llm_port_backend.db.dao.rbac_dao import RbacDAO
from llm_port_backend.db.models.users import User
from llm_port_backend.services.chat.gateway_client import GatewayChatClient
from llm_port_backend.settings import settings
from llm_port_backend.web.api.rbac import require_permission

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/chat", tags=["chat"])

_COOKIE_NAME = "fapiauth"


def _jwt_from_cookie(request: Request) -> str:
    """Extract JWT from the ``fapiauth`` cookie or raise 401."""
    token = request.cookies.get(_COOKIE_NAME)
    if not token:
        raise HTTPException(
            status_code=http_status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )
    return token


def _client() -> GatewayChatClient:
    return GatewayChatClient(base_url=settings.gateway_url)


async def _proxy_error(exc: Exception) -> JSONResponse:
    """Convert httpx errors to JSON error responses."""
    import httpx  # noqa: PLC0415

    if isinstance(exc, httpx.HTTPStatusError):
        try:
            detail = exc.response.json()
        except Exception:
            detail = exc.response.text
        return JSONResponse(
            status_code=exc.response.status_code,
            content=detail if isinstance(detail, dict) else {"detail": detail},
        )
    logger.exception("Gateway proxy error")
    return JSONResponse(
        status_code=http_status.HTTP_502_BAD_GATEWAY,
        content={"detail": "Gateway unavailable"},
    )


# ── Chat Completions ──────────────────────────────────────────────


@router.post("/completions", response_model=None)
async def chat_completions(request: Request) -> StreamingResponse | JSONResponse:
    """Proxy ``/v1/chat/completions`` — supports streaming (SSE) and
    non-streaming modes."""
    jwt = _jwt_from_cookie(request)
    body: dict[str, Any] = await request.json()
    is_stream = body.get("stream", False)

    client = _client()
    try:
        if is_stream:
            return StreamingResponse(
                client.stream_chat(body, jwt),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )
        resp = await client.chat(body, jwt)
        return JSONResponse(status_code=resp.status_code, content=resp.json())
    except Exception as exc:
        return await _proxy_error(exc)


# ── Models ────────────────────────────────────────────────────────


@router.get("/models")
async def list_models(request: Request) -> JSONResponse:
    jwt = _jwt_from_cookie(request)
    client = _client()
    try:
        data = await client.list_models(jwt)
        return JSONResponse(content=data)
    except Exception as exc:
        return await _proxy_error(exc)


# ── Capacity ──────────────────────────────────────────────────────


@router.get("/capacity")
async def get_capacity(request: Request) -> JSONResponse:
    jwt = _jwt_from_cookie(request)
    client = _client()
    try:
        data = await client.get_capacity(jwt)
        return JSONResponse(content=data)
    except Exception as exc:
        return await _proxy_error(exc)


# ── Projects ──────────────────────────────────────────────────────


@router.get("/projects")
async def list_projects(request: Request) -> JSONResponse:
    jwt = _jwt_from_cookie(request)
    client = _client()
    try:
        data = await client.list_projects(jwt)
        return JSONResponse(content=data)
    except Exception as exc:
        return await _proxy_error(exc)


@router.post("/projects", status_code=http_status.HTTP_201_CREATED)
async def create_project(request: Request) -> JSONResponse:
    jwt = _jwt_from_cookie(request)
    body = await request.json()
    client = _client()
    try:
        data = await client.create_project(body, jwt)
        return JSONResponse(status_code=201, content=data)
    except Exception as exc:
        return await _proxy_error(exc)


@router.get("/projects/{project_id}")
async def get_project(project_id: str, request: Request) -> JSONResponse:
    jwt = _jwt_from_cookie(request)
    client = _client()
    try:
        data = await client.get_project(project_id, jwt)
        return JSONResponse(content=data)
    except Exception as exc:
        return await _proxy_error(exc)


@router.patch("/projects/{project_id}")
async def update_project(project_id: str, request: Request) -> JSONResponse:
    jwt = _jwt_from_cookie(request)
    body = await request.json()
    client = _client()
    try:
        data = await client.update_project(project_id, body, jwt)
        return JSONResponse(content=data)
    except Exception as exc:
        return await _proxy_error(exc)


@router.delete("/projects/{project_id}", status_code=http_status.HTTP_204_NO_CONTENT)
async def delete_project(project_id: str, request: Request) -> None:
    jwt = _jwt_from_cookie(request)
    client = _client()
    try:
        await client.delete_project(project_id, jwt)
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Gateway error") from exc


# ── Sessions ──────────────────────────────────────────────────────


@router.get("/sessions")
async def list_sessions(
    request: Request,
    project_id: str | None = None,
) -> JSONResponse:
    jwt = _jwt_from_cookie(request)
    client = _client()
    try:
        data = await client.list_sessions(jwt, project_id=project_id)
        return JSONResponse(content=data)
    except Exception as exc:
        return await _proxy_error(exc)


@router.post("/sessions", status_code=http_status.HTTP_201_CREATED)
async def create_session(request: Request) -> JSONResponse:
    jwt = _jwt_from_cookie(request)
    body = await request.json()
    client = _client()
    try:
        data = await client.create_session(body, jwt)
        return JSONResponse(status_code=201, content=data)
    except Exception as exc:
        return await _proxy_error(exc)


@router.get("/sessions/{session_id}")
async def get_session(session_id: str, request: Request) -> JSONResponse:
    jwt = _jwt_from_cookie(request)
    client = _client()
    try:
        data = await client.get_session(session_id, jwt)
        return JSONResponse(content=data)
    except Exception as exc:
        return await _proxy_error(exc)


@router.patch("/sessions/{session_id}")
async def update_session(session_id: str, request: Request) -> JSONResponse:
    jwt = _jwt_from_cookie(request)
    body = await request.json()
    client = _client()
    try:
        data = await client.update_session(session_id, body, jwt)
        return JSONResponse(content=data)
    except Exception as exc:
        return await _proxy_error(exc)


@router.delete(
    "/sessions/{session_id}", status_code=http_status.HTTP_204_NO_CONTENT,
)
async def delete_session(session_id: str, request: Request) -> None:
    jwt = _jwt_from_cookie(request)
    client = _client()
    try:
        await client.delete_session(session_id, jwt)
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Gateway error") from exc


# ── Messages ──────────────────────────────────────────────────────


@router.get("/sessions/{session_id}/messages")
async def list_messages(
    session_id: str,
    request: Request,
    limit: int = 100,
) -> JSONResponse:
    jwt = _jwt_from_cookie(request)
    client = _client()
    try:
        data = await client.list_messages(session_id, jwt, limit=limit)
        return JSONResponse(content=data)
    except Exception as exc:
        return await _proxy_error(exc)


# ── Stream reconnection ──────────────────────────────────────────


@router.get("/sessions/{session_id}/stream/status")
async def stream_status(session_id: str, request: Request) -> JSONResponse:
    """Check whether an SSE stream is still active for a session."""
    jwt = _jwt_from_cookie(request)
    client = _client()
    try:
        data = await client.stream_status(session_id, jwt)
        return JSONResponse(content=data)
    except Exception as exc:
        return await _proxy_error(exc)


@router.get("/sessions/{session_id}/stream", response_model=None)
async def stream_resume(session_id: str, request: Request) -> StreamingResponse | JSONResponse:
    """Reconnect to an in-progress SSE stream (replays buffered chunks)."""
    jwt = _jwt_from_cookie(request)
    client = _client()
    try:
        return StreamingResponse(
            client.stream_resume(session_id, jwt),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    except Exception as exc:
        return await _proxy_error(exc)


# ── Attachments ───────────────────────────────────────────────────


@router.post(
    "/sessions/{session_id}/attachments",
    status_code=http_status.HTTP_201_CREATED,
)
async def upload_attachment(
    session_id: str,
    request: Request,
    file: UploadFile | None = None,
) -> JSONResponse:
    jwt = _jwt_from_cookie(request)
    if not file:
        raise HTTPException(status_code=400, detail="No file provided")
    file_bytes = await file.read()
    client = _client()
    try:
        data = await client.upload_attachment(
            session_id,
            file_bytes,
            file.filename or "file",
            file.content_type or "application/octet-stream",
            jwt,
        )
        return JSONResponse(status_code=201, content=data)
    except Exception as exc:
        return await _proxy_error(exc)


@router.get("/sessions/{session_id}/attachments")
async def list_attachments(session_id: str, request: Request) -> JSONResponse:
    jwt = _jwt_from_cookie(request)
    client = _client()
    try:
        data = await client.list_attachments(session_id, jwt)
        return JSONResponse(content=data)
    except Exception as exc:
        return await _proxy_error(exc)


@router.delete(
    "/attachments/{attachment_id}", status_code=http_status.HTTP_204_NO_CONTENT,
)
async def delete_attachment(attachment_id: str, request: Request) -> None:
    jwt = _jwt_from_cookie(request)
    client = _client()
    try:
        await client.delete_attachment(attachment_id, jwt)
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Gateway error") from exc


# ── Tool Availability & Policy ────────────────────────────────────


@router.get("/tools/catalog")
async def get_tool_catalog(
    request: Request,
    execution_mode: str = "server_only",
) -> JSONResponse:
    """Proxy ``GET /v1/tools/catalog`` to the API gateway (no session required)."""
    jwt = _jwt_from_cookie(request)
    client = _client()
    try:
        data = await client.get_tool_catalog(jwt, execution_mode=execution_mode)
        return JSONResponse(content=data)
    except Exception as exc:
        return await _proxy_error(exc)


@router.get("/tools/available")
async def get_available_tools(
    request: Request,
    session_id: str,
    include_disabled: bool = True,
    include_unavailable: bool = True,
) -> JSONResponse:
    """Proxy ``GET /v1/tools/available`` to the API gateway."""
    jwt = _jwt_from_cookie(request)
    client = _client()
    try:
        data = await client.get_available_tools(
            session_id,
            jwt,
            include_disabled=include_disabled,
            include_unavailable=include_unavailable,
        )
        return JSONResponse(content=data)
    except Exception as exc:
        return await _proxy_error(exc)


@router.get("/sessions/{session_id}/tool-policy")
async def get_session_tool_policy(
    session_id: str,
    request: Request,
    _user: User = Depends(require_permission("chat.tool_policy", "read")),
) -> JSONResponse:
    """Proxy ``GET /v1/sessions/{session_id}/tool-policy`` to the API gateway."""
    jwt = _jwt_from_cookie(request)
    client = _client()
    try:
        data = await client.get_session_tool_policy(session_id, jwt)
        return JSONResponse(content=data)
    except Exception as exc:
        return await _proxy_error(exc)


@router.patch("/sessions/{session_id}/tool-policy")
async def patch_session_tool_policy(
    session_id: str,
    request: Request,
    _user: User = Depends(require_permission("chat.tool_policy", "update")),
) -> JSONResponse:
    """Proxy ``PATCH /v1/sessions/{session_id}/tool-policy`` to the API gateway."""
    jwt = _jwt_from_cookie(request)
    body = await request.json()
    client = _client()
    try:
        data = await client.patch_session_tool_policy(session_id, body, jwt)
        return JSONResponse(content=data)
    except Exception as exc:
        return await _proxy_error(exc)


@router.get("/sessions/{session_id}/pii-policy")
async def get_session_pii_policy(
    session_id: str,
    request: Request,
    _user: User = Depends(require_permission("pii.session", "read")),
) -> JSONResponse:
    """Proxy ``GET /v1/sessions/{session_id}/pii-policy`` to the API gateway."""
    jwt = _jwt_from_cookie(request)
    client = _client()
    try:
        data = await client.get_session_pii_policy(session_id, jwt)
        return JSONResponse(content=data)
    except Exception as exc:
        return await _proxy_error(exc)


@router.patch("/sessions/{session_id}/pii-policy")
async def patch_session_pii_policy(
    session_id: str,
    request: Request,
    _user: User = Depends(require_permission("pii.session", "strengthen")),
    rbac_dao: RbacDAO = Depends(),
) -> JSONResponse:
    """Proxy ``PATCH /v1/sessions/{session_id}/pii-policy`` to the API gateway."""
    jwt = _jwt_from_cookie(request)
    body = await request.json()

    # Soft-check: does the caller also have the 'weaken' privilege?
    allow_weaken = _user.is_superuser or await rbac_dao.has_permission(
        _user.id, "pii.session", "weaken",
    )

    client = _client()
    try:
        data = await client.patch_session_pii_policy(
            session_id, body, jwt, allow_weaken=allow_weaken,
        )
        return JSONResponse(content=data)
    except Exception as exc:
        return await _proxy_error(exc)


@router.delete("/sessions/{session_id}/pii-policy")
async def delete_session_pii_policy(
    session_id: str,
    request: Request,
    _user: User = Depends(require_permission("pii.session", "strengthen")),
) -> Response:
    """Proxy ``DELETE /v1/sessions/{session_id}/pii-policy`` to the API gateway."""
    jwt = _jwt_from_cookie(request)
    client = _client()
    try:
        await client.delete_session_pii_policy(session_id, jwt)
        return Response(status_code=204)
    except Exception as exc:
        return await _proxy_error(exc)
