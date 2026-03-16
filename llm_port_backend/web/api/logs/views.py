"""Loki-backed logs endpoints proxied via backend."""

from __future__ import annotations

import json
import logging
import re
import uuid
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from urllib.parse import quote, urlencode, urlparse, urlunparse

import httpx
import jwt
import websockets
from fastapi import APIRouter, Depends, HTTPException, Query, Request, WebSocket
from starlette import status
from starlette.websockets import WebSocketDisconnect

from fastapi_users.db import SQLAlchemyUserDatabase

from llm_port_backend.db.models.users import User
from llm_port_backend.settings import settings
from llm_port_backend.web.api.rbac import require_permission

router = APIRouter()
log = logging.getLogger(__name__)

_RATE_LIMIT_PER_MINUTE = 120


async def _ws_authenticate(websocket: WebSocket) -> User:
    """Extract and verify the user from a WebSocket cookie JWT.

    fastapi-users' ``current_user`` dependency doesn't work on WebSocket
    endpoints because its transport layer requires an HTTP ``Request``.
    This helper manually reads the ``fapiauth`` cookie set by the cookie
    transport and validates it against the same JWT secret.
    """
    token = websocket.cookies.get("fapiauth")
    if not token:
        await websocket.close(code=4401, reason="Not authenticated")
        raise WebSocketDisconnect(code=4401, reason="Not authenticated")

    try:
        payload = jwt.decode(
            token,
            settings.users_secret,
            algorithms=["HS256"],
            audience=["fastapi-users:auth"],
        )
        user_id = payload.get("sub")
        if not user_id:
            raise ValueError("Missing sub claim")
    except (jwt.PyJWTError, ValueError):
        await websocket.close(code=4401, reason="Invalid token")
        raise WebSocketDisconnect(code=4401, reason="Invalid token")

    session_factory = websocket.app.state.db_session_factory
    async with session_factory() as session:
        user_db = SQLAlchemyUserDatabase(session, User)
        user = await user_db.get(uuid.UUID(user_id))
        if user is None or not user.is_active:
            await websocket.close(code=4403, reason="User not found or inactive")
            raise WebSocketDisconnect(code=4403)
        return user


_rate_buckets: dict[str, int] = defaultdict(int)

_LABEL_MATCH_RE = re.compile(r"([a-zA-Z_][a-zA-Z0-9_]*)\s*(?:=|!=|=~|!~)")


