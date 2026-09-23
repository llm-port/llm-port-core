"""An agent with no Loki address asks the backend for one.

The one-line install writes no ``LLM_PORT_NODE_AGENT_LOKI_URL``, and an
agent without one forwarded nothing -- both DGX nodes went silent in Loki the
day they were reinstalled that way.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import pytest

from llm_port_node_agent.backend_client import BackendClient
from llm_port_node_agent import service as service_module


async def _log_sink_with(status: int, body: dict[str, Any] | None = None) -> str | None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/node-files/log-sink"
        assert request.headers["authorization"] == "Bearer cred"
        return httpx.Response(status, json=body or {})

    client = BackendClient.__new__(BackendClient)
    client._client = httpx.AsyncClient(  # type: ignore[attr-defined]
        transport=httpx.MockTransport(handler), base_url="http://backend"
    )
    try:
        return await client.log_sink(credential="cred")
    finally:
        await client._client.aclose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_the_backend_names_the_sink() -> None:
    assert await _log_sink_with(200, {"loki_url": "http://10.88.10.220:3100"}) == "http://10.88.10.220:3100"


@pytest.mark.asyncio
async def test_a_backend_with_no_sink_says_so() -> None:
    assert await _log_sink_with(200, {"loki_url": None}) is None


@pytest.mark.asyncio
async def test_an_older_backend_is_no_sink_not_an_error() -> None:
    assert await _log_sink_with(404) is None


@pytest.mark.asyncio
async def test_a_backend_in_trouble_is_retried_not_taken_as_no() -> None:
    with pytest.raises(httpx.HTTPStatusError):
        await _log_sink_with(503)


# ── the discovery loop ───────────────────────────────────────────────────


class _Client:
    def __init__(self, answers: list[Any]) -> None:
        self.answers = answers
        self.asked = 0

    async def log_sink(self, *, credential: str) -> str | None:
        self.asked += 1
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def _service(answers: list[Any], credential: str | None = "cred") -> Any:
    svc = service_module.NodeAgentService.__new__(service_module.NodeAgentService)
    svc._client = _Client(answers)  # type: ignore[assignment]
    svc._state_store = type("S", (), {"state": type("St", (), {"credential": credential})()})()
    svc._log_tasks = []
    svc.started: list[str] = []  # type: ignore[attr-defined]
    svc._start_log_forwarding = lambda url, runtime: svc.started.append(url)  # type: ignore[method-assign]
    return svc


@pytest.fixture
def no_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    async def instant(_seconds: float) -> None:
        return None

    monkeypatch.setattr(service_module.asyncio, "sleep", instant)


@pytest.mark.asyncio
async def test_forwarding_starts_on_the_backend_s_answer(no_waiting: None) -> None:
    svc = _service(["http://10.88.10.220:3100"])
    await svc._discover_log_sink(runtime=None)
    assert svc.started == ["http://10.88.10.220:3100"]


@pytest.mark.asyncio
async def test_an_unreachable_backend_is_asked_again(no_waiting: None) -> None:
    svc = _service([httpx.ConnectError("down"), httpx.ConnectError("down"), "http://loki:3100"])
    await svc._discover_log_sink(runtime=None)
    assert svc.started == ["http://loki:3100"]
    assert svc._client.asked == 3


@pytest.mark.asyncio
async def test_no_sink_ends_the_search(no_waiting: None) -> None:
    svc = _service([None])
    await svc._discover_log_sink(runtime=None)
    assert svc.started == []
    assert svc._client.asked == 1


@pytest.mark.asyncio
async def test_a_machine_still_joining_waits_for_its_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    svc = _service(["http://loki:3100"], credential=None)
    naps = 0

    async def nap(_seconds: float) -> None:
        nonlocal naps
        naps += 1
        if naps == 2:
            svc._state_store.state.credential = "cred"  # joined meanwhile

    monkeypatch.setattr(service_module.asyncio, "sleep", nap)
    await asyncio.wait_for(svc._discover_log_sink(runtime=None), timeout=5)
    assert svc.started == ["http://loki:3100"]


def test_requests_are_not_logged_line_by_line() -> None:
    """Every Loki push logged a line, which the next push shipped to Loki."""
    import logging

    from llm_port_node_agent import __main__ as agent_main

    agent_main._configure_logging("INFO")
    assert not logging.getLogger("httpx").isEnabledFor(logging.INFO)
    assert not logging.getLogger("httpcore").isEnabledFor(logging.INFO)
