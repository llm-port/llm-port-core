from __future__ import annotations

import json
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request, Response, WebSocket
from pydantic import ValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from starlette import status

from llm_port_api.db.dao.gateway_dao import GatewayDAO
from llm_port_api.db.dao.session_dao import SessionDAO
from llm_port_api.services.gateway.audit import AuditService
from llm_port_api.services.gateway.auth import AuthContext, get_auth_context
from llm_port_api.services.gateway.errors import GatewayError, error_response
from llm_port_api.services.gateway.lease import LeaseManager
from llm_port_api.services.gateway.llm_adapter import LLMAdapter
from llm_port_api.services.gateway.observability import GatewayObservability
from llm_port_api.services.gateway.mcp_client import MCPClient
from llm_port_api.services.gateway.mcp_tool_cache import MCPToolCache
from llm_port_api.services.gateway.pii_client import PIIClient
from llm_port_api.services.gateway.skills_client import SkillsClient
from llm_port_api.services.gateway.proxy import UpstreamProxy
from llm_port_api.services.gateway.ratelimit import RateLimiter
from llm_port_api.services.gateway.routing import RouterService
from llm_port_api.services.gateway.schemas import (
    ChatCompletionRequest,
    EmbeddingsRequest,
    SessionToolPolicyDTO,
    SessionToolPolicyPatchDTO,
    ToolAvailabilityResponse,
)
from llm_port_api.services.gateway.service import GatewayService
from llm_port_api.services.registry import service_registry
from llm_port_api.settings import settings

router = APIRouter()


@router.get("/health")
async def public_health_check() -> None:
    """Public health endpoint for L7 probes."""


@router.get("/v1/services")
async def list_services(request: Request) -> JSONResponse:
    """Return the manifest of optional service modules.

    The frontend uses this to discover which features (PII, Auth, RAG, ...)
    are available and healthy so it can show/hide UI sections accordingly.
    """
    registry = request.app.state.service_registry
    # Run async health checks against enabled services
    await registry.check_health(request.app.state.http_client)
    return JSONResponse(status_code=200, content=registry.to_dict())


def get_gateway_service(
    request: Request,
    dao: GatewayDAO = Depends(),
    session_dao: SessionDAO = Depends(),
) -> GatewayService:
    """Build gateway service with request-scoped dependencies."""
    cache = request.app.state.cache_backend
    lease_manager = LeaseManager(cache, ttl_sec=settings.lease_ttl_sec)
    router_service = RouterService(
        dao=dao,
        cache=cache,
        lease_manager=lease_manager,
    )
    proxy = UpstreamProxy(client=request.app.state.http_client)
    limiter = RateLimiter(cache)
    audit = AuditService(dao, pricing_service=getattr(request.app.state, "pricing_service", None))
    observability: GatewayObservability = request.app.state.gateway_observability

    # PII client (optional - only when PII module is enabled in registry)
    pii_client: PIIClient | None = None
    pii_url = service_registry.get_url("pii")
    if pii_url:
        pii_client = PIIClient(
            base_url=pii_url,
            http_client=request.app.state.http_client,
        )

    # MCP client + cache (optional - when MCP module is enabled in registry)
    mcp_client: MCPClient | None = None
    mcp_tool_cache: MCPToolCache | None = None
    mcp_url = service_registry.get_url("mcp")
    if mcp_url and settings.mcp_service_token:
        mcp_client = MCPClient(
            base_url=mcp_url,
            http_client=request.app.state.http_client,
            service_token=settings.mcp_service_token,
        )
        mcp_tool_cache = MCPToolCache(mcp_client)

    # Skills client (optional - when Skills module is enabled in registry)
    skills_client: SkillsClient | None = None
    skills_url = service_registry.get_url("skills")
    if skills_url and settings.skills_service_token:
        skills_client = SkillsClient(
            base_url=skills_url,
            http_client=request.app.state.http_client,
            service_token=settings.skills_service_token,
        )

    # RAG Lite client (optional - when RAG Lite is enabled)
    from llm_port_api.services.gateway.rag_lite_client import RagLiteClient  # noqa: PLC0415

    rag_lite_client: RagLiteClient | None = None
    if settings.rag_lite_enabled and not settings.rag_enabled:
        rag_lite_client = RagLiteClient(
            base_url=settings.rag_lite_backend_url,
            http_client=request.app.state.http_client,
        )

    # Tool Router (optional – when MCP or client tools are available)
    from llm_port_api.services.gateway.tool_router import (  # noqa: PLC0415
        ServerToolExecutor,
        ToolRouter,
        ToolRouterConfig,
    )
    from llm_port_api.services.gateway.client_broker import ClientToolBroker  # noqa: PLC0415
    from llm_port_api.services.gateway.policy_engine import PolicyEngine  # noqa: PLC0415

    tool_router: ToolRouter | None = None
    if mcp_client:
        server_executor = ServerToolExecutor(mcp_client)
        client_broker = ClientToolBroker()
        policy_engine = PolicyEngine()
        tool_router = ToolRouter(
            dao=dao,
            config=ToolRouterConfig(
                server_executor=server_executor,
                client_broker=client_broker,
                policy_engine=policy_engine,
            ),
        )

    service = GatewayService(
        dao=dao,
        router=router_service,
        proxy=proxy,
        adapter=LLMAdapter(),
        limiter=limiter,
        audit=audit,
        observability=observability,
        pii_client=pii_client,
        rag_lite_client=rag_lite_client,
        session_dao=session_dao if settings.sessions_enabled else None,
        file_store=getattr(request.app.state, "chat_file_store", None),
        mcp_client=mcp_client,
        mcp_tool_cache=mcp_tool_cache,
        skills_client=skills_client,
        tool_router=tool_router,
    )
    service.stream_buffer = getattr(request.app.state, "stream_buffer", None)
    return service


