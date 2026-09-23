"""Running the install line again upgrades; it does not ask to join again.

The documented way to update an agent is to run the same line. For a machine
already in the fleet that filed a fresh join request -- one more thing for an
operator to approve, for a machine that was never out.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from llm_port_node_agent import __main__ as agent_main
from llm_port_node_agent.backend_client import BackendClient


class _Config:
    backend_url = "http://backend"


async def _whoami_with(status: int, body: dict[str, Any] | None = None) -> dict | None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/node-files/whoami"
        assert request.headers["authorization"] == "Bearer cred"
        return httpx.Response(status, json=body or {})

    client = BackendClient.__new__(BackendClient)
    client._client = httpx.AsyncClient(  # type: ignore[attr-defined]
        transport=httpx.MockTransport(handler), base_url="http://backend"
    )
    try:
        return await client.whoami(credential="cred")
    finally:
        await client._client.aclose()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_a_working_credential_means_already_a_member() -> None:
    member = await _whoami_with(200, {"agent_id": "spark-3201", "node_id": "n1"})
    assert member == {"agent_id": "spark-3201", "node_id": "n1"}


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 404])
async def test_a_revoked_credential_means_join_again(status: int) -> None:
    """Deleted from the fleet: the old credential is no membership at all."""
    assert await _whoami_with(status) is None


def test_join_on_an_enrolled_machine_restarts_instead_of_asking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started: list[Any] = []
    joined: list[Any] = []

    async def member(_config: Any) -> dict:
        return {"agent_id": "spark-3201"}

    async def join(_config: Any) -> bool:
        joined.append(True)
        return True

    monkeypatch.setattr(agent_main, "_existing_membership", member)
    monkeypatch.setattr(agent_main, "_join_flow", join)
    monkeypatch.setattr(agent_main, "cmd_start", lambda user=None: started.append(user))
    monkeypatch.setattr(agent_main, "_banner", lambda: None)
    monkeypatch.setattr(agent_main, "_save_env_file", lambda env: None)

    agent_main.cmd_join("http://10.88.10.220:8000", user=True)

    assert joined == [], "no join request for a machine already in the fleet"
    assert started == [True], "the service restarts on the new build, in the same scope"


def test_join_on_a_new_machine_still_asks(monkeypatch: pytest.MonkeyPatch) -> None:
    joined: list[Any] = []

    async def not_member(_config: Any) -> None:
        return None

    async def join(_config: Any) -> bool:
        joined.append(True)
        return True

    monkeypatch.setattr(agent_main, "_existing_membership", not_member)
    monkeypatch.setattr(agent_main, "_join_flow", join)
    monkeypatch.setattr(agent_main, "cmd_start", lambda user=None: None)
    monkeypatch.setattr(agent_main, "_banner", lambda: None)
    monkeypatch.setattr(agent_main, "_save_env_file", lambda env: None)

    agent_main.cmd_join("http://10.88.10.220:8000", user=True)

    assert joined == [True]
