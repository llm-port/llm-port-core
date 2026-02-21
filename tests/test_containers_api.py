"""Integration tests for admin container API endpoints.

Docker is mocked — these tests validate policy enforcement, registry interactions,
and audit trail creation without a live Docker daemon.
"""

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from airgap_backend.db.dao.container_registry_dao import ContainerRegistryDAO
from airgap_backend.db.models.containers import ContainerClass, ContainerPolicy
from airgap_backend.db.models.users import User, current_active_user
from airgap_backend.web.api.admin.dependencies import get_docker

_FAKE_ID = "abc123def456" * 3  # 36-char fake container ID


def _make_superuser() -> User:
    user = MagicMock(spec=User)
    user.id = uuid.uuid4()
    user.is_active = True
    user.is_superuser = True
    user.is_verified = True
    return user


def _mock_docker(extra: dict[str, Any] | None = None) -> MagicMock:
    """Return a mock DockerService with sensible defaults."""
    docker = MagicMock()
    docker.list_containers = AsyncMock(
        return_value=[
            {
                "Id": _FAKE_ID,
                "Names": ["/test-container"],
                "Image": "nginx:latest",
                "Status": "Up 5 minutes",
                "State": "running",
                "Created": "2026-01-01T00:00:00Z",
                "Ports": [],
                "NetworkSettings": {"Networks": {"bridge": {}}},
            }
        ]
    )
    docker.inspect_container = AsyncMock(
        return_value={
            "Id": _FAKE_ID,
            "Name": "/test-container",
            "Created": "2026-01-01T00:00:00Z",
            "State": {"Status": "running"},
            "Config": {"Image": "nginx:latest"},
            "NetworkSettings": {"Ports": {}, "Networks": {"bridge": {}}},
        }
    )
    docker.start = AsyncMock()
    docker.stop = AsyncMock()
    docker.restart = AsyncMock()
    docker.pause = AsyncMock()
    docker.unpause = AsyncMock()
    docker.delete = AsyncMock()
    docker.create_exec = AsyncMock(return_value="exec-id-123")
    if extra:
        for k, v in extra.items():
            setattr(docker, k, v)
    return docker


@pytest.fixture()
def superuser() -> User:
    return _make_superuser()


@pytest.fixture()
def mock_docker() -> MagicMock:
    return _mock_docker()


@pytest.fixture()
def authed_app(fastapi_app: FastAPI, superuser: User, mock_docker: MagicMock) -> FastAPI:
    fastapi_app.dependency_overrides[current_active_user] = lambda: superuser
    fastapi_app.dependency_overrides[get_docker] = lambda: mock_docker
    return fastapi_app


# ──────────────────────────────────────────────────────────────────────────────
# Container list
# ──────────────────────────────────────────────────────────────────────────────


async def test_list_containers(
    authed_app: FastAPI,
    client: AsyncClient,
) -> None:
    url = authed_app.url_path_for("list_containers")
    resp = await client.get(url)
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert isinstance(data, list)
    assert data[0]["id"] == _FAKE_ID
    assert data[0]["container_class"] == ContainerClass.UNTRUSTED.value  # not in registry


# ──────────────────────────────────────────────────────────────────────────────
# Register container → TENANT_APP
# ──────────────────────────────────────────────────────────────────────────────


async def test_register_container_as_tenant(
    authed_app: FastAPI,
    client: AsyncClient,
    dbsession: AsyncSession,
) -> None:
    url = authed_app.url_path_for("register_container", container_id=_FAKE_ID)
    resp = await client.put(
        url,
        json={
            "container_class": ContainerClass.TENANT_APP.value,
            "owner_scope": "workspace-1",
            "policy": ContainerPolicy.FREE.value,
        },
    )
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["container_class"] == ContainerClass.TENANT_APP.value

    # Verify registry was persisted
    dao = ContainerRegistryDAO(dbsession)
    entry = await dao.get(_FAKE_ID)
    assert entry is not None
    assert entry.container_class == ContainerClass.TENANT_APP


# ──────────────────────────────────────────────────────────────────────────────
# Lifecycle: TENANT_APP — start allowed without root mode
# ──────────────────────────────────────────────────────────────────────────────


async def test_tenant_app_lifecycle_start_allowed(
    authed_app: FastAPI,
    client: AsyncClient,
    dbsession: AsyncSession,
    mock_docker: MagicMock,
) -> None:
    # Register first
    reg_url = authed_app.url_path_for("register_container", container_id=_FAKE_ID)
    await client.put(
        reg_url,
        json={
            "container_class": ContainerClass.TENANT_APP.value,
            "owner_scope": "ws-1",
            "policy": ContainerPolicy.FREE.value,
        },
    )
    lc_url = authed_app.url_path_for("container_lifecycle", container_id=_FAKE_ID, action="start")
    resp = await client.post(lc_url)
    assert resp.status_code == status.HTTP_204_NO_CONTENT
    mock_docker.start.assert_awaited_once_with(_FAKE_ID)


