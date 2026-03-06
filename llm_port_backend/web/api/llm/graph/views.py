"""LLM Graph endpoints for topology and live trace visualization."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from llm_port_backend.db.models.users import User
from llm_port_backend.services.llm.graph_service import (
    TRACE_DEFAULT_LIMIT,
    TRACE_MAX_LIMIT,
    LLMGraphService,
)
from llm_port_backend.web.api.llm.dependencies import get_llm_graph_service
from llm_port_backend.web.api.llm.schema import DataUsageSummaryDTO, TopologyResponseDTO, TraceSnapshotResponseDTO
from llm_port_backend.web.api.rbac import require_permission

router = APIRouter()


@router.get("/topology", response_model=TopologyResponseDTO)
async def get_graph_topology(
    user: User = Depends(require_permission("llm.graph", "read")),
    graph_service: LLMGraphService = Depends(get_llm_graph_service),
) -> TopologyResponseDTO:
    """Return provider/runtime/model graph topology."""
    return await graph_service.get_topology()


@router.get("/traces", response_model=TraceSnapshotResponseDTO)
async def get_recent_traces(
    user: User = Depends(require_permission("llm.graph", "read")),
    graph_service: LLMGraphService = Depends(get_llm_graph_service),
    limit: int = Query(TRACE_DEFAULT_LIMIT, ge=1, le=TRACE_MAX_LIMIT),
    after_event_id: int | None = Query(None),
) -> TraceSnapshotResponseDTO:
    """Return initial or incremental graph trace events."""
    return await graph_service.list_recent_traces(limit=limit, after_event_id=after_event_id)


@router.get("/data-usage", response_model=DataUsageSummaryDTO)
async def get_data_usage(
    user: User = Depends(require_permission("llm.graph", "read")),
    graph_service: LLMGraphService = Depends(get_llm_graph_service),
) -> DataUsageSummaryDTO:
    """Aggregate token/request usage per provider instance from gateway logs."""
    return await graph_service.get_data_usage()


@router.get("/traces/stream")
async def stream_traces(
    request: Request,
    user: User = Depends(require_permission("llm.graph", "read")),
) -> StreamingResponse:
    """Stream trace events as Server-Sent Events.

    This is an Enterprise-only endpoint.  Returns ``402 Payment Required``
    unless the Observability Pro plugin is loaded (which shadows this route).
    """
    raise HTTPException(
        status_code=402,
        detail=(
            "Gateway trace SSE streaming requires the Observability Pro plugin. "
            "Install llm-port-ee to enable this endpoint."
        ),
    )
