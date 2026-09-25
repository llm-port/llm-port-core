"""A remote runtime is held to whether its endpoint answers.

It was marked running when it was added and never looked at again: the
runtimes list and the data residency map said "running" for an endpoint whose
host no longer resolved, and the gateway kept routing to it. Editing the
provider's endpoint did not reach the runtime or its route either.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any, Self

import httpx
import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.dao.llm_dao import ModelDAO, ProviderDAO, RuntimeDAO
from llm_port_backend.db.dao.rbac_dao import RbacDAO
from llm_port_backend.db.models.llm import (
    LLMProvider,
    LLMRuntime,
    ModelSource,
    ModelStatus,
    ProviderTarget,
    ProviderType,
    RuntimeStatus,
)
from llm_port_backend.db.models.users import User, current_active_user
from llm_port_backend.services.llm import remote_health
from llm_port_backend.services.llm.service import LLMService
from llm_port_backend.web.api.llm.dependencies import get_llm_service

pytestmark = pytest.mark.anyio


class _Gateway:
    """Records what the gateway is told."""

    enabled = True

    def __init__(self) -> None:
        self.health: list[tuple[uuid.UUID, str]] = []
        self.published: list[dict[str, Any]] = []

    async def set_instance_health(self, *, runtime_id: uuid.UUID, health_status: str) -> None:
        self.health.append((runtime_id, health_status))

    async def publish_runtime(self, **kwargs: Any) -> None:
        self.published.append(kwargs)


async def _remote_runtime(
    session: AsyncSession,
    url: str | None = "http://10.1.2.3:8000",
    *,
    litellm: str | None = None,
) -> tuple[LLMProvider, LLMRuntime]:
    provider = await ProviderDAO(session).create(
        name=f"remote-{uuid.uuid4().hex[:6]}",
        type_=ProviderType.VLLM,
        target=ProviderTarget.REMOTE_ENDPOINT,
        endpoint_url=url,
        litellm_provider=litellm,
    )
    model = await ModelDAO(session).create(
        f"m-{uuid.uuid4().hex[:6]}",
        ModelSource.REMOTE,
        status=ModelStatus.AVAILABLE,
    )
    runtime = await RuntimeDAO(session).create(f"rt-{uuid.uuid4().hex[:6]}", provider.id, model.id)
    runtime.execution_target = "remote"
    runtime.endpoint_url = url or f"litellm://{litellm}"
    runtime.status = RuntimeStatus.RUNNING
    await session.commit()
    return provider, runtime


def _answers(*results: bool) -> Any:
    """A probe that answers *results* in turn."""
    queue = list(results)

    async def _probe(url: str, _client: Any) -> remote_health.Probe:
        ok = queue.pop(0)
        return remote_health.Probe(reachable=ok, detail="HTTP 200" if ok else f"[Errno 11001] {url}")

    return _probe


# ── what is asked, and what counts as an answer ─────────────────────────


def test_what_each_kind_of_remote_is_asked() -> None:
    assert remote_health.probe_url("http://vllm.lan:8000", None) == "http://vllm.lan:8000"
    assert (
        remote_health.probe_url("litellm://anthropic", "anthropic") == "https://api.anthropic.com/v1/models"
    )
    assert remote_health.probe_url("litellm://someone", "someone") is None, "nothing known to ask"
    assert remote_health.probe_url(None, None) is None


async def test_any_answer_below_500_means_something_is_there() -> None:
    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "gone.invalid":
            raise httpx.ConnectError("[Errno 11001] getaddrinfo failed", request=request)
        return httpx.Response({"404": 404, "401": 401, "503": 503}[request.url.path.strip("/")])

    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler)) as client:
        assert (await remote_health.probe("http://vllm.lan/404", client)).reachable, (
            "a server without the path"
        )
        assert (await remote_health.probe("http://api.cloud/401", client)).reachable, (
            "a cloud API asked without a key"
        )
        assert not (await remote_health.probe("http://proxy.lan/503", client)).reachable
        gone = await remote_health.probe("http://gone.invalid/404", client)
        assert not gone.reachable and "getaddrinfo" in gone.detail


# ── down, and back ───────────────────────────────────────────────────────


async def test_a_remote_that_stops_answering_goes_down_and_comes_back(
    dbsession: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _provider, runtime = await _remote_runtime(dbsession)
    remote_health.forget(runtime.id)
    gateway = _Gateway()
    monkeypatch.setattr(remote_health, "probe", _answers(False, False, True))

    assert await remote_health.follow_remote_runtimes(dbsession, gateway) == 0, "one miss is not enough"
    await dbsession.refresh(runtime)
    assert runtime.status == RuntimeStatus.RUNNING
    assert gateway.health == []

    assert await remote_health.follow_remote_runtimes(dbsession, gateway) == 1
    await dbsession.refresh(runtime)
    assert runtime.status == RuntimeStatus.ERROR
    assert "11001" in (runtime.status_message or "")
    assert gateway.health == [(runtime.id, "unhealthy")], "out of routing"

    assert await remote_health.follow_remote_runtimes(dbsession, gateway) == 1
    await dbsession.refresh(runtime)
    assert (runtime.status, runtime.status_message) == (RuntimeStatus.RUNNING, None)
    assert gateway.health[-1] == (runtime.id, "healthy"), "back in routing on its first answer"


async def test_a_stopped_remote_is_left_alone(
    dbsession: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _provider, runtime = await _remote_runtime(dbsession)
    runtime.status = RuntimeStatus.STOPPED
    await dbsession.commit()
    monkeypatch.setattr(remote_health, "probe", _answers())  # any probe would fail the pop
    assert await remote_health.follow_remote_runtimes(dbsession, _Gateway()) == 0


# ── edits reach the routes ───────────────────────────────────────────────


async def test_an_edited_endpoint_reaches_the_runtime_and_its_route(dbsession: AsyncSession) -> None:
    provider, runtime = await _remote_runtime(dbsession)
    runtime.status = RuntimeStatus.ERROR  # down at the old address
    provider.endpoint_url = "http://10.9.9.9:8000"
    gateway = _Gateway()
    service = LLMService(SimpleNamespace(), gateway_sync=gateway)  # type: ignore[arg-type]

    assert await service.sync_remote_routes(RuntimeDAO(dbsession), provider) == 1
    assert runtime.endpoint_url == "http://10.9.9.9:8000"
    assert runtime.status == RuntimeStatus.RUNNING, "another chance at the new address"
    assert gateway.published[0]["base_url"] == "http://10.9.9.9:8000"
    assert gateway.published[0]["health_status"] == "healthy"


async def test_the_providers_api_sends_an_edit_through(
    client: AsyncClient,
    fastapi_app: FastAPI,
    dbsession: AsyncSession,
) -> None:
    rbac = RbacDAO(dbsession)
    await rbac.seed_defaults()
    admin = User(
        email=f"admin-{uuid.uuid4().hex}@test.local",
        hashed_password="x",
        is_verified=True,
        is_active=True,
        is_superuser=False,
    )
    dbsession.add(admin)
    await dbsession.flush()
    await rbac.assign_role(admin.id, (await rbac.get_role_by_name("admin")).id)
    fastapi_app.dependency_overrides[current_active_user] = lambda: admin
    gateway = _Gateway()
    fastapi_app.dependency_overrides[get_llm_service] = lambda: LLMService(
        SimpleNamespace(),
        gateway_sync=gateway,  # type: ignore[arg-type]
    )
    provider, runtime = await _remote_runtime(dbsession)

    said = await client.patch(
        f"/api/llm/providers/{provider.id}", json={"endpoint_url": "http://10.9.9.9:8000/v1/"}
    )
    assert said.status_code == 200, said.text
    assert said.json()["endpoint_url"] == "http://10.9.9.9:8000", "normalised as on create"
    assert said.json()["residency"]["kind"] == "private", "and placed where it now is"
    await dbsession.refresh(runtime)
    assert runtime.endpoint_url == "http://10.9.9.9:8000", "the runtime goes there too"
    assert gateway.published[-1]["base_url"] == "http://10.9.9.9:8000", "and so does its route"


# ── the reconciler ───────────────────────────────────────────────────────


async def test_found_containers_are_followed_when_no_cluster_has_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pass returned before following them when nothing else was pending."""
    from llm_port_backend.db.dao import inference_dao
    from llm_port_backend.web import lifespan

    class _Session:
        async def __aenter__(self) -> Self:
            return self

        async def __aexit__(self, *exc: object) -> bool:
            return False

        async def commit(self) -> None:
            return None

    async def _nothing(self: Any) -> list[Any]:
        return []

    followed: list[str] = []

    async def _found(_session: Any, _gateway: Any) -> None:
        followed.append("found")

    async def _remote(_session: Any, _gateway: Any) -> None:
        followed.append("remote")

    async def _queue(_session: Any) -> None:
        return None

    monkeypatch.setattr(inference_dao.EnvironmentDAO, "list_pending_observation", _nothing)
    monkeypatch.setattr(inference_dao.DeploymentDAO, "list_pending_observation", _nothing)
    monkeypatch.setattr(lifespan, "_follow_found_containers", _found)
    monkeypatch.setattr(lifespan, "_follow_remote_runtimes", _remote)
    monkeypatch.setattr(lifespan, "_queue_health_checks", _queue)
    app = SimpleNamespace(state=SimpleNamespace(db_session_factory=_Session))

    monkeypatch.setattr(lifespan, "_health_check_due", lambda: False)
    await lifespan._run_inference_reconcile_pass(app)
    assert followed == ["found"]

    monkeypatch.setattr(lifespan, "_health_check_due", lambda: True)
    await lifespan._run_inference_reconcile_pass(app)
    assert followed == ["found", "remote", "found"], "remotes are probed on the health interval"
