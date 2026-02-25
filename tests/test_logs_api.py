"""Tests for Loki proxy logs API."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from starlette import status

from llm_port_backend.db.models.users import User, current_active_user
from llm_port_backend.settings import settings
from llm_port_backend.web.api.logs import views as logs_views


def _make_superuser() -> User:
    user = MagicMock(spec=User)
    user.id = uuid.uuid4()
    user.is_active = True
    user.is_superuser = True
    user.is_verified = True
    return user


@pytest.fixture()
def authed_app(fastapi_app: FastAPI) -> FastAPI:
    fastapi_app.dependency_overrides[current_active_user] = lambda: _make_superuser()
    return fastapi_app


async def test_query_range_limit_default(monkeypatch: pytest.MonkeyPatch, authed_app: FastAPI, client: AsyncClient) -> None:
    captured: dict[str, str] = {}
    monkeypatch.setattr(settings, "logs_default_limit", 123)
    monkeypatch.setattr(settings, "logs_max_limit", 5000)
    monkeypatch.setattr(settings, "logs_allowed_labels_raw", None)

    async def _fake_request(path: str, params: dict[str, str] | None = None, timeout: float = 10.0) -> dict:
        del path, timeout
        captured.update(params or {})
        return {"data": {"result": []}}

    monkeypatch.setattr(logs_views, "_request_loki_json", _fake_request)

    url = authed_app.url_path_for("logs_query_range")
    resp = await client.get(url, params={"query": '{job="api"}'})
    assert resp.status_code == status.HTTP_200_OK
    assert captured["limit"] == "123"


async def test_query_range_limit_clamped(monkeypatch: pytest.MonkeyPatch, authed_app: FastAPI, client: AsyncClient) -> None:
    captured: dict[str, str] = {}
    monkeypatch.setattr(settings, "logs_default_limit", 200)
    monkeypatch.setattr(settings, "logs_max_limit", 50)
    monkeypatch.setattr(settings, "logs_allowed_labels_raw", None)

    async def _fake_request(path: str, params: dict[str, str] | None = None, timeout: float = 10.0) -> dict:
        del path, timeout
        captured.update(params or {})
        return {"data": {"result": []}}

    monkeypatch.setattr(logs_views, "_request_loki_json", _fake_request)

    url = authed_app.url_path_for("logs_query_range")
    resp = await client.get(url, params={"query": '{job="api"}', "limit": 9999})
    assert resp.status_code == status.HTTP_200_OK
    assert captured["limit"] == "50"


async def test_query_allowlist_enforced(monkeypatch: pytest.MonkeyPatch, authed_app: FastAPI, client: AsyncClient) -> None:
    monkeypatch.setattr(settings, "logs_allowed_labels_raw", "job,container")

    url = authed_app.url_path_for("logs_query_range")
    resp = await client.get(url, params={"query": '{namespace="prod"}'})
    assert resp.status_code == status.HTTP_400_BAD_REQUEST
    assert "Disallowed labels" in resp.text


async def test_label_name_allowlist_enforced(
    monkeypatch: pytest.MonkeyPatch,
    authed_app: FastAPI,
    client: AsyncClient,
) -> None:
    monkeypatch.setattr(settings, "logs_allowed_labels_raw", "job,container")

    url = authed_app.url_path_for("logs_label_values", name="namespace")
    resp = await client.get(url)
    assert resp.status_code == status.HTTP_400_BAD_REQUEST
    assert "not allowed" in resp.text


async def test_loki_error_maps_to_502(monkeypatch: pytest.MonkeyPatch, authed_app: FastAPI, client: AsyncClient) -> None:
    monkeypatch.setattr(settings, "logs_allowed_labels_raw", None)

    async def _fake_request(path: str, params: dict[str, str] | None = None, timeout: float = 10.0) -> dict:
        del path, params, timeout
        raise logs_views.LokiUpstreamError("boom", status_code=500)

    monkeypatch.setattr(logs_views, "_request_loki_json", _fake_request)

    url = authed_app.url_path_for("logs_labels")
    resp = await client.get(url)
    assert resp.status_code == status.HTTP_502_BAD_GATEWAY
    assert "boom" in resp.text


async def test_query_response_normalized(monkeypatch: pytest.MonkeyPatch, authed_app: FastAPI, client: AsyncClient) -> None:
    monkeypatch.setattr(settings, "logs_allowed_labels_raw", None)

    async def _fake_request(path: str, params: dict[str, str] | None = None, timeout: float = 10.0) -> dict:
        del path, params, timeout
        return {
            "data": {
                "result": [
                    {
                        "stream": {"job": "api"},
                        "values": [
                            ["1735800000000000000", '{"level":"error","msg":"boom"}'],
                            ["1735800001000000000", "plain line"],
                        ],
                    }
                ],
                "stats": {"summary": {"bytesProcessedPerSecond": 1}},
            }
        }

    monkeypatch.setattr(logs_views, "_request_loki_json", _fake_request)

    url = authed_app.url_path_for("logs_query_range")
    resp = await client.get(url, params={"query": '{job="api"}'})
    assert resp.status_code == status.HTTP_200_OK
    body = resp.json()
    assert "streams" in body
    assert body["streams"][0]["labels"]["job"] == "api"
    assert body["streams"][0]["entries"][0]["line"] == '{"level":"error","msg":"boom"}'
    assert body["streams"][0]["entries"][0]["structured"]["level"] == "error"
    assert body["streams"][0]["entries"][1]["line"] == "plain line"
    assert "structured" not in body["streams"][0]["entries"][1]
