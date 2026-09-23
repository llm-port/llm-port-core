"""The logs page's filters come from the labels the logs actually carry.

They were five hard-coded names with values loaded across all time, so a
model's logs -- whose streams carry ``app`` / ``deployment`` / ``replica`` and
no ``container`` -- could not be filtered, and choosing a machine left every
other filter offering values from every other machine.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from llm_port_backend.web.api.logs import views
from llm_port_backend.web.api.logs.views import _selector

LOKI_LABELS = ["__stream_shard__", "container_id", "host", "job", "app", "level"]
LOKI_VALUES = {
    "host": ["10.88.10.49", "10.88.10.71"],
    "job": ["docker", "node-agent", "ray-serve"],
    "app": ["llmport-e783c0c2"],
    "level": ["info", "error"],
}


@pytest.fixture
def loki(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, str]]]:
    asked: list[tuple[str, dict[str, str]]] = []

    async def fake(path: str, params: dict[str, str] | None = None, timeout: float = 10.0) -> dict[str, Any]:
        asked.append((path, dict(params or {})))
        if path == "/loki/api/v1/labels":
            return {"data": LOKI_LABELS}
        name = path.split("/")[-2]
        return {"data": LOKI_VALUES.get(name, [])}

    monkeypatch.setattr(views, "_request_loki_json", fake)
    return asked


@pytest.fixture
def signed_in(fastapi_app: FastAPI) -> Any:
    """An administrator, as in the other RBAC-guarded endpoint tests."""
    import uuid
    from unittest.mock import AsyncMock, MagicMock

    from llm_port_backend.db.dao.rbac_dao import RbacDAO
    from llm_port_backend.db.models.users import User, current_active_user

    user = MagicMock(spec=User)
    user.id = uuid.uuid4()
    user.is_active = user.is_superuser = user.is_verified = True
    dao = MagicMock(spec=RbacDAO)
    dao.has_permission = AsyncMock(return_value=True)
    fastapi_app.dependency_overrides[current_active_user] = lambda: user
    fastapi_app.dependency_overrides[RbacDAO] = lambda: dao
    yield
    fastapi_app.dependency_overrides.pop(current_active_user, None)
    fastapi_app.dependency_overrides.pop(RbacDAO, None)


def test_the_selector_leaves_out_the_label_being_listed() -> None:
    selected = {"host": "10.88.10.71", "job": "ray-serve"}
    assert _selector(selected, excluding="job") == '{host="10.88.10.71"}'
    assert _selector(selected) == '{host="10.88.10.71",job="ray-serve"}'
    assert _selector({"a": 'x"y'}) == '{a="x\\"y"}'
    assert _selector({}) is None


@pytest.mark.anyio()
async def test_every_label_in_the_range_comes_back_with_its_values(
    client: AsyncClient, loki: list, signed_in: Any,
) -> None:
    response = await client.get("/api/logs/filters", params={"start": "1000", "end": "2000"})

    assert response.status_code == 200
    labels = response.json()["labels"]
    assert set(labels) == {"host", "job", "app", "level"}, "Loki's internal labels are left out"
    assert labels["job"] == ["docker", "node-agent", "ray-serve"]
    assert all(params.get("start") == "1000" and params.get("end") == "2000" for _, params in loki)


@pytest.mark.anyio()
async def test_choosing_a_machine_narrows_the_others_but_not_itself(
    client: AsyncClient, loki: list, signed_in: Any,
) -> None:
    response = await client.get(
        "/api/logs/filters", params=[("sel", "host:10.88.10.71"), ("sel", "job:ray-serve")],
    )

    assert response.status_code == 200
    scopes = {path.split("/")[-2]: params.get("query") for path, params in loki if "/label/" in path}
    assert scopes["level"] == '{host="10.88.10.71",job="ray-serve"}'
    assert scopes["host"] == '{job="ray-serve"}', "a machine can still be switched"
    assert scopes["job"] == '{host="10.88.10.71"}'


@pytest.mark.anyio()
async def test_a_label_name_cannot_inject_a_query(client: AsyncClient, loki: list, signed_in: Any) -> None:
    response = await client.get("/api/logs/filters", params={"sel": 'host"}|x:1'})
    assert response.status_code == 400