@router.get("/v1/models")
async def list_models(
    auth: AuthContext = Depends(get_auth_context),
    service: GatewayService = Depends(get_gateway_service),
) -> JSONResponse:
    """List model aliases available for tenant."""
    try:
        payload = await service.list_models(auth)
        return JSONResponse(status_code=200, content=payload)
    except GatewayError as exc:
        return error_response(
            status_code=exc.status_code,
            message=exc.message,
            error_type=exc.error_type,
            param=exc.param,
            code=exc.code,
        )


# ── Tool Availability ────────────────────────────────────────────


@router.get("/v1/tools/catalog", response_model=ToolAvailabilityResponse)
async def get_tool_catalog(
    request: Request,
    execution_mode: str = "server_only",
    auth: AuthContext = Depends(get_auth_context),
    dao: GatewayDAO = Depends(),
) -> JSONResponse:
    """Return the global tool catalog (no session required).

    Used for pre-session discovery so users can browse available tools
    before starting a conversation.
    """
    from llm_port_api.services.gateway.tool_availability import (  # noqa: PLC0415
        ToolAvailabilityService,
    )

    mcp_client = None
    mcp_tool_cache = None
    mcp_url = service_registry.get_url("mcp")
    if mcp_url and settings.mcp_service_token:
        from llm_port_api.services.gateway.mcp_tool_cache import MCPToolCache  # noqa: PLC0415

        mcp_client = MCPClient(
            base_url=mcp_url,
            http_client=request.app.state.http_client,
            service_token=settings.mcp_service_token,
        )
        mcp_tool_cache = MCPToolCache(mcp_client)

    service = ToolAvailabilityService(
        dao=dao,
        mcp_client=mcp_client,
        mcp_tool_cache=mcp_tool_cache,
    )

    result = await service.get_global_catalog(
        tenant_id=auth.tenant_id,
        execution_mode=execution_mode,
    )
    return JSONResponse(
        status_code=200,
        content=result.model_dump(mode="json"),
    )


@router.get("/v1/tools/available", response_model=ToolAvailabilityResponse)
async def get_available_tools(
    request: Request,
    session_id: str,
    include_disabled: bool = True,
    include_unavailable: bool = True,
    auth: AuthContext = Depends(get_auth_context),
    dao: GatewayDAO = Depends(),
) -> JSONResponse:
    """Return the effective tool catalog for a session.

    Merges MCP tools with session execution policy and per-tool overrides
    to compute effective availability for each tool.
    """
    from llm_port_api.services.gateway.tool_availability import (  # noqa: PLC0415
        ToolAvailabilityService,
    )

    # Build MCP client + cache (same pattern as get_gateway_service)
    mcp_client = None
    mcp_tool_cache = None
    mcp_url = service_registry.get_url("mcp")
    if mcp_url and settings.mcp_service_token:
        from llm_port_api.services.gateway.mcp_tool_cache import MCPToolCache  # noqa: PLC0415

        mcp_client = MCPClient(
            base_url=mcp_url,
            http_client=request.app.state.http_client,
            service_token=settings.mcp_service_token,
        )
        mcp_tool_cache = MCPToolCache(mcp_client)

    service = ToolAvailabilityService(
        dao=dao,
        mcp_client=mcp_client,
        mcp_tool_cache=mcp_tool_cache,
    )

    try:
        import uuid as _uuid

        sid = _uuid.UUID(session_id)
    except ValueError:
        return error_response(
            status_code=400,
            message="Invalid session_id format.",
            error_type="invalid_request_error",
            code="invalid_session_id",
        )

    result = await service.get_available_tools(
        session_id=sid,
        tenant_id=auth.tenant_id,
        include_disabled=include_disabled,
        include_unavailable=include_unavailable,
    )
    return JSONResponse(
        status_code=200,
        content=result.model_dump(mode="json"),
    )