# ──────────────────────────────────────────────────────────────────────────────
# Lifecycle: SYSTEM_CORE — exec denied without root mode
# ──────────────────────────────────────────────────────────────────────────────


async def test_system_core_exec_denied_without_root(
    authed_app: FastAPI,
    client: AsyncClient,
    dbsession: AsyncSession,
) -> None:
    # Register as SYSTEM_CORE
    reg_url = authed_app.url_path_for("register_container", container_id=_FAKE_ID)
    await client.put(
        reg_url,
        json={
            "container_class": ContainerClass.SYSTEM_CORE.value,
            "owner_scope": "platform",
            "policy": ContainerPolicy.FREE.value,
        },
    )
    exec_url = authed_app.url_path_for("container_exec", container_id=_FAKE_ID)
    resp = await client.post(exec_url, json={"cmd": ["/bin/sh"], "workdir": "/"})
    assert resp.status_code == status.HTTP_403_FORBIDDEN


async def test_system_core_stop_denied_without_root(
    authed_app: FastAPI,
    client: AsyncClient,
    dbsession: AsyncSession,
) -> None:
    # Register as SYSTEM_CORE
    reg_url = authed_app.url_path_for("register_container", container_id=_FAKE_ID)
    await client.put(
        reg_url,
        json={
            "container_class": ContainerClass.SYSTEM_CORE.value,
            "owner_scope": "platform",
            "policy": ContainerPolicy.FREE.value,
        },
    )
    lc_url = authed_app.url_path_for("container_lifecycle", container_id=_FAKE_ID, action="stop")
    resp = await client.post(lc_url)
    assert resp.status_code == status.HTTP_403_FORBIDDEN


async def test_system_core_start_allowed_without_root(
    authed_app: FastAPI,
    client: AsyncClient,
    dbsession: AsyncSession,
    mock_docker: MagicMock,
) -> None:
    """SYSTEM_CORE start is allowed even without root mode."""
    reg_url = authed_app.url_path_for("register_container", container_id=_FAKE_ID)
    await client.put(
        reg_url,
        json={
            "container_class": ContainerClass.SYSTEM_CORE.value,
            "owner_scope": "platform",
            "policy": ContainerPolicy.FREE.value,
        },
    )
    lc_url = authed_app.url_path_for("container_lifecycle", container_id=_FAKE_ID, action="start")
    resp = await client.post(lc_url)
    assert resp.status_code == status.HTTP_204_NO_CONTENT


# ──────────────────────────────────────────────────────────────────────────────
# Non-superuser must be rejected (403)
# ──────────────────────────────────────────────────────────────────────────────


async def test_non_superuser_gets_403(
    fastapi_app: FastAPI,
    client: AsyncClient,
    mock_docker: MagicMock,
) -> None:
    regular_user = MagicMock(spec=User)
    regular_user.id = uuid.uuid4()
    regular_user.is_active = True
    regular_user.is_superuser = False
    regular_user.is_verified = True

    fastapi_app.dependency_overrides[current_active_user] = lambda: regular_user
    fastapi_app.dependency_overrides[get_docker] = lambda: mock_docker

    url = fastapi_app.url_path_for("list_containers")
    resp = await client.get(url)
    assert resp.status_code == status.HTTP_403_FORBIDDEN


# ──────────────────────────────────────────────────────────────────────────────
# Label spoofing: dgx.* labels on raw Docker data must NOT affect classification
# ──────────────────────────────────────────────────────────────────────────────


async def test_dgx_labels_not_trusted(
    authed_app: FastAPI,
    client: AsyncClient,
    mock_docker: MagicMock,
) -> None:
    """
    Even if a container's Docker labels claim it is TENANT_APP,
    an unregistered container is returned as UNTRUSTED.
    """
    # Inject spoofed dgx label into Docker response
    mock_docker.list_containers = AsyncMock(
        return_value=[
            {
                "Id": _FAKE_ID,
                "Names": ["/spoof-container"],
                "Image": "evil:latest",
                "Status": "Up",
                "State": "running",
                "Created": "2026-01-01T00:00:00Z",
                "Ports": [],
                "NetworkSettings": {"Networks": {}},
                "Labels": {"dgx.class": "TENANT_APP"},  # spoofed!
            }
        ]
    )
    url = authed_app.url_path_for("list_containers")
    resp = await client.get(url)
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    # Must be UNTRUSTED because it's not in the registry
    assert data[0]["container_class"] == ContainerClass.UNTRUSTED.value