class LokiUpstreamError(Exception):
    """Wraps failures from Loki."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def _loki_http_url(path: str) -> str:
    return f"{settings.loki_base_url.rstrip('/')}{path}"


def _loki_ws_url(path: str, params: dict[str, str]) -> str:
    parsed = urlparse(settings.loki_base_url)
    ws_scheme = "wss" if parsed.scheme == "https" else "ws"
    encoded = urlencode(params)
    return urlunparse((ws_scheme, parsed.netloc, path, "", encoded, ""))


def _get_correlation_id(request: Request) -> str:
    return request.headers.get("X-Request-ID") or str(uuid.uuid4())


def _allowlisted_labels() -> set[str] | None:
    return settings.logs_allowed_labels


def _enforce_rate_limit(request: Request, endpoint: str) -> None:
    client_ip = request.client.host if request.client else "unknown"
    minute_key = datetime.now(tz=UTC).strftime("%Y%m%d%H%M")
    key = f"{client_ip}:{endpoint}:{minute_key}"
    _rate_buckets[key] += 1
    if _rate_buckets[key] > _RATE_LIMIT_PER_MINUTE:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded for logs endpoint.",
        )


def _enforce_label_name_allowed(name: str) -> None:
    allowed = _allowlisted_labels()
    if allowed is None:
        return
    if name.lower() not in allowed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Label '{name}' is not allowed.",
        )


def _extract_query_labels(query: str) -> set[str]:
    selectors = re.findall(r"\{([^}]*)\}", query)
    extracted: set[str] = set()
    for selector in selectors:
        for match in _LABEL_MATCH_RE.finditer(selector):
            extracted.add(match.group(1).lower())
    return extracted


def _enforce_query_allowlist(query: str) -> None:
    allowed = _allowlisted_labels()
    if allowed is None:
        return
    used = _extract_query_labels(query)
    disallowed = sorted(label for label in used if label not in allowed)
    if disallowed:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Disallowed labels in query: {', '.join(disallowed)}",
        )


def _parse_time_to_ns(value: str) -> int:
    if value.isdigit():
        return int(value)
    normalized = value.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid time value '{value}'. Use ns epoch or RFC3339.",
        ) from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp() * 1_000_000_000)


def _time_param_ns(value: str | None, default_ns: int) -> int:
    if value is None:
        return default_ns
    return _parse_time_to_ns(value)


def _ns_to_rfc3339nano(ns_value: str) -> str:
    ns = int(ns_value)
    sec = ns // 1_000_000_000
    nanos = ns % 1_000_000_000
    dt = datetime.fromtimestamp(sec, tz=UTC)
    return f"{dt:%Y-%m-%dT%H:%M:%S}.{nanos:09d}Z"


def _maybe_parse_structured(line: str) -> dict[str, Any] | None:
    try:
        parsed = json.loads(line)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _normalize_query_range(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data", {})
    streams_out: list[dict[str, Any]] = []
    for stream_item in data.get("result", []):
        labels = stream_item.get("stream", {}) or {}
        entries: list[dict[str, Any]] = []

        values = stream_item.get("values", [])
        for raw_ts, line in values:
            entry: dict[str, Any] = {
                "ts": _ns_to_rfc3339nano(str(raw_ts)),
                "line": str(line),
            }
            structured = _maybe_parse_structured(str(line))
            if structured is not None:
                entry["structured"] = structured
            entries.append(entry)

        streams_out.append({"labels": labels, "entries": entries})

    normalized: dict[str, Any] = {"streams": streams_out}
    stats = data.get("stats")
    if stats is not None:
        normalized["stats"] = stats
    return normalized


_loki_client: httpx.AsyncClient | None = None


def _get_loki_client() -> httpx.AsyncClient:
    global _loki_client
    if _loki_client is None:
        _loki_client = httpx.AsyncClient(timeout=15.0)
    return _loki_client


async def _request_loki_json(
    path: str,
    params: dict[str, str] | None = None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    url = _loki_http_url(path)
    try:
        response = await _get_loki_client().get(url, params=params, timeout=timeout)
    except httpx.TimeoutException as exc:
        raise LokiUpstreamError("Loki request timed out.") from exc
    except httpx.HTTPError as exc:
        raise LokiUpstreamError(f"Loki connection failed: {exc}") from exc

    if response.status_code >= 400:
        detail = response.text[:300]
        raise LokiUpstreamError(
            message=f"Loki error {response.status_code}: {detail}",
            status_code=response.status_code,
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise LokiUpstreamError("Loki returned invalid JSON.") from exc
    return payload


def _raise_bad_gateway(exc: LokiUpstreamError, correlation_id: str) -> None:
    log.warning(
        "logs.loki_upstream_error correlation_id=%s status=%s message=%s",
        correlation_id,
        exc.status_code,
        exc.message,
    )
    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=exc.message,
    ) from exc


@router.get("/labels", name="logs_labels")
async def list_labels(
    request: Request,
    _user: User = Depends(require_permission("logs", "read")),
) -> dict[str, list[str]]:
    """Return available Loki labels (optionally filtered by allowlist)."""
    correlation_id = _get_correlation_id(request)
    _enforce_rate_limit(request, "labels")
    try:
        payload = await _request_loki_json("/loki/api/v1/labels")
    except LokiUpstreamError as exc:
        _raise_bad_gateway(exc, correlation_id)

    labels = payload.get("data", []) or []
    allowed = _allowlisted_labels()
    if allowed is not None:
        labels = [label for label in labels if str(label).lower() in allowed]
    log.info("logs.labels correlation_id=%s count=%s", correlation_id, len(labels))
    return {"labels": sorted(str(label) for label in labels)}


@router.get("/label/{name}/values", name="logs_label_values")
async def list_label_values(
    name: str,
    request: Request,
    _user: User = Depends(require_permission("logs", "read")),
) -> dict[str, Any]:
    """Return values for one label name."""
    correlation_id = _get_correlation_id(request)
    _enforce_rate_limit(request, "label_values")
    _enforce_label_name_allowed(name)
    try:
        payload = await _request_loki_json(f"/loki/api/v1/label/{quote(name)}/values")
    except LokiUpstreamError as exc:
        _raise_bad_gateway(exc, correlation_id)
    values = payload.get("data", []) or []
    log.info(
        "logs.label_values correlation_id=%s label=%s count=%s",
        correlation_id,
        name,
        len(values),
    )
    return {"label": name, "values": sorted(str(value) for value in values)}


@router.get("/query_range", name="logs_query_range")
async def query_range(
    request: Request,
    query: str = Query(..., min_length=1),
    start: str | None = Query(default=None),
    end: str | None = Query(default=None),
    limit: int | None = Query(default=None, ge=1),
    direction: Literal["BACKWARD", "FORWARD"] = Query(default="BACKWARD"),
    _user: User = Depends(require_permission("logs", "read")),
) -> dict[str, Any]:
    """Proxy Loki query_range and normalize response for frontend consumption."""
    correlation_id = _get_correlation_id(request)
    _enforce_rate_limit(request, "query_range")
    _enforce_query_allowlist(query)

    now = datetime.now(tz=UTC)
    default_end_ns = int(now.timestamp() * 1_000_000_000)
    default_start_ns = int((now - timedelta(minutes=15)).timestamp() * 1_000_000_000)
    start_ns = _time_param_ns(start, default_start_ns)
    end_ns = _time_param_ns(end, default_end_ns)
    if start_ns > end_ns:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="start must be <= end",
        )

    bounded_limit = limit if limit is not None else settings.logs_default_limit
    bounded_limit = max(1, min(bounded_limit, settings.logs_max_limit))

    params = {
        "query": query,
        "start": str(start_ns),
        "end": str(end_ns),
        "limit": str(bounded_limit),
        "direction": direction,
    }

    try:
        payload = await _request_loki_json("/loki/api/v1/query_range", params=params)
    except LokiUpstreamError as exc:
        _raise_bad_gateway(exc, correlation_id)

    normalized = _normalize_query_range(payload)
    log.info(
        "logs.query_range correlation_id=%s direction=%s limit=%s streams=%s",
        correlation_id,
        direction,
        bounded_limit,
        len(normalized.get("streams", [])),
    )
    return normalized


@router.websocket("/tail")
async def tail_logs(
    websocket: WebSocket,
    query: str = Query(..., min_length=1),
) -> None:
    """
    Stream tail messages from Loki to the browser via backend websocket proxy.

    Loki currently publishes JSON frames for tail updates. The endpoint forwards
    each frame as-is so the frontend can parse/shape it as needed.
    """
    user = await _ws_authenticate(websocket)
    _enforce_query_allowlist(query)
    await websocket.accept()

    ws_url = _loki_ws_url("/loki/api/v1/tail", {"query": query})
    try:
        async with websockets.connect(ws_url, ping_interval=20, ping_timeout=20) as loki_ws:
            async for message in loki_ws:
                if isinstance(message, bytes):
                    await websocket.send_text(message.decode("utf-8", errors="replace"))
                else:
                    await websocket.send_text(message)
    except WebSocketDisconnect:
        return
    except Exception as exc:
        log.warning("logs.tail upstream failure: %s", exc)
        await websocket.close(code=1011, reason="Upstream Loki tail failed.")