# ── Session Tool Policy ─────────────────────────────────────────


def _parse_session_id(session_id: str) -> uuid.UUID:
    """Validate and parse a session ID string to UUID."""
    try:
        return uuid.UUID(session_id)
    except ValueError as exc:
        raise GatewayError(
            status_code=400,
            message="Invalid session_id format.",
            code="invalid_session_id",
        ) from exc


async def _require_session_owner(
    sid: uuid.UUID,
    auth: AuthContext,
    dao: GatewayDAO,
) -> None:
    """Verify the caller owns the session, or raise 404."""
    owns = await dao.verify_session_ownership(sid, auth.tenant_id, auth.user_id)
    if not owns:
        raise GatewayError(
            status_code=404,
            message="Session not found.",
            code="session_not_found",
        )


@router.get(
    "/v1/sessions/{session_id}/tool-policy",
    response_model=SessionToolPolicyDTO,
)
async def get_session_tool_policy(
    session_id: str,
    auth: AuthContext = Depends(get_auth_context),
    dao: GatewayDAO = Depends(),
) -> JSONResponse:
    """Return the current tool execution policy for a session."""
    sid = _parse_session_id(session_id)
    await _require_session_owner(sid, auth, dao)
    policy = await dao.get_session_execution_policy(sid)
    mode = await dao.get_session_execution_mode(sid)
    dto = SessionToolPolicyDTO(
        session_id=session_id,
        execution_mode=mode.value,
        hybrid_preference=policy.hybrid_preference if policy else None,
        effective_catalog_version=policy.catalog_version if policy else 0,
    )
    return JSONResponse(status_code=200, content=dto.model_dump(mode="json"))


@router.patch(
    "/v1/sessions/{session_id}/tool-policy",
    response_model=SessionToolPolicyDTO,
)
async def patch_session_tool_policy(
    session_id: str,
    request: Request,
    auth: AuthContext = Depends(get_auth_context),
    dao: GatewayDAO = Depends(),
) -> JSONResponse:
    """Update the execution mode, hybrid preference, and/or tool overrides."""
    sid = _parse_session_id(session_id)
    await _require_session_owner(sid, auth, dao)
    body = await _get_json_payload(request)
    patch = SessionToolPolicyPatchDTO.model_validate(body)

    from llm_port_api.db.models.gateway import ExecutionMode  # noqa: PLC0415

    # Update execution policy
    mode_arg = ExecutionMode(patch.execution_mode.value) if patch.execution_mode else None
    policy = await dao.upsert_session_execution_policy(
        sid,
        execution_mode=mode_arg,
        hybrid_preference=patch.hybrid_preference if patch.hybrid_preference is not None else ...,
    )

    # Apply tool overrides
    if patch.tool_overrides:
        await dao.upsert_session_tool_overrides(
            sid,
            [(o.tool_id, o.enabled) for o in patch.tool_overrides],
        )

    dto = SessionToolPolicyDTO(
        session_id=session_id,
        execution_mode=policy.execution_mode.value,
        hybrid_preference=policy.hybrid_preference,
        effective_catalog_version=policy.catalog_version,
    )
    return JSONResponse(status_code=200, content=dto.model_dump(mode="json"))


# ── Client WebSocket ─────────────────────────────────────────────


@router.websocket("/v1/client/ws")
async def client_websocket(
    ws: WebSocket,
    dao: GatewayDAO = Depends(),
) -> None:
    """WebSocket channel for local agentic client connections."""
    from llm_port_api.services.gateway.client_ws import handle_client_websocket  # noqa: PLC0415

    await handle_client_websocket(ws, dao)


@router.post("/v1/embeddings")
async def create_embeddings(
    request: Request,
    auth: AuthContext = Depends(get_auth_context),
    service: GatewayService = Depends(get_gateway_service),
) -> JSONResponse:
    """Proxy embeddings request through shared pipeline."""
    try:
        payload = await _get_json_payload(request)
        EmbeddingsRequest.model_validate(payload)
        request_id = _request_id(request)
        routed = await service.route_non_stream(
            auth=auth,
            endpoint="/v1/embeddings",
            payload=payload,
            request_id=request_id,
        )
        response = JSONResponse(status_code=routed.status_code, content=routed.payload)
        response.headers["x-request-id"] = request_id
        response.headers["x-provider-instance-id"] = routed.provider_instance_id
        if routed.trace_id:
            response.headers["x-langfuse-trace-id"] = routed.trace_id
        return response
    except GatewayError as exc:
        return error_response(
            status_code=exc.status_code,
            message=exc.message,
            error_type=exc.error_type,
            param=exc.param,
            code=exc.code,
        )


