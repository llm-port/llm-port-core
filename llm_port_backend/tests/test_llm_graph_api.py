"""Tests for LLM graph API endpoints."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from starlette import status

from llm_port_backend.db.models.users import User, current_active_user
from llm_port_backend.web.api.llm.dependencies import get_llm_graph_service
from llm_port_backend.web.api.llm.schema import (
    GraphEdgeDTO,
    GraphNodeDTO,
    TopologyResponseDTO,
    TraceEventDTO,
    TraceSnapshotResponseDTO,
)


def _make_user(*, superuser: bool) -> User:
    user = MagicMock(spec=User)
    user.id = uuid.uuid4()
    user.is_active = True
    user.is_superuser = superuser
    user.is_verified = True
    return user


class _FakeGraphService:
    def __init__(self) -> None:
        self.cursor_seen: int | None = None

    async def get_topology(self) -> TopologyResponseDTO:
        now = datetime.now(tz=UTC)
        return TopologyResponseDTO(
            generated_at=now,
            nodes=[
                GraphNodeDTO(id="provider:p1", type="provider", label="provider-1", status="enabled"),
                GraphNodeDTO(id="runtime:r1", type="runtime", label="runtime-1", status="running"),
            ],
            edges=[
                GraphEdgeDTO(
                    id="provider-runtime:p1:r1",
                    source="provider:p1",
                    target="runtime:r1",
                    type="provider_runtime",
                ),
            ],
        )

    async def list_recent_traces(
        self,
        limit: int = 100,
        after_event_id: int | None = None,
    ) -> TraceSnapshotResponseDTO:
        now = datetime.now(tz=UTC)
        event = TraceEventDTO(
            event_id=42,
            ts=now,
            request_id="req-1",
            trace_id="trace-1",
            tenant_id="tenant-1",
            user_id="user-1",
            model_alias="gpt-test",
            provider_instance_id="provider-1",
            status=200,
            latency_ms=123,
            ttft_ms=20,
            prompt_tokens=10,
            completion_tokens=15,
            total_tokens=25,
            error_code=None,
        )
        if after_event_id is not None and after_event_id >= 42:
            return TraceSnapshotResponseDTO(items=[], next_cursor=str(after_event_id))
        return TraceSnapshotResponseDTO(items=[event], next_cursor="42")

    async def stream_traces(
        self,
        cursor: int | None = None,
        poll_interval_sec: float = 1.0,
        ping_interval_sec: float = 15.0,
    ) -> AsyncGenerator[str]:
        del poll_interval_sec, ping_interval_sec
        self.cursor_seen = cursor
        yield 'id: 42\nevent: trace\ndata: {"event_id":42}\n\n'


@pytest.fixture()
def authed_app(fastapi_app: FastAPI) -> FastAPI:
    graph_service = _FakeGraphService()
    fastapi_app.dependency_overrides[current_active_user] = lambda: _make_user(superuser=True)
    fastapi_app.dependency_overrides[get_llm_graph_service] = lambda: graph_service
    return fastapi_app


async def test_graph_requires_auth(client: AsyncClient, fastapi_app: FastAPI) -> None:
    fastapi_app.dependency_overrides.pop(current_active_user, None)
    url = fastapi_app.url_path_for("get_graph_topology")
    response = await client.get(url)
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


async def test_graph_forbidden_without_permission(client: AsyncClient, fastapi_app: FastAPI) -> None:
    fastapi_app.dependency_overrides[current_active_user] = lambda: _make_user(superuser=False)
    url = fastapi_app.url_path_for("get_graph_topology")
    response = await client.get(url)
    assert response.status_code == status.HTTP_403_FORBIDDEN


async def test_graph_topology_success(client: AsyncClient, authed_app: FastAPI) -> None:
    url = authed_app.url_path_for("get_graph_topology")
    response = await client.get(url)
    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert len(body["nodes"]) == 2
    assert len(body["edges"]) == 1


async def test_graph_traces_snapshot(client: AsyncClient, authed_app: FastAPI) -> None:
    url = authed_app.url_path_for("get_recent_traces")
    response = await client.get(url, params={"limit": 100})
    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["next_cursor"] == "42"
    assert body["items"][0]["event_id"] == 42


async def test_graph_stream_respects_last_event_id_header(client: AsyncClient, authed_app: FastAPI) -> None:
    service = authed_app.dependency_overrides[get_llm_graph_service]()  # type: ignore[call-arg]
    url = authed_app.url_path_for("stream_traces")
    async with client.stream("GET", url, headers={"Last-Event-ID": "41"}) as response:
        assert response.status_code == status.HTTP_200_OK
        text = await response.aread()
        assert b"event: trace" in text
    assert service.cursor_seen == 41
