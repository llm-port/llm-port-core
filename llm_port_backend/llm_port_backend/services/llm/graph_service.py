"""Read model service for LLM topology and gateway trace graph data."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator, Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.dao.llm_dao import ModelDAO, ProviderDAO, RuntimeDAO
from llm_port_backend.web.api.llm.schema import (
    DataUsagePerInstanceDTO,
    DataUsageSummaryDTO,
    GraphEdgeDTO,
    GraphNodeDTO,
    TopologyResponseDTO,
    TraceEventDTO,
    TraceSnapshotResponseDTO,
)

TRACE_POLL_INTERVAL_SEC = 1.0
TRACE_PING_INTERVAL_SEC = 15.0
TRACE_DEFAULT_LIMIT = 100
TRACE_MAX_LIMIT = 500
log = logging.getLogger(__name__)


class ProviderOwners:
    """Which backend provider a gateway instance serves.

    A runtime is published under its own id. A cluster deployment and a found
    container are published under an id of the gateway's, with their source
    (``inference_deployment`` / ``found_container`` and the source's id) --
    and the provider they own carries that same source. Going by runtime ids
    alone, their traffic was nobody's.
    """

    def __init__(self, *, runtimes: list[Any], providers: list[Any]) -> None:
        self._by_runtime = {str(rt.id): str(rt.provider_id) for rt in runtimes}
        self._by_source = {
            (str(p.source_kind), str(p.source_id)): str(p.id)
            for p in providers
            if getattr(p, "source_kind", None) and getattr(p, "source_id", None)
        }

    def provider_for(
        self, instance_id: str, source_kind: str | None, source_id: str | None,
    ) -> str | None:
        """The provider id, or None for an instance whose provider is gone."""
        found = self._by_runtime.get(instance_id)
        if found is None and source_kind and source_id:
            found = self._by_source.get((source_kind, source_id))
        return found


class LLMGraphService:
    """Builds graph DTOs for topology and live traces."""

    def __init__(
        self,
        provider_dao: ProviderDAO,
        runtime_dao: RuntimeDAO,
        model_dao: ModelDAO,
        trace_session_factory: Callable[[], AsyncSession] | None = None,
    ) -> None:
        self._provider_dao = provider_dao
        self._runtime_dao = runtime_dao
        self._model_dao = model_dao
        self._trace_session_factory = trace_session_factory

    async def get_topology(self) -> TopologyResponseDTO:
        """Return provider -> runtime -> model topology."""
        providers = await self._provider_dao.list_all()
        runtimes = await self._runtime_dao.list_all()
        models = await self._model_dao.list_all()

        # Build a set of model IDs referenced by runtimes so we can filter
        # out orphan models that would create disconnected nodes.
        referenced_model_ids = {str(rt.model_id) for rt in runtimes}

        nodes: list[GraphNodeDTO] = []
        edges: list[GraphEdgeDTO] = []

        for provider in providers:
            provider_id = str(provider.id)
            nodes.append(
                GraphNodeDTO(
                    id=f"provider:{provider_id}",
                    type="provider",
                    label=provider.name,
                    status="enabled",
                    meta={"provider_type": provider.type.value, "target": provider.target.value},
                ),
            )

        for runtime in runtimes:
            runtime_id = str(runtime.id)
            provider_id = str(runtime.provider_id)
            model_id = str(runtime.model_id)
            nodes.append(
                GraphNodeDTO(
                    id=f"runtime:{runtime_id}",
                    type="runtime",
                    label=runtime.name,
                    status=runtime.status.value,
                    meta={
                        "endpoint_url": runtime.endpoint_url,
                        "openai_compat": runtime.openai_compat,
                    },
                ),
            )
            edges.append(
                GraphEdgeDTO(
                    id=f"provider-runtime:{provider_id}:{runtime_id}",
                    source=f"provider:{provider_id}",
                    target=f"runtime:{runtime_id}",
                    type="provider_runtime",
                ),
            )
            edges.append(
                GraphEdgeDTO(
                    id=f"runtime-model:{runtime_id}:{model_id}",
                    source=f"runtime:{runtime_id}",
                    target=f"model:{model_id}",
                    type="runtime_model",
                ),
            )

        for model in models:
            model_id = str(model.id)
            # Skip models not linked to any runtime – they would appear as
            # disconnected nodes with dangling edges.
            if model_id not in referenced_model_ids:
                continue
            nodes.append(
                GraphNodeDTO(
                    id=f"model:{model_id}",
                    type="model",
                    label=model.display_name,
                    status=model.status.value,
                    meta={
                        "source": model.source.value,
                        "hf_repo_id": model.hf_repo_id,
                    },
                ),
            )

        return TopologyResponseDTO(
            generated_at=datetime.now(tz=UTC),
            nodes=nodes,
            edges=edges,
        )

    async def list_recent_traces(
        self,
        limit: int = TRACE_DEFAULT_LIMIT,
        after_event_id: int | None = None,
    ) -> TraceSnapshotResponseDTO:
        """Return a bounded list of trace events for graph bootstrap/polling."""
        bounded = max(1, min(limit, TRACE_MAX_LIMIT))
        events = await self._fetch_traces(limit=bounded, after_event_id=after_event_id)
        next_cursor = (
            str(events[-1].event_id)
            if events
            else (str(after_event_id) if after_event_id is not None else None)
        )
        return TraceSnapshotResponseDTO(items=events, next_cursor=next_cursor)

    async def get_data_usage(self) -> DataUsageSummaryDTO:
        """Aggregate token/request usage per provider instance from gateway logs.

        Each instance is attributed to the provider it serves (``provider_id``).
        """
        if self._trace_session_factory is None:
            return DataUsageSummaryDTO(
                generated_at=datetime.now(tz=UTC),
                instances=[],
            )

        # The instance's source comes along: a cluster deployment or a found
        # container is known to the gateway by its source, not by a runtime.
        query = """
            SELECT
                l.provider_instance_id,
                MAX(pi.source_kind)                    AS source_kind,
                MAX(CAST(pi.source_id AS TEXT))        AS source_id,
                COUNT(*)                               AS total_requests,
                COALESCE(SUM(l.prompt_tokens), 0)      AS total_prompt_tokens,
                COALESCE(SUM(l.completion_tokens), 0)  AS total_completion_tokens,
                COALESCE(SUM(l.total_tokens), 0)       AS total_tokens,
                SUM(CASE WHEN l.status_code >= 400 OR l.error_code IS NOT NULL THEN 1 ELSE 0 END)
                                                       AS error_count
            FROM llm_gateway_request_log l
            LEFT JOIN llm_provider_instance pi ON pi.id = l.provider_instance_id
            WHERE l.provider_instance_id IS NOT NULL
            GROUP BY l.provider_instance_id
        """
        try:
            async with self._trace_session_factory() as session:
                result = await session.execute(text(query))
                rows = list(result.mappings().all())
        except SQLAlchemyError:
            log.exception("Failed to query gateway data-usage.")
            return DataUsageSummaryDTO(
                generated_at=datetime.now(tz=UTC),
                instances=[],
            )

        owners = ProviderOwners(
            runtimes=await self._runtime_dao.list_all(),
            providers=await self._provider_dao.list_all(),
        )
        instances: list[DataUsagePerInstanceDTO] = []
        grand_requests = 0
        grand_tokens = 0
        for row in rows:
            inst = DataUsagePerInstanceDTO(
                provider_instance_id=str(row["provider_instance_id"]),
                provider_id=owners.provider_for(
                    str(row["provider_instance_id"]), row["source_kind"], row["source_id"],
                ),
                total_requests=int(row["total_requests"]),
                total_prompt_tokens=int(row["total_prompt_tokens"]),
                total_completion_tokens=int(row["total_completion_tokens"]),
                total_tokens=int(row["total_tokens"]),
                error_count=int(row["error_count"]),
            )
            instances.append(inst)
            grand_requests += inst.total_requests
            grand_tokens += inst.total_tokens

        return DataUsageSummaryDTO(
            generated_at=datetime.now(tz=UTC),
            instances=instances,
            grand_total_requests=grand_requests,
            grand_total_tokens=grand_tokens,
        )

    async def stream_traces(
        self,
        cursor: int | None = None,
        poll_interval_sec: float = TRACE_POLL_INTERVAL_SEC,
        ping_interval_sec: float = TRACE_PING_INTERVAL_SEC,
    ) -> AsyncGenerator[str]:
        """Yield SSE frames for trace events with ping keep-alives."""
        last_event_id = cursor
        last_ping = asyncio.get_running_loop().time()
        while True:
            snapshot = await self.list_recent_traces(limit=TRACE_DEFAULT_LIMIT, after_event_id=last_event_id)
            if snapshot.items:
                for item in snapshot.items:
                    payload = item.model_dump_json()
                    yield f"id: {item.event_id}\nevent: trace\ndata: {payload}\n\n"
                    last_event_id = item.event_id
                last_ping = asyncio.get_running_loop().time()
            else:
                now = asyncio.get_running_loop().time()
                if now - last_ping >= ping_interval_sec:
                    yield f"event: ping\ndata: {json.dumps({'ts': datetime.now(tz=UTC).isoformat()})}\n\n"
                    last_ping = now
            await asyncio.sleep(poll_interval_sec)

    async def _fetch_traces(
        self,
        *,
        limit: int,
        after_event_id: int | None,
    ) -> list[TraceEventDTO]:
        if self._trace_session_factory is None:
            return []

        # Derive a timestamp lower-bound from the composite event_id so we
        # can push the filter into SQL rather than fetching everything and
        # discarding in Python.
        params: dict[str, object] = {"limit": limit}
        where_clause = ""
        if after_event_id is not None:
            cursor_ts = _timestamp_from_event_id(after_event_id)
            if cursor_ts is not None:
                where_clause = "WHERE created_at >= :cursor_ts"
                params["cursor_ts"] = cursor_ts

        query = f"""
            SELECT
                id,
                created_at,
                request_id,
                trace_id,
                tenant_id,
                user_id,
                model_alias,
                provider_instance_id,
                status_code,
                latency_ms,
                ttft_ms,
                prompt_tokens,
                completion_tokens,
                total_tokens,
                error_code
            FROM llm_gateway_request_log
            {where_clause}
            ORDER BY created_at DESC, id DESC
            LIMIT :limit
        """

        try:
            async with self._trace_session_factory() as session:
                result = await session.execute(text(query), params)
                rows = list(result.mappings().all())
        except SQLAlchemyError:
            log.exception("Failed to query llm gateway trace table.")
            return []

        rows.reverse()
        events: list[TraceEventDTO] = []
        for row in rows:
            if row["id"] is None:
                continue
            event_id = _event_id(row["created_at"], str(row["id"]))
            # Final exact filter – the SQL WHERE is an approximate lower
            # bound; this ensures no duplicates across cursor boundaries.
            if after_event_id is not None and event_id <= after_event_id:
                continue
            events.append(
                TraceEventDTO(
                    event_id=event_id,
                    ts=row["created_at"],
                    request_id=row["request_id"],
                    trace_id=row["trace_id"],
                    tenant_id=row["tenant_id"],
                    user_id=row["user_id"],
                    model_alias=row["model_alias"],
                    provider_instance_id=(
                        str(row["provider_instance_id"]) if row["provider_instance_id"] is not None else None
                    ),
                    status=row["status_code"],
                    latency_ms=row["latency_ms"],
                    ttft_ms=row["ttft_ms"],
                    prompt_tokens=row["prompt_tokens"],
                    completion_tokens=row["completion_tokens"],
                    total_tokens=row["total_tokens"],
                    error_code=row["error_code"],
                ),
            )
        return events


def _event_id(timestamp: datetime, row_id: str) -> int:
    """Build stable event id for SSE cursor semantics."""
    micros = int(timestamp.timestamp() * 1_000_000)
    suffix = int(row_id.replace("-", "")[-6:], 16)
    return micros * 1_000_000 + suffix


def _timestamp_from_event_id(event_id: int) -> datetime | None:
    """Extract approximate timestamp from a composite event_id.

    Returns a datetime suitable for a ``WHERE created_at >= :ts`` clause.
    The suffix portion is discarded, so results should still be
    de-duplicated with the exact ``event_id <=`` check in Python.
    """
    try:
        micros = event_id // 1_000_000
        return datetime.fromtimestamp(micros / 1_000_000, tz=UTC)
    except (OSError, ValueError, OverflowError):
        return None
