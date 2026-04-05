"""Admin endpoints for Observability — cost dashboard + pricing editor.

Queries the gateway database (``llm_api``) via the secondary engine
set up in ``app.state.llm_graph_trace_session_factory``, using raw SQL
to avoid coupling with the gateway's ORM models.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.models.users import User
from llm_port_backend.services.observability.pricing_service import PricingCRUDService
from llm_port_backend.services.observability.service import ObservabilityService
from llm_port_backend.web.api.admin.observability.schema import (
    PaginatedRequestsDTO,
    PerformanceDTO,
    PricingCreateDTO,
    PricingEntryDTO,
    PricingUpdateDTO,
    RequestLogDTO,
    SessionCostDTO,
    SummaryDTO,
    TimeseriesBucketDTO,
    ToolCallLogDTO,
)
from llm_port_backend.web.api.rbac import require_permission

logger = logging.getLogger(__name__)

router = APIRouter()

MAX_RANGE_DAYS = 90


# ── Dependencies ──────────────────────────────────────────────────


async def _get_gateway_session(request: Request) -> AsyncGenerator[AsyncSession, None]:
    """Yield a session against the gateway (llm_api) database."""
    factory = getattr(request.app.state, "llm_graph_trace_session_factory", None)
    if factory is None:
        raise HTTPException(status_code=503, detail="Gateway database not available")
    async with factory() as session:
        yield session


def _parse_range(
    start: datetime | None,
    end: datetime | None,
) -> tuple[datetime, datetime]:
    """Validate and clamp the time range."""
    now = datetime.now(timezone.utc)
    if end is None:
        end = now
    if start is None:
        start = end - timedelta(days=7)
    if (end - start).days > MAX_RANGE_DAYS:
        raise HTTPException(
            status_code=400,
            detail=f"Time range cannot exceed {MAX_RANGE_DAYS} days",
        )
    return start, end


# ── Summary ───────────────────────────────────────────────────────


@router.get("/summary", response_model=SummaryDTO, name="observability_summary")
async def get_summary(
    _user: Annotated[User, Depends(require_permission("observability", "read"))],
    gw: AsyncSession = Depends(_get_gateway_session),
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
) -> SummaryDTO:
    start, end = _parse_range(start, end)
    svc = ObservabilityService(gw)
    data = await svc.get_summary(start, end)
    return SummaryDTO(**data)


# ── Timeseries ────────────────────────────────────────────────────


@router.get(
    "/timeseries",
    response_model=list[TimeseriesBucketDTO],
    name="observability_timeseries",
)
async def get_timeseries(
    _user: Annotated[User, Depends(require_permission("observability", "read"))],
    gw: AsyncSession = Depends(_get_gateway_session),
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    granularity: str = Query(default="day"),
) -> list[TimeseriesBucketDTO]:
    start, end = _parse_range(start, end)
    svc = ObservabilityService(gw)
    rows = await svc.get_timeseries(start, end, granularity)
    return [TimeseriesBucketDTO(**r) for r in rows]


# ── Performance ───────────────────────────────────────────────────


@router.get(
    "/performance",
    response_model=PerformanceDTO,
    name="observability_performance",
)
async def get_performance(
    _user: Annotated[User, Depends(require_permission("observability", "read"))],
    gw: AsyncSession = Depends(_get_gateway_session),
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
) -> PerformanceDTO:
    start, end = _parse_range(start, end)
    svc = ObservabilityService(gw)
    data = await svc.get_performance(start, end)
    return PerformanceDTO(**data)


# ── Request list ──────────────────────────────────────────────────


@router.get(
    "/requests",
    response_model=PaginatedRequestsDTO,
    name="observability_requests",
)
async def list_requests(
    _user: Annotated[User, Depends(require_permission("observability", "read"))],
    gw: AsyncSession = Depends(_get_gateway_session),
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=50, ge=1, le=500),
    model_alias: str | None = Query(default=None),
    user_id: str | None = Query(default=None),
    status_code: int | None = Query(default=None),
) -> PaginatedRequestsDTO:
    start, end = _parse_range(start, end)
    svc = ObservabilityService(gw)
    data = await svc.get_requests(
        start, end, page=page, limit=limit,
        model_alias=model_alias, user_id=user_id, status_code=status_code,
    )
    return PaginatedRequestsDTO(**data)


# ── Request detail ────────────────────────────────────────────────


@router.get(
    "/requests/{request_id}",
    response_model=RequestLogDTO,
    name="observability_request_detail",
)
async def get_request_detail(
    request_id: str,
    _user: Annotated[User, Depends(require_permission("observability", "read"))],
    gw: AsyncSession = Depends(_get_gateway_session),
) -> RequestLogDTO:
    svc = ObservabilityService(gw)
    data = await svc.get_request_detail(request_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Request not found")
    return RequestLogDTO(**data)


# ── Request detail by trace_id ────────────────────────────────


@router.get(
    "/requests/by-trace/{trace_id}",
    response_model=RequestLogDTO,
    name="observability_request_by_trace",
)
async def get_request_by_trace(
    trace_id: str,
    _user: Annotated[User, Depends(require_permission("chat.debug", "read"))],
    gw: AsyncSession = Depends(_get_gateway_session),
) -> RequestLogDTO:
    svc = ObservabilityService(gw)
    data = await svc.get_request_by_trace_id(trace_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Request not found for trace_id")
    return RequestLogDTO(**data)


# ── Tool call logs ─────────────────────────────────────────────


@router.get(
    "/requests/{request_id}/tool-calls",
    response_model=list[ToolCallLogDTO],
    name="observability_tool_calls",
)
async def get_tool_calls(
    request_id: str,
    _user: Annotated[User, Depends(require_permission("observability", "read"))],
    gw: AsyncSession = Depends(_get_gateway_session),
) -> list[ToolCallLogDTO]:
    svc = ObservabilityService(gw)
    data = await svc.get_tool_calls(request_id)
    return [ToolCallLogDTO(**row) for row in data]


# ── Session cost ──────────────────────────────────────────────────


@router.get(
    "/sessions/{session_id}",
    response_model=SessionCostDTO,
    name="observability_session_cost",
)
async def get_session_cost(
    session_id: str,
    _user: Annotated[User, Depends(require_permission("observability", "read"))],
    gw: AsyncSession = Depends(_get_gateway_session),
) -> SessionCostDTO:
    svc = ObservabilityService(gw)
    data = await svc.get_session_cost(session_id)
    return SessionCostDTO(**data)


# ── CSV export ────────────────────────────────────────────────────


@router.get("/export.csv", name="observability_export_csv")
async def export_csv(
    _user: Annotated[User, Depends(require_permission("observability", "read"))],
    gw: AsyncSession = Depends(_get_gateway_session),
    start: datetime | None = Query(default=None),
    end: datetime | None = Query(default=None),
) -> StreamingResponse:
    start, end = _parse_range(start, end)
    svc = ObservabilityService(gw)

    async def _stream():
        async for chunk in svc.export_csv(start, end):
            yield chunk

    return StreamingResponse(
        _stream(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=observability_export.csv"},
    )


# ── Model names (autocomplete) ────────────────────────────────────


@router.get(
    "/model-names",
    response_model=list[str],
    name="observability_model_names",
)
async def list_model_names(
    _user: Annotated[User, Depends(require_permission("observability", "read"))],
    gw: AsyncSession = Depends(_get_gateway_session),
    q: str = Query(default="", max_length=200),
) -> list[str]:
    svc = ObservabilityService(gw)
    return await svc.get_model_names(q)


# ── Provider names (autocomplete) ─────────────────────────────────


@router.get(
    "/provider-names",
    response_model=list[str],
    name="observability_provider_names",
)
async def list_provider_names(
    _user: Annotated[User, Depends(require_permission("observability", "read"))],
    gw: AsyncSession = Depends(_get_gateway_session),
    q: str = Query(default="", max_length=200),
) -> list[str]:
    svc = ObservabilityService(gw)
    return await svc.get_provider_names(q)


# ── Force-recalculate costs ───────────────────────────────────────


@router.post(
    "/recalculate-costs",
    name="observability_recalculate_costs",
)
async def recalculate_costs(
    _user: Annotated[User, Depends(require_permission("observability", "write"))],
    gw: AsyncSession = Depends(_get_gateway_session),
) -> dict:
    """Recalculate cost estimates for all request log rows using current pricing."""
    svc = ObservabilityService(gw)
    return await svc.recalculate_costs()


# ── Pricing CRUD ──────────────────────────────────────────────────


@router.get(
    "/pricing",
    response_model=list[PricingEntryDTO],
    name="observability_list_pricing",
)
async def list_pricing(
    _user: Annotated[User, Depends(require_permission("observability", "read"))],
    gw: AsyncSession = Depends(_get_gateway_session),
) -> list[PricingEntryDTO]:
    svc = PricingCRUDService(gw)
    rows = await svc.list_active()
    return [PricingEntryDTO(**r) for r in rows]


@router.post(
    "/pricing",
    response_model=PricingEntryDTO,
    status_code=201,
    name="observability_create_pricing",
)
async def create_pricing(
    body: PricingCreateDTO,
    _user: Annotated[User, Depends(require_permission("observability", "write"))],
    gw: AsyncSession = Depends(_get_gateway_session),
) -> PricingEntryDTO:
    svc = PricingCRUDService(gw)
    row = await svc.create(
        provider=body.provider,
        model=body.model,
        input_price_per_1k=body.input_price_per_1k,
        output_price_per_1k=body.output_price_per_1k,
        currency=body.currency,
        notes=body.notes,
    )
    return PricingEntryDTO(**row)


@router.put(
    "/pricing/{entry_id}",
    response_model=PricingEntryDTO,
    name="observability_update_pricing",
)
async def update_pricing(
    entry_id: str,
    body: PricingUpdateDTO,
    _user: Annotated[User, Depends(require_permission("observability", "write"))],
    gw: AsyncSession = Depends(_get_gateway_session),
) -> PricingEntryDTO:
    svc = PricingCRUDService(gw)
    try:
        row = await svc.update(
            entry_id=entry_id,
            input_price_per_1k=body.input_price_per_1k,
            output_price_per_1k=body.output_price_per_1k,
            currency=body.currency,
            notes=body.notes,
        )
    except ValueError:
        raise HTTPException(status_code=404, detail="Active price entry not found")
    return PricingEntryDTO(**row)


@router.delete(
    "/pricing/{entry_id}",
    status_code=204,
    name="observability_deactivate_pricing",
)
async def deactivate_pricing(
    entry_id: str,
    _user: Annotated[User, Depends(require_permission("observability", "write"))],
    gw: AsyncSession = Depends(_get_gateway_session),
) -> None:
    svc = PricingCRUDService(gw)
    try:
        await svc.deactivate(entry_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Active price entry not found")


@router.get(
    "/pricing/{provider}/{model}/history",
    response_model=list[PricingEntryDTO],
    name="observability_pricing_history",
)
async def pricing_history(
    provider: str,
    model: str,
    _user: Annotated[User, Depends(require_permission("observability", "read"))],
    gw: AsyncSession = Depends(_get_gateway_session),
) -> list[PricingEntryDTO]:
    svc = PricingCRUDService(gw)
    rows = await svc.get_history(provider, model)
    return [PricingEntryDTO(**r) for r in rows]


# ── EE stubs (402) ────────────────────────────────────────────────


_EE_DETAIL = {
    "detail": "This feature requires LLM.port Enterprise Edition",
    "upgrade_url": "https://llmport.com/pricing",
}


@router.post("/budgets", status_code=402, name="observability_budgets_stub")
async def budgets_stub(
    _user: Annotated[User, Depends(require_permission("observability", "read"))],
) -> dict:
    return _EE_DETAIL


@router.get("/forecast", status_code=402, name="observability_forecast_stub")
async def forecast_stub(
    _user: Annotated[User, Depends(require_permission("observability", "read"))],
) -> dict:
    return _EE_DETAIL


@router.post("/alerts", status_code=402, name="observability_alerts_stub")
async def alerts_stub(
    _user: Annotated[User, Depends(require_permission("observability", "read"))],
) -> dict:
    return _EE_DETAIL


@router.get("/chargeback", status_code=402, name="observability_chargeback_stub")
async def chargeback_stub(
    _user: Annotated[User, Depends(require_permission("observability", "read"))],
) -> dict:
    return _EE_DETAIL
