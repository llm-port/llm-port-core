"""`llmport-agent join`: the path that needs no clipboard.

The value of this command is entirely in what it does *not* ask for. The
operator types a backend address; no token, no checksum, no environment
variables. So the tests below are mostly about the shape of the conversation
rather than the happy result.
"""

from __future__ import annotations

from typing import Any

import pytest

from llm_port_node_agent.backend_client import BackendClient


class _FakeResponse:
    def __init__(self, payload: Any, status: int = 200) -> None:
        self._payload = payload
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> Any:
        return self._payload


class _FakeHttp:
    """Records what the client sent, and replays scripted answers."""

    def __init__(self, answers: list[Any]) -> None:
        self.answers = answers
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def post(self, url: str, json: dict[str, Any] | None = None, **_: Any) -> _FakeResponse:
        self.calls.append((url, json or {}))
        answer = self.answers.pop(0)
        if isinstance(answer, _FakeResponse):
            return answer
        return _FakeResponse(answer)


def _client(answers: list[Any]) -> tuple[BackendClient, _FakeHttp]:
    client = BackendClient.__new__(BackendClient)
    http = _FakeHttp(answers)
    client._client = http  # type: ignore[attr-defined]
    return client, http


@pytest.mark.anyio()
async def test_the_request_carries_no_secret() -> None:
    """Nothing is transported to this machine, so nothing is sent from it."""
    client, http = _client([{"id": "r1", "code": "K7M-3QP", "poll_secret": "s", "expires_at": "x"}])
    await client.request_join(
        agent_id="spark-3201",
        host="10.88.10.71",
        capabilities={"gpu_count": 3},
        version="0.1.8",
    )

    url, body = http.calls[0]
    assert url.endswith("/nodes/join-requests")
    assert set(body) == {"agent_id", "host", "capabilities", "version"}
    assert "token" not in str(body).lower()


@pytest.mark.anyio()
async def test_the_machine_says_what_it_is_before_being_let_in() -> None:
    """The operator approves hardware, not a name, so it has to be sent."""
    client, http = _client([{"id": "r1", "code": "K7M-3QP", "poll_secret": "s", "expires_at": "x"}])
    await client.request_join(
        agent_id="spark-3201",
        host="10.88.10.71",
        capabilities={"gpu_count": 3, "gpu": {"vendor": "nvidia", "family": "GB10"}},
        version="0.1.8",
    )
    _, body = http.calls[0]
    assert body["capabilities"]["gpu"]["family"] == "GB10"


@pytest.mark.anyio()
async def test_collect_proves_it_is_the_requester() -> None:
    client, http = _client([{"status": "pending"}])
    await client.collect_join(request_id="r1", poll_secret="the-secret")

    url, body = http.calls[0]
    assert url.endswith("/nodes/join-requests/r1/collect")
    assert body == {"poll_secret": "the-secret"}


@pytest.mark.anyio()
@pytest.mark.parametrize(
    "answer",
    [
        {"status": "pending", "code": "K7M-3QP"},
        {"status": "approved", "credential": "id.secret", "node_id": "n1"},
        {"status": "rejected", "message": "Not ours."},
        {"status": "expired", "message": "The request timed out."},
        {"status": "claimed", "message": "This request was already used."},
    ],
)
async def test_every_outcome_is_a_status_the_agent_can_act_on(answer: dict[str, Any]) -> None:
    """No outcome is an exception the caller has to guess the meaning of."""
    client, _ = _client([answer])
    result = await client.collect_join(request_id="r1", poll_secret="s")
    assert result["status"] == answer["status"]


@pytest.mark.anyio()
async def test_a_malformed_answer_is_refused_rather_than_half_used() -> None:
    client, _ = _client([["not", "a", "dict"]])
    with pytest.raises(RuntimeError):
        await client.collect_join(request_id="r1", poll_secret="s")


def test_join_waits_in_human_time() -> None:
    """Someone has to walk to a browser; a 30-second timeout would be useless."""
    from llm_port_node_agent import __main__ as cli

    assert cli._JOIN_WAIT_SECONDS >= 10 * 60
    assert 1.0 <= cli._JOIN_POLL_SECONDS <= 10.0


def test_join_accepts_a_bare_host_and_port() -> None:
    """`10.88.10.220:8000` is what a person types; it should just work."""
    from llm_port_node_agent import __main__ as cli
    import inspect

    source = inspect.getsource(cli.cmd_join)
    assert '"://" not in backend' in source
    assert 'f"http://{backend}"' in source
