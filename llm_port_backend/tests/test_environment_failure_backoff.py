"""A cluster that cannot start must say why once, and stop asking.

Found by walking the onboarding guide on the DGX pair: the catalogue pinned an
image the server did not hold, so every cluster start was refused by the
node's integrity check -- correctly. Then:

* the reconciler revisited the failed cluster on every tick and issued the
  same command again, 303 times in three hours, each failing identically;
* the reason never reached the page, which said "Some machines are not
  reporting" about a machine that was reporting perfectly well.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
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

MISMATCH = (
    "This server holds a different build of the runtime image (id sha256:aaa) "
    "than the node was asked to run (id sha256:bbb)."
)


class _ImageRefused(_FakeNodeControl):
    """Every image command fails the way the walkthrough's did."""

    def __init__(self, *, code: str = "server_image_mismatch", message: str = MISMATCH) -> None:
        super().__init__()
        self._code = code
        self._message = message

    async def issue_command(self, **kwargs: Any) -> InfraNodeCommand:
        row = await super().issue_command(**kwargs)
        if kwargs["command_type"] == NodeCommandType.ENSURE_RUNTIME_IMAGE.value:
            row.status = NodeCommandStatus.FAILED.value
            row.error_code = self._code
            row.error_message = self._message
            row.result_json = {}
        return row


def _image_commands(control: _FakeNodeControl) -> int:
    return len(control.by_type(NodeCommandType.ENSURE_RUNTIME_IMAGE.value))


@pytest.mark.anyio()
async def test_the_reason_reaches_the_page(dbsession: AsyncSession) -> None:
    """The page reads ``status_message``; nothing used to write it."""
    env = await _bound_environment(dbsession)

    await RayEnvironmentManager().reconcile_environment(dbsession, env, node_control=_ImageRefused())

    assert env.status == EnvironmentStatus.FAILED
    assert env.status_message and "different build" in env.status_message
    # The machine is fine; saying otherwise sends the operator the wrong way.
    assert "not reporting" not in env.status_message


@pytest.mark.anyio()
async def test_a_permanent_failure_is_not_asked_again_on_the_next_tick(
    dbsession: AsyncSession,
) -> None:
    env = await _bound_environment(dbsession)
    control = _ImageRefused()
    manager = RayEnvironmentManager()

    await manager.reconcile_environment(dbsession, env, node_control=control)
    first = _image_commands(control)
    for _ in range(5):
        await manager.reconcile_environment(dbsession, env, node_control=control)

    assert _image_commands(control) == first, "the same question was asked again"
    assert env.status == EnvironmentStatus.FAILED, "holding back must not change what it shows"


@pytest.mark.anyio()
async def test_it_says_that_retrying_will_not_help(dbsession: AsyncSession) -> None:
    env = await _bound_environment(dbsession)

    await RayEnvironmentManager().reconcile_environment(dbsession, env, node_control=_ImageRefused())

    assert "will not change by retrying" in env.status_message
    assert env.observed_status_json["retry"]["permanent"] is True


@pytest.mark.anyio()
async def test_a_changed_input_is_tried_at_once(dbsession: AsyncSession) -> None:
    """A rebuild or an operator edit means the answer could now differ."""
    env = await _bound_environment(dbsession)
    control = _ImageRefused()
    manager = RayEnvironmentManager()

    await manager.reconcile_environment(dbsession, env, node_control=control)
    before = _image_commands(control)

    env.generation += 1  # what any operator change does
    await manager.reconcile_environment(dbsession, env, node_control=control)

    assert _image_commands(control) > before


@pytest.mark.anyio()
async def test_a_transient_failure_backs_off_rather_than_giving_up(
    dbsession: AsyncSession,
) -> None:
    env = await _bound_environment(dbsession)
    control = _ImageRefused(code="image_transfer_failed", message="connection reset")
    manager = RayEnvironmentManager()

    await manager.reconcile_environment(dbsession, env, node_control=control)
    retry = env.observed_status_json["retry"]

    assert retry["permanent"] is False
    assert retry["attempts"] == 1
    due = datetime.fromisoformat(retry["next_attempt_at"])
    assert timedelta(seconds=30) < due - datetime.now(tz=UTC) <= timedelta(minutes=2)
    assert "Trying again in about" in env.status_message


@pytest.mark.anyio()
async def test_repeats_back_off_further_each_time(dbsession: AsyncSession) -> None:
    env = await _bound_environment(dbsession)
    control = _ImageRefused(code="image_transfer_failed", message="connection reset")
    manager = RayEnvironmentManager()

    waits = []
    for _ in range(3):
        await manager.reconcile_environment(dbsession, env, node_control=control)
        retry = env.observed_status_json["retry"]
        waits.append(
            datetime.fromisoformat(retry["next_attempt_at"])
            - datetime.fromisoformat(retry["last_failed_at"])
        )
        # Pretend the wait has passed so the next pass is allowed to try.
        env.observed_status_json = {
            **env.observed_status_json,
            "retry": {**retry, "next_attempt_at": datetime.now(tz=UTC).isoformat()},
        }

    assert waits[0] < waits[1] < waits[2]