@router.post("/v1/chat/completions", response_model=None)
async def create_chat_completions(
    request: Request,
    auth: AuthContext = Depends(get_auth_context),
    service: GatewayService = Depends(get_gateway_service),
) -> Response:
    """Proxy chat completions with stream and non-stream support."""
    try:
        payload = await _get_json_payload(request)
        parsed = ChatCompletionRequest.model_validate(payload)
        request_id = _request_id(request)

        # Strip session_id from upstream payload but pass to service
        session_id = payload.pop("session_id", None)

        if parsed.stream:
            streamed = await service.route_stream_chat(
                auth=auth,
                payload=payload,
                request_id=request_id,
                session_id=session_id,
            )
            stream_response = StreamingResponse(
                streamed.stream,
                media_type="text/event-stream",
            )
            stream_response.headers["x-request-id"] = request_id
            stream_response.headers["x-provider-instance-id"] = (
                streamed.provider_instance_id
            )
            stream_response.headers["cache-control"] = "no-cache"
            if streamed.trace_id:
                stream_response.headers["x-langfuse-trace-id"] = streamed.trace_id
            return stream_response

        non_stream = await service.route_non_stream(
            auth=auth,
            endpoint="/v1/chat/completions",
            payload=payload,
            request_id=request_id,
            session_id=session_id,
        )
        json_response = JSONResponse(
            status_code=non_stream.status_code,
            content=non_stream.payload,
        )
        json_response.headers["x-request-id"] = request_id
        json_response.headers["x-provider-instance-id"] = (
            non_stream.provider_instance_id
        )
        if non_stream.trace_id:
            json_response.headers["x-langfuse-trace-id"] = non_stream.trace_id
        return json_response
    except ValidationError as exc:
        return error_response(
            status_code=status.HTTP_400_BAD_REQUEST,
            message=str(exc),
            error_type="invalid_request_error",
            code="validation_error",
        )
    except GatewayError as exc:
        return error_response(
            status_code=exc.status_code,
            message=exc.message,
            error_type=exc.error_type,
            param=exc.param,
            code=exc.code,
        )


# ── Stream reconnection ──────────────────────────────────────────

@router.get("/v1/sessions/{session_id}/stream/status")
async def stream_status(
    request: Request,
    session_id: str,
    auth: AuthContext = Depends(get_auth_context),
) -> JSONResponse:
    """Check whether a streaming response is still in progress for a session."""
    from llm_port_api.services.gateway.stream_buffer import StreamBuffer  # noqa: PLC0415
    buf: StreamBuffer | None = getattr(request.app.state, "stream_buffer", None)
    if buf and buf.has_buffer(session_id):
        return JSONResponse({"active": buf.is_active(session_id)})
    return JSONResponse({"active": False})


@router.get("/v1/sessions/{session_id}/stream")
async def stream_resume(
    request: Request,
    session_id: str,
    auth: AuthContext = Depends(get_auth_context),
) -> Response:
    """Reconnect to an in-progress (or recently finished) SSE stream.

    Replays all buffered chunks, then tails live chunks until the
    stream completes.  Returns 204 if no buffer exists.
    """
    from llm_port_api.services.gateway.stream_buffer import StreamBuffer  # noqa: PLC0415
    buf: StreamBuffer | None = getattr(request.app.state, "stream_buffer", None)
    if not buf or not buf.has_buffer(session_id):
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    subscriber = await buf.subscribe(session_id)
    return StreamingResponse(
        subscriber,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _request_id(request: Request) -> str:
    header_val = request.headers.get("x-request-id", "").strip()
    return header_val or str(uuid.uuid4())


async def _get_json_payload(request: Request) -> dict[str, Any]:
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > settings.request_max_body_bytes:
                raise GatewayError(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    message="Request body exceeds size limit.",
                    code="request_too_large",
                )
        except ValueError:
            pass
    body = await request.body()
    if len(body) > settings.request_max_body_bytes:
        raise GatewayError(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            message="Request body exceeds size limit.",
            code="request_too_large",
        )
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GatewayError(
            status_code=400,
            message="Request body must be valid JSON.",
            code="invalid_json",
        ) from exc
    if not isinstance(payload, dict):
        raise GatewayError(
            status_code=400,
            message="Request body must be a JSON object.",
            code="invalid_json_body",
        )
    return payload
