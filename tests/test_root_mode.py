"""Integration tests for root mode (break-glass) API."""

import uuid
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from airgap_backend.db.models.users import User, current_active_user
from airgap_backend.web.api.admin.dependencies import get_docker


def _make_superuser() -> User:
    user = MagicMock(spec=User)
    user.id = uuid.uuid4()
    user.is_active = True
    user.is_superuser = True
    user.is_verified = True
    return user


@pytest.fixture()
def superuser() -> User:
    return _make_superuser()


@pytest.fixture()
def authed_app(fastapi_app: FastAPI, superuser: User) -> FastAPI:
    """Return app with mocked auth (superuser) and mocked Docker."""
    fastapi_app.dependency_overrides[current_active_user] = lambda: superuser
    mock_docker = MagicMock()
    fastapi_app.dependency_overrides[get_docker] = lambda: mock_docker
    return fastapi_app


async def test_start_root_mode(
    authed_app: FastAPI,
    client: AsyncClient,
    dbsession: AsyncSession,
) -> None:
    """Starting a root session should return 200 with the session details."""
    url = authed_app.url_path_for("start_root_mode")
    resp = await client.post(
        url,
        json={"reason": "emergency maintenance work", "scope": "all", "duration_seconds": 300},
    )
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["active"] is True
    assert data["reason"] == "emergency maintenance work"
    assert data["scope"] == "all"
    assert data["duration_seconds"] == 300


async def test_double_start_root_mode_conflict(
    authed_app: FastAPI,
    client: AsyncClient,
    dbsession: AsyncSession,
) -> None:
    """Starting a second root session while one is active returns 409."""
    url = authed_app.url_path_for("start_root_mode")
    payload = {"reason": "first session for testing purposes", "scope": "all"}
    await client.post(url, json=payload)
    resp = await client.post(url, json=payload)
    assert resp.status_code == status.HTTP_409_CONFLICT


async def test_stop_root_mode(
    authed_app: FastAPI,
    client: AsyncClient,
    dbsession: AsyncSession,
) -> None:
    """Stopping an active root session should return 200."""
    start_url = authed_app.url_path_for("start_root_mode")
    stop_url = authed_app.url_path_for("stop_root_mode")

    await client.post(
        start_url,
        json={"reason": "testing stop endpoint now", "scope": "all"},
    )
    resp = await client.post(stop_url)
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert data["active"] is False
    assert data["end_time"] is not None


async def test_stop_root_mode_no_session(
    authed_app: FastAPI,
    client: AsyncClient,
) -> None:
    """Stopping when no session is active returns 404."""
    url = authed_app.url_path_for("stop_root_mode")
    resp = await client.post(url)
    assert resp.status_code == status.HTTP_404_NOT_FOUND


async def test_root_mode_status_inactive(
    authed_app: FastAPI,
    client: AsyncClient,
) -> None:
    """Status endpoint returns active=False when no session exists."""
    url = authed_app.url_path_for("root_mode_status")
    resp = await client.get(url)
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["active"] is False


async def test_root_mode_status_active(
    authed_app: FastAPI,
    client: AsyncClient,
) -> None:
    """Status endpoint returns active=True while a session is live."""
    start_url = authed_app.url_path_for("start_root_mode")
    status_url = authed_app.url_path_for("root_mode_status")

    await client.post(
        start_url,
        json={"reason": "checking status endpoint works correctly", "scope": "all"},
    )
    resp = await client.get(status_url)
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["active"] is True
