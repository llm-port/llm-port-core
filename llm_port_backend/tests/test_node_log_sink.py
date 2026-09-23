"""Where a machine sends its logs.

The one-line install writes no Loki address, so machines installed that way
forwarded nothing: on the DGX pair the last Ray log in Loki predated the
reinstall. The agent now asks the backend, and the backend has to answer with
an address that works *from the machine* -- its own ``loki_base_url`` is a
loopback address in development and a container name in the deployment.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from llm_port_backend.settings import settings
from llm_port_backend.web.api.node_files import views
from llm_port_backend.web.api.node_files.views import agent_log_sink


@pytest.mark.parametrize(
    ("base", "request_host", "expected"),
    [
        # Development: Loki on the backend's own loopback, published on the host.
        ("http://127.0.0.1:3100", "10.88.10.220", "http://10.88.10.220:3100"),
        ("http://localhost:3100", "10.88.10.220", "http://10.88.10.220:3100"),
        # The deployment: a compose service name only the backend can resolve.
        ("http://llm-port-loki:3100", "llmport.example.com", "http://llmport.example.com:3100"),
        # A Loki the machines can already reach is passed on as it is.
        ("https://loki.corp.example:3100/", "10.0.0.5", "https://loki.corp.example:3100"),
        ("http://10.0.0.9:3100", "10.0.0.5", "http://10.0.0.9:3100"),
        # IPv6 hosts need brackets.
        ("http://[::1]:3100", "fd00::5", "http://[fd00::5]:3100"),
    ],
)
def test_the_address_works_from_the_machine(base: str, request_host: str, expected: str) -> None:
    assert agent_log_sink(base, "", request_host) == expected


def test_an_explicit_address_wins() -> None:
    assert agent_log_sink("http://127.0.0.1:3100", " http://logs.lan:3100 ", "x") == "http://logs.lan:3100"


def test_no_answer_rather_than_a_wrong_one() -> None:
    assert agent_log_sink("http://127.0.0.1:3100", "", None) is None
    assert agent_log_sink("", "", "10.0.0.5") is None


@pytest.mark.anyio()
async def test_the_endpoint_answers_with_the_address_the_agent_used(
    fastapi_app: FastAPI, client: AsyncClient, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "loki_base_url", "http://127.0.0.1:3100")
    monkeypatch.setattr(settings, "agent_loki_url", "")
    fastapi_app.dependency_overrides[views._authenticate_node] = lambda: SimpleNamespace(id="n1")
    try:
        response = await client.get(
            "/api/node-files/log-sink", headers={"host": "10.88.10.220:8000"},
        )
    finally:
        fastapi_app.dependency_overrides.pop(views._authenticate_node, None)

    assert response.status_code == 200
    assert response.json() == {"loki_url": "http://10.88.10.220:3100"}


@pytest.mark.anyio()
async def test_the_endpoint_needs_a_node_credential(client: AsyncClient) -> None:
    response = await client.get("/api/node-files/log-sink")
    # Refused, and no address given. (The test app has no database, so node
    # authentication fails closed with 503 before it reads the credential.)
    assert response.status_code in (401, 403, 503)
    assert "loki_url" not in response.text
