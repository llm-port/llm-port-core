"""The runtime image reaches the second machine from the first, not the server.

Every member used to pull the ~12 GB image from this server at once: on the
DGX pair, two transfers sharing a 1 Gb/s management link while the machines
sat next to each other on a 200 Gb/s RoCE fabric.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.models.inference import EnvironmentStatus
from llm_port_backend.db.models.node_control import (
    InfraNodeCommand,
    NodeCommandStatus,
    NodeCommandType,
)
from llm_port_backend.services.inference.drivers.ray.environment import RayEnvironmentManager
from tests.test_inference_env_observation import _bound_environment, _FakeNodeControl

IMAGE = NodeCommandType.ENSURE_RUNTIME_IMAGE.value
SERVE = NodeCommandType.SERVE_RUNTIME_IMAGE.value


class _Fleet(_FakeNodeControl):
    """Members that may lack the image, and a peer that may fail to serve."""

    def __init__(self, *, lacking: set[str], peer_fails: bool = False) -> None:
        super().__init__()
        self.lacking = lacking  # agent_ids
        self.peer_fails = peer_fails
        self.agent_of: dict[Any, str] = {}

    async def issue_command(self, **kwargs: Any) -> InfraNodeCommand:
        row = await super().issue_command(**kwargs)
        if kwargs["command_type"] != IMAGE:
            return row
        payload = kwargs.get("payload") or {}
        agent = self.agent_of.get(kwargs["node_id"])
        if payload.get("fetch") is False and agent in self.lacking:
            row.status = NodeCommandStatus.FAILED.value
            row.error_code = "runtime_image_missing"
            row.error_message = "not present"
            row.result_json = {}
        elif (payload.get("source") or {}).get("peer_url") and self.peer_fails:
            row.status = NodeCommandStatus.FAILED.value
            row.error_code = "image_transfer_failed"
            row.error_message = "connection refused"
            row.result_json = {}
        return row

    def steps(self) -> list[str]:
        """Each image command as "<step>@<agent>", in issue order."""
        out = []
        for cmd in self.issued:
            if cmd["command_type"] not in (IMAGE, SERVE):
                continue
            step = cmd["idempotency_key"].split(":")[3]
            out.append(f"{step}@{self.agent_of.get(cmd['node_id'])}")
        return out


async def _fleet(dbsession: AsyncSession, **kw: Any) -> tuple[Any, _Fleet]:
    env = await _bound_environment(dbsession)
    fleet = _Fleet(**kw)
    from sqlalchemy import select

    from llm_port_backend.db.models.node_control import InfraNode

    for node in (await dbsession.execute(select(InfraNode))).scalars():
        fleet.agent_of[node.id] = node.agent_id
    return env, fleet


def _payload_of(fleet: _Fleet, step: str) -> dict[str, Any]:
    for cmd in fleet.issued:
        if cmd["idempotency_key"].split(":")[3] == step:
            return cmd["payload"]
    raise AssertionError(f"no {step} command issued")


@pytest.mark.anyio()
async def test_first_start_fetches_once_and_seeds_the_rest(dbsession: AsyncSession) -> None:
    env, fleet = await _fleet(dbsession, lacking={"spark-a", "spark-b"})

    await RayEnvironmentManager().reconcile_environment(dbsession, env, node_control=fleet)

    steps = fleet.steps()
    assert steps.count("image-from-server@spark-a") == 1
    assert "image-from-server@spark-b" not in steps, "the second machine must not use the server"
    assert "image-seed@spark-a" in steps
    assert "image-from-peer@spark-b" in steps
    assert env.status == EnvironmentStatus.READY


@pytest.mark.anyio()
async def test_it_serves_over_the_fabric_not_the_management_network(
    dbsession: AsyncSession,
) -> None:
    env, fleet = await _fleet(dbsession, lacking={"spark-a", "spark-b"})

    await RayEnvironmentManager().reconcile_environment(dbsession, env, node_control=fleet)

    serve = _payload_of(fleet, "image-seed")
    fetch = _payload_of(fleet, "image-from-peer")
    assert serve["bind_ip"] == "10.100.0.1", "the RoCE address from the applied plan"
    assert fetch["source"]["peer_url"].startswith("http://10.100.0.1:")
    assert serve["token"] == fetch["source"]["token"]
    assert len(serve["token"]) >= 32
    assert serve["expected_peers"] == 1


@pytest.mark.anyio()
async def test_a_member_that_already_has_it_seeds_without_the_server(
    dbsession: AsyncSession,
) -> None:
    env, fleet = await _fleet(dbsession, lacking={"spark-b"})

    await RayEnvironmentManager().reconcile_environment(dbsession, env, node_control=fleet)

    steps = fleet.steps()
    assert not any(step.startswith("image-from-server") for step in steps)
    assert "image-seed@spark-a" in steps
    assert "image-from-peer@spark-b" in steps


@pytest.mark.anyio()
async def test_nobody_fetches_anything_when_everyone_has_it(dbsession: AsyncSession) -> None:
    env, fleet = await _fleet(dbsession, lacking=set())

    await RayEnvironmentManager().reconcile_environment(dbsession, env, node_control=fleet)

    assert fleet.steps() == ["image-probe@spark-a", "image-probe@spark-b"]


@pytest.mark.anyio()
async def test_a_peer_that_cannot_serve_falls_back_to_the_server(dbsession: AsyncSession) -> None:
    env, fleet = await _fleet(dbsession, lacking={"spark-b"}, peer_fails=True)

    await RayEnvironmentManager().reconcile_environment(dbsession, env, node_control=fleet)

    steps = fleet.steps()
    assert "image-from-peer@spark-b" in steps
    assert "image-from-server@spark-b" in steps, "the server is the fallback"
    assert env.status == EnvironmentStatus.READY


@pytest.mark.anyio()
async def test_the_token_survives_a_resumed_pass(dbsession: AsyncSession) -> None:
    """A pass that times out waiting resumes the same commands next time."""
    env, _fleet_ = await _fleet(dbsession, lacking={"spark-b"})
    manager = RayEnvironmentManager()

    first = manager._seed_token(env, "node-a")
    second = manager._seed_token(env, "node-a")
    other_node = manager._seed_token(env, "node-b")

    assert first == second
    assert first != other_node
