"""LLM Graph endpoints for topology and live trace visualization."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query
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
    user: User = Depends(require_permission("llm.graph", "read")),
    graph_service: LLMGraphService = Depends(get_llm_graph_service),
    last_event_id_header: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    cursor: int | None = Query(None),
) -> StreamingResponse:
    """Stream trace events as Server-Sent Events."""
    if cursor is None and last_event_id_header:
        try:
            cursor = int(last_event_id_header)
        except ValueError:
            cursor = None
    stream = graph_service.stream_traces(cursor=cursor)
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
