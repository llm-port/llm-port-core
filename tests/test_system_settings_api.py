"""Tests for admin system settings APIs."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

from fastapi import FastAPI
from httpx import AsyncClient
from starlette import status

from llm_port_backend.db.models.users import User, current_active_user
from llm_port_backend.settings import settings


def _superuser() -> User:
    user = MagicMock(spec=User)
    user.id = uuid.uuid4()
    user.is_active = True
    user.is_superuser = True
    user.is_verified = True
    return user


def _regular_user() -> User:
    user = MagicMock(spec=User)
    user.id = uuid.uuid4()
    user.is_active = True
    user.is_superuser = False
    user.is_verified = True
    return user


def _override_regular_user() -> User:
    return _regular_user()


def _override_superuser() -> User:
    return _superuser()


async def test_system_settings_schema_requires_auth(
    client: AsyncClient,
    fastapi_app: FastAPI,
) -> None:
    fastapi_app.dependency_overrides.pop(current_active_user, None)
    url = fastapi_app.url_path_for("system_settings_schema")
    response = await client.get(url)
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


async def test_system_settings_schema_forbidden_for_regular_user(
    client: AsyncClient,
    fastapi_app: FastAPI,
) -> None:
    fastapi_app.dependency_overrides[current_active_user] = _override_regular_user
    url = fastapi_app.url_path_for("system_settings_schema")
    response = await client.get(url)
    assert response.status_code == status.HTTP_403_FORBIDDEN


async def test_system_settings_schema_success(
    client: AsyncClient,
    fastapi_app: FastAPI,
) -> None:
    fastapi_app.dependency_overrides[current_active_user] = _override_superuser
    url = fastapi_app.url_path_for("system_settings_schema")
    response = await client.get(url)
    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert isinstance(body, list)
    assert any(item["key"] == "api.server.endpoint_url" for item in body)


async def test_system_settings_update_live_reload_key(
    client: AsyncClient,
    fastapi_app: FastAPI,
) -> None:
    fastapi_app.dependency_overrides[current_active_user] = _override_superuser
    url = fastapi_app.url_path_for("system_settings_update", key="api.server.endpoint_url")
    response = await client.put(url, json={"value": "http://localhost:8001/api/docs", "target_host": "local"})
    assert response.status_code == status.HTTP_200_OK
    body = response.json()
    assert body["key"] == "api.server.endpoint_url"
    assert body["apply_scope"] == "live_reload"


async def test_system_settings_update_protected_key_requires_root_mode(
    client: AsyncClient,
    fastapi_app: FastAPI,
) -> None:
    fastapi_app.dependency_overrides[current_active_user] = _override_superuser
    url = fastapi_app.url_path_for("system_settings_update", key="llm_port_api.jwt_secret")
    response = await client.put(url, json={"value": "secret-value", "target_host": "local"})
    assert response.status_code == status.HTTP_403_FORBIDDEN


async def test_system_agent_apply_enabled_returns_job_id(
    client: AsyncClient,
    fastapi_app: FastAPI,
) -> None:
    fastapi_app.dependency_overrides[current_active_user] = _override_superuser
    previous = settings.system_agent_enabled
    settings.system_agent_enabled = True
    try:
        url = fastapi_app.url_path_for("system_agent_apply", agent_id="agent-1")
        response = await client.post(url, json={"signed_bundle": {"plan": "test"}})
        assert response.status_code == status.HTTP_200_OK
        body = response.json()
        assert body["accepted"] is True
        assert body["agent_id"] == "agent-1"
        assert isinstance(body["job_id"], str)
    finally:
        settings.system_agent_enabled = previous