@pytest.mark.anyio()
async def test_success_clears_the_memory_of_failure(dbsession: AsyncSession) -> None:
    env = await _bound_environment(dbsession)
    manager = RayEnvironmentManager()

    await manager.reconcile_environment(dbsession, env, node_control=_ImageRefused())
    assert "retry" in env.observed_status_json

    env.generation += 1
    await manager.reconcile_environment(dbsession, env, node_control=_FakeNodeControl())

    assert env.status == EnvironmentStatus.READY
    assert "retry" not in env.observed_status_json
    assert env.status_message is None


@pytest.mark.anyio()
async def test_try_again_overrides_the_backoff(dbsession: AsyncSession) -> None:
    """The person pressing it has usually just fixed what it was waiting on."""
    from llm_port_backend.services.inference.service import EnvironmentService

    env = await _bound_environment(dbsession)
    await RayEnvironmentManager().reconcile_environment(dbsession, env, node_control=_ImageRefused())
    assert "retry" in env.observed_status_json

    service = EnvironmentService(session=dbsession)
    refreshed = await service.request_reconcile(env.id)

    assert "retry" not in (refreshed.observed_status_json or {})


@pytest.mark.anyio()
async def test_progress_from_a_member_reaches_the_cluster(dbsession: AsyncSession) -> None:
    """The page said "Preparing 1 machine" for the whole transfer."""
    from llm_port_backend.db.dao.node_control_dao import NodeControlDAO
    from llm_port_backend.db.models.node_control import InfraNode
    from llm_port_backend.services.inference.service import EnvironmentService

    env = await _bound_environment(dbsession)
    node = InfraNode(agent_id=f"n-{uuid.uuid4().hex[:6]}", host="10.0.0.9", status="healthy",
                     capabilities_json={})
    dbsession.add(node)
    await dbsession.flush()

    dao = NodeControlDAO(dbsession)
    command = await dao.create_command(
        node_id=node.id,
        command_type=NodeCommandType.ENSURE_RUNTIME_IMAGE.value,
        payload_json={},
        timeout_sec=3600,
        issued_by=None,
        correlation_id=None,
        idempotency_key=f"inference-env:{env.id}:1:ensure-image:{node.id}",
    )
    command.status = NodeCommandStatus.RUNNING.value
    await dao.append_command_event(
        command_id=command.id,
        phase="progress",
        message="Receiving llmport/ray-vllm-gb10: 4.0 GiB of 12.0 GiB (33%)",
        payload_json={"progress_pct": 33},
    )
    await dbsession.flush()

    progress = await EnvironmentService(session=dbsession).lifecycle_progress(env.id)

    assert progress is not None
    assert progress["progress_pct"] == 33
    assert "4.0 GiB of 12.0 GiB" in progress["message"]


@pytest.mark.anyio()
async def test_no_progress_once_nothing_is_in_flight(dbsession: AsyncSession) -> None:
    from llm_port_backend.services.inference.service import EnvironmentService

    env = await _bound_environment(dbsession)

    assert await EnvironmentService(session=dbsession).lifecycle_progress(env.id) is None


@pytest.mark.anyio()
async def test_each_machine_keeps_its_own_progress(dbsession: AsyncSession) -> None:
    """Two machines receive the image at once; one line would flick between them."""
    from llm_port_backend.db.dao.node_control_dao import NodeControlDAO
    from llm_port_backend.db.models.node_control import InfraNode
    from llm_port_backend.services.inference.service import EnvironmentService

    env = await _bound_environment(dbsession)
    dao = NodeControlDAO(dbsession)
    for name, pct in (("head", 20), ("worker", 70)):
        node = InfraNode(agent_id=f"{name}-{uuid.uuid4().hex[:6]}", host="10.0.0.9",
                         status="healthy", capabilities_json={})
        dbsession.add(node)
        await dbsession.flush()
        command = await dao.create_command(
            node_id=node.id,
            command_type=NodeCommandType.ENSURE_RUNTIME_IMAGE.value,
            payload_json={},
            timeout_sec=3600,
            issued_by=None,
            correlation_id=None,
            idempotency_key=f"inference-env:{env.id}:1:ensure-image:{node.id}",
        )
        command.status = NodeCommandStatus.RUNNING.value
        for step in (pct - 10, pct):
            await dao.append_command_event(
                command_id=command.id,
                phase="progress",
                message=f"{name} at {step}%",
                payload_json={"progress_pct": step},
            )
    await dbsession.flush()

    progress = await EnvironmentService(session=dbsession).lifecycle_progress(env.id)

    assert progress is not None
    seen = sorted(m["progress_pct"] for m in progress["machines"])
    assert seen == [20, 70], "each machine reports its own newest figure"
