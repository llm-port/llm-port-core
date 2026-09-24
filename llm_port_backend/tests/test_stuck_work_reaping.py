"""Nothing in flight may stay in flight forever.

Three places held work that could never finish and that nothing would ever
clean up. None of them logged an error, because from each component's own
point of view nothing had failed:

  * an in-flight **command** was only reaped when its node was OFFLINE. Two
    agents on one machine, or a dropped session, leave the node heartbeating
    normally while a dispatched command is never answered. It stayed in
    flight, and the deployment waiting on it never moved.
  * a stream **session** was closed only by the websocket handler's teardown,
    which a killed agent never reaches. The row counted as live for hours, so
    a node appeared to have more agents attached than it did.
  * a PENDING/SYNCING **artifact row** meant "a sync is under way", and the
    coordinator skipped that node. If the sync died, the row stayed, the node
    was skipped permanently, and the artifact never became ready.

The shared shape: an in-progress marker with no expiry. The shared fix: make
the marker a claim with a deadline, and prefer evidence of progress over
evidence of reachability.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.dao.node_control_dao import NodeControlDAO
from llm_port_backend.db.models.node_control import (
    InfraNode,
    NodeCommandStatus,
    NodeCommandType,
    NodeHealthStatus,
)
from llm_port_backend.services.nodes.service import NodeControlService
from llm_port_backend.settings import settings

PEPPER = "test-pepper"


def _service(session: AsyncSession) -> NodeControlService:
    return NodeControlService(
        NodeControlDAO(session),
        pepper=PEPPER,
        enrollment_ttl_minutes=60,
        default_command_timeout_sec=60,
    )


async def _node(session: AsyncSession, *, status: str) -> InfraNode:
    node = InfraNode(
        agent_id=f"agent-{uuid.uuid4().hex[:8]}",
        host="10.88.10.71",
        status=status,
        capabilities_json={},
    )
    session.add(node)
    await session.flush()
    return node


async def _inflight_command(
    session: AsyncSession, node: InfraNode, *, dispatched_ago_sec: int
):
    dao = NodeControlDAO(session)
    command = await dao.create_command(
        node_id=node.id,
        command_type=NodeCommandType.GET_RAY_SERVE_STATUS.value,
        payload_json={},
        timeout_sec=60,
        issued_by=None,
        correlation_id=None,
        idempotency_key=f"k-{uuid.uuid4().hex[:8]}",
    )
    when = datetime.now(tz=UTC) - timedelta(seconds=dispatched_ago_sec)
    command.status = NodeCommandStatus.DISPATCHED.value
    command.dispatched_at = when
    command.issued_at = when
    await session.flush()
    return command


# ── commands ─────────────────────────────────────────────────────────────


def _silence_window() -> int:
    """The silence the service will demand of the command these tests build.

    It scales with the command's own timeout now, rather than being one
    constant for every command, so the tests ask the service instead of
    hard-coding a number that would drift away from it.
    """
    return max(
        NodeControlService._REAPER_MIN_SILENCE_SEC,
        settings.node_command_default_timeout_sec,
    )


@pytest.mark.anyio()
async def test_a_silent_command_on_a_healthy_node_is_reaped(
    dbsession: AsyncSession,
) -> None:
    """The exact regression: the node is fine, the command is not.

    Reaping only OFFLINE nodes left these in flight indefinitely.
    """
    service = _service(dbsession)
    node = await _node(dbsession, status=NodeHealthStatus.HEALTHY.value)
    command = await _inflight_command(
        dbsession, node, dispatched_ago_sec=_silence_window() + 600
    )

    assert await service.reap_stale_commands() >= 1
    await dbsession.refresh(command)
    assert command.status == NodeCommandStatus.TIMED_OUT.value
    # The message has to say the node was reachable, or it sends the operator
    # looking for a network fault that did not happen.
    assert "reachable" in (command.error_message or "")


@pytest.mark.anyio()
async def test_a_recently_dispatched_command_is_left_alone(
    dbsession: AsyncSession,
) -> None:
    service = _service(dbsession)
    node = await _node(dbsession, status=NodeHealthStatus.HEALTHY.value)
    command = await _inflight_command(dbsession, node, dispatched_ago_sec=90)

    await service.reap_stale_commands()
    await dbsession.refresh(command)
    assert command.status == NodeCommandStatus.DISPATCHED.value


@pytest.mark.anyio()
async def test_a_command_still_reporting_progress_is_left_alone(
    dbsession: AsyncSession,
) -> None:
    """Why progress, not the clock, is the discrimination.

    An 11GB image transfer runs far past any per-command timeout and is
    perfectly healthy; it just streams events while it does.
    """
    service = _service(dbsession)
    dao = NodeControlDAO(dbsession)
    node = await _node(dbsession, status=NodeHealthStatus.HEALTHY.value)
    command = await _inflight_command(
        dbsession, node, dispatched_ago_sec=_silence_window() + 600
    )
    # ...but it said something just now.
    await dao.append_command_event(
        command_id=command.id,
        phase="progress",
        message="transferred 6.2GB of 11GB",
        payload_json=None,
    )
    await dbsession.flush()

    await service.reap_stale_commands()
    await dbsession.refresh(command)
    assert command.status == NodeCommandStatus.DISPATCHED.value


@pytest.mark.anyio()
async def test_an_offline_node_still_uses_its_grace_period(
    dbsession: AsyncSession,
) -> None:
    """A briefly unreachable node may reconnect and re-dispatch."""
    service = _service(dbsession)
    node = await _node(dbsession, status=NodeHealthStatus.OFFLINE.value)
    command = await _inflight_command(dbsession, node, dispatched_ago_sec=120)

    await service.reap_stale_commands()
    await dbsession.refresh(command)
    assert command.status == NodeCommandStatus.DISPATCHED.value


# ── sessions ─────────────────────────────────────────────────────────────


@pytest.mark.anyio()
async def test_a_session_that_stopped_heartbeating_is_closed(
    dbsession: AsyncSession,
) -> None:
    service = _service(dbsession)
    dao = NodeControlDAO(dbsession)
    node = await _node(dbsession, status=NodeHealthStatus.HEALTHY.value)
    credential_id = uuid.uuid4()
    await dao.create_credential(
        node_id=node.id, credential_id=credential_id, secret_hash="x" * 32
    )
    session_row = await dao.create_session(node_id=node.id, credential_id=credential_id)
    session_row.last_heartbeat_at = datetime.now(tz=UTC) - timedelta(
        seconds=NodeControlService._SESSION_STALE_SEC + 600
    )
    await dbsession.flush()

    assert await service.close_stale_sessions() == 1
    await dbsession.refresh(session_row)
    assert session_row.disconnected_at is not None


@pytest.mark.anyio()
async def test_a_live_session_is_not_closed(dbsession: AsyncSession) -> None:
    """A brief stall must never disconnect a working agent."""
    service = _service(dbsession)
    dao = NodeControlDAO(dbsession)
    node = await _node(dbsession, status=NodeHealthStatus.HEALTHY.value)
    credential_id = uuid.uuid4()
    await dao.create_credential(
        node_id=node.id, credential_id=credential_id, secret_hash="x" * 32
    )
    session_row = await dao.create_session(node_id=node.id, credential_id=credential_id)
    session_row.last_heartbeat_at = datetime.now(tz=UTC) - timedelta(seconds=20)
    await dbsession.flush()

    assert await service.close_stale_sessions() == 0
    await dbsession.refresh(session_row)
    assert session_row.disconnected_at is None


@pytest.mark.anyio()
async def test_a_session_that_never_heartbeated_still_expires(
    dbsession: AsyncSession,
) -> None:
    """An agent that connected and died before its first heartbeat."""
    service = _service(dbsession)
    dao = NodeControlDAO(dbsession)
    node = await _node(dbsession, status=NodeHealthStatus.HEALTHY.value)
    credential_id = uuid.uuid4()
    await dao.create_credential(
        node_id=node.id, credential_id=credential_id, secret_hash="x" * 32
    )
    session_row = await dao.create_session(node_id=node.id, credential_id=credential_id)
    session_row.last_heartbeat_at = None
    session_row.connected_at = datetime.now(tz=UTC) - timedelta(
        seconds=NodeControlService._SESSION_STALE_SEC + 600
    )
    await dbsession.flush()

    assert await service.close_stale_sessions() == 1


# ── a failure with no reason is not a report ─────────────────────────────


def test_the_failure_reason_comes_from_the_deployment_not_the_app() -> None:
    """Ray puts the cause one level down from where this used to read it.

    The application's own ``message`` is usually empty, so the operator got
    "application DEPLOY_FAILED:" and nothing after the colon -- for a failure
    whose cause was sitting in the deployment beneath it.
    """
    from llm_port_backend.services.inference.drivers.ray.deployment import (
        _failure_detail,
    )

    app = {
        "message": "",
        "deployments": {
            "OpenAiIngress": {"status": "HEALTHY", "message": ""},
            "LLMServer:Qwen": {
                "status": "DEPLOY_FAILED",
                "message": (
                    "ValueError: Free memory on device cuda:0 (109.83/121.69 GiB) "
                    "on startup is less than desired GPU memory utilization"
                ),
            },
        },
    }
    detail = _failure_detail(app)
    assert "Free memory on device cuda:0" in detail
    assert detail != ""


def test_a_long_traceback_is_truncated_not_dumped() -> None:
    from llm_port_backend.services.inference.drivers.ray.deployment import (
        _failure_detail,
    )

    app = {"deployments": {"LLMServer:Q": {"status": "DEPLOY_FAILED", "message": "x" * 5_000}}}
    detail = _failure_detail(app)
    assert len(detail) < 1_000
    assert detail.endswith("...")


def test_a_replica_that_failed_to_start_says_why() -> None:
    """The live one: the reason is the traceback's last line, not its first.

    Cut at the limit, the message read "... Error: ray::ServeReplica... File
    ... return self. ..." and the operator never saw why.
    """
    from llm_port_backend.services.inference.drivers.ray.deployment import (
        _failure_detail,
    )

    lines = [
        "The deployment failed to start 3 times in a row. This may be due to a problem with its "
        "constructor or initial health check failing. See controller logs for details. Error:",
        "ray::ServeReplica:llmport-a34b:LLMServer:Qwen3-0_6B.initialize_and_get_metadata() (pid=1)",
        '  File "/usr/lib/python3.12/concurrent/futures/_base.py", line 449, in result',
        "    return self.__get_result()",
        *(["    ^^^^^^^^^^^^^^^^^^^"] * 40),
        "RuntimeError: Traceback (most recent call last):",
        "ray.exceptions.RayTaskError(RuntimeError): ray::_get_vllm_engine_config() (pid=2)",
        '  File "/usr/local/lib/python3.12/dist-packages/ray/llm/vllm_engine.py", line 206',
        "RuntimeError: Failed to create vLLM engine config: Cannot find an appropriate cached "
        "snapshot folder for the specified revision on the local disk and outgoing traffic has "
        "been disabled.",
    ]
    message = "\n".join(lines)
    app = {"deployments": {"LLMServer:Qwen3-0_6B": {"status": "DEPLOY_FAILED", "message": message}}}
    detail = _failure_detail(app)
    assert detail.startswith("The deployment failed to start 3 times in a row.")
    assert "RuntimeError: Failed to create vLLM engine config: Cannot find an appropriate cached" in detail
    assert "return self" not in detail


def test_no_app_means_no_claim() -> None:
    from llm_port_backend.services.inference.drivers.ray.deployment import (
        _failure_detail,
    )

    assert _failure_detail(None) == ""
    assert _failure_detail({}) == ""


def test_the_default_memory_fraction_fits_unified_memory() -> None:
    """0.92 of *total* is unreachable when the OS holds several GB of it.

    On GB10 the accelerator's memory is system RAM, so the engine measures
    its budget against a number the operating system is already spending
    from. The first replica on a fresh node fits and the second does not,
    which presents as "scaling does nothing".
    """
    from llm_port_backend.services.inference.drivers.ray.compiler import (
        DEFAULT_GPU_MEMORY_UTILIZATION,
        _engine_kwargs,
    )

    # Headroom on a 122GiB node must exceed what an OS plus an agent use.
    assert DEFAULT_GPU_MEMORY_UTILIZATION <= 0.85
    assert (1 - DEFAULT_GPU_MEMORY_UTILIZATION) * 121.69 > 12

    applied = _engine_kwargs(
        engine_config={}, tensor_parallel_size=None, pipeline_parallel_size=None
    )
    assert applied["gpu_memory_utilization"] == DEFAULT_GPU_MEMORY_UTILIZATION

    # An operator who wants the last of it still can.
    override = _engine_kwargs(
        engine_config={"gpu_memory_utilization": 0.95},
        tensor_parallel_size=None,
        pipeline_parallel_size=None,
    )
    assert override["gpu_memory_utilization"] == 0.95


@pytest.mark.anyio()
async def test_a_short_command_is_not_given_an_image_transfers_patience(
    dbsession: AsyncSession,
) -> None:
    """A 300s command declared dead on a 300s scale, not a 1800s one.

    The silence window used to be one constant sized for the slowest thing a
    node ever does -- a multi-GB image push. Every short command inherited it,
    so a `run_serve_app` that died on arrival held its deployment in flight
    for half an hour while the row reported the phase before it. Nobody
    watching a deploy waits that long to be told nothing is happening.
    """
    service = _service(dbsession)
    node = await _node(dbsession, status=NodeHealthStatus.HEALTHY.value)

    # Silent for well past its own budget, nowhere near the old flat window.
    short_budget = 300
    command = await _inflight_command(
        dbsession, node, dispatched_ago_sec=short_budget * 3
    )
    command.timeout_sec = short_budget
    await dbsession.flush()

    assert service._silence_budget(command) == short_budget
    assert await service.reap_stale_commands() >= 1
    await dbsession.refresh(command)
    assert command.status == NodeCommandStatus.TIMED_OUT.value


@pytest.mark.anyio()
async def test_a_long_command_keeps_its_longer_window(
    dbsession: AsyncSession,
) -> None:
    """The transfer the old constant existed to protect still is protected."""
    service = _service(dbsession)
    node = await _node(dbsession, status=NodeHealthStatus.HEALTHY.value)

    long_budget = 3600
    command = await _inflight_command(dbsession, node, dispatched_ago_sec=900)
    command.timeout_sec = long_budget
    await dbsession.flush()

    assert service._silence_budget(command) == long_budget
    await dbsession.refresh(command)
    assert command.status != NodeCommandStatus.TIMED_OUT.value


# -- a command dies with the socket that carried it -------------------------


async def test_a_command_in_flight_when_the_stream_closes_is_failed_at_once(
    dbsession: AsyncSession,
) -> None:
    """The proof is the disconnect, not the silence that follows it.

    A command runs inside the agent holding the socket. When the socket goes
    the work goes with it -- the agent resumes nothing on reconnect, so the
    result frame is never coming.

    Waiting for the reaper was not enough in practice. Pulling a runtime
    image saturates the link, times out the websocket keepalive and drops the
    stream carrying the command that started the pull. The node reconnects
    seconds later, so it is never offline long enough for the offline path,
    and the command sat in RUNNING for the whole silence budget while the
    cluster showed "preparing" and explained nothing.
    """
    service = _service(dbsession)
    node = await _node(dbsession, status=NodeHealthStatus.HEALTHY.value)
    command = await _inflight_command(dbsession, node, dispatched_ago_sec=5)
    command.status = NodeCommandStatus.RUNNING.value
    await dbsession.flush()

    failed = await service._fail_commands_lost_with_the_stream(node_id=node.id)

    assert failed == 1
    await dbsession.refresh(command)
    assert command.status == NodeCommandStatus.FAILED.value
    assert command.error_code == "node_stream_lost"


async def test_a_model_copy_that_died_with_the_stream_is_retried_not_waited_on(
    dbsession: AsyncSession,
) -> None:
    """Found on the DGX pair: the copy failed, its row still read "syncing".

    The deployment then waited for a copy nothing was doing, until the row
    went stale an hour later. A failed row is retried after the usual backoff.
    """
    from llm_port_backend.db.dao.inference_dao import ModelAvailabilityDAO
    from llm_port_backend.db.models.inference import ModelAvailabilityStatus
    from llm_port_backend.db.models.llm import LLMModel, ModelSource, ModelStatus

    service = _service(dbsession)
    node = await _node(dbsession, status=NodeHealthStatus.HEALTHY.value)
    model = LLMModel(display_name="Qwen3-0.6B", source=ModelSource.HUGGINGFACE,
                     status=ModelStatus.AVAILABLE, hf_repo_id="Qwen/Qwen3-0.6B")
    dbsession.add(model)
    await dbsession.flush()
    await ModelAvailabilityDAO(dbsession).mark(model_id=model.id, node_id=node.id,
                                               status=ModelAvailabilityStatus.SYNCING)
    command = await _inflight_command(dbsession, node, dispatched_ago_sec=5)
    command.command_type = NodeCommandType.SYNC_MODEL.value
    command.payload_json = {"model_id": str(model.id)}
    command.status = NodeCommandStatus.RUNNING.value
    await dbsession.flush()

    assert await service._fail_commands_lost_with_the_stream(node_id=node.id) == 1

    row = await ModelAvailabilityDAO(dbsession).get(model.id, node.id)
    assert row is not None
    assert row.status == ModelAvailabilityStatus.FAILED.value
    assert "connection closed" in (row.status_message or "")


async def test_the_reason_tells_the_operator_it_is_safe_to_retry(
    dbsession: AsyncSession,
) -> None:
    """"It failed" without "and nothing is left running" invites a guess."""
    service = _service(dbsession)
    node = await _node(dbsession, status=NodeHealthStatus.HEALTHY.value)
    command = await _inflight_command(dbsession, node, dispatched_ago_sec=5)
    command.status = NodeCommandStatus.RUNNING.value
    await dbsession.flush()

    await service._fail_commands_lost_with_the_stream(node_id=node.id)
    await dbsession.refresh(command)

    assert "connection closed" in (command.error_message or "")
    assert "try again" in (command.error_message or "")


async def test_a_node_with_nothing_in_flight_is_untouched(
    dbsession: AsyncSession,
) -> None:
    service = _service(dbsession)
    node = await _node(dbsession, status=NodeHealthStatus.HEALTHY.value)

    assert await service._fail_commands_lost_with_the_stream(node_id=node.id) == 0


async def test_another_nodes_commands_are_not_collateral(
    dbsession: AsyncSession,
) -> None:
    """One machine losing its stream says nothing about any other."""
    service = _service(dbsession)
    mine = await _node(dbsession, status=NodeHealthStatus.HEALTHY.value)
    theirs = await _node(dbsession, status=NodeHealthStatus.HEALTHY.value)
    untouched = await _inflight_command(dbsession, theirs, dispatched_ago_sec=5)
    untouched.status = NodeCommandStatus.RUNNING.value
    await dbsession.flush()

    await service._fail_commands_lost_with_the_stream(node_id=mine.id)

    await dbsession.refresh(untouched)
    assert untouched.status == NodeCommandStatus.RUNNING.value


async def test_a_killed_agents_commands_are_failed_when_its_session_is_reaped(
    dbsession: AsyncSession,
) -> None:
    """The clean path never runs for an agent that was killed.

    `close_stream_session` fires from the websocket teardown, which a killed
    agent never reaches. Its commands then stay in flight on a node that
    looks perfectly healthy as soon as it restarts -- which is how a cluster
    sat at "preparing" pointing at a command from an agent that no longer
    existed.
    """
    service = _service(dbsession)
    node = await _node(dbsession, status=NodeHealthStatus.HEALTHY.value)

    dao = NodeControlDAO(dbsession)
    credential = await dao.create_credential(
        node_id=node.id, credential_id=uuid.uuid4(), secret_hash="x"
    )
    session_row = await dao.create_session(node_id=node.id, credential_id=credential.id)
    # Silent for long enough that the reaper will take it.
    session_row.connected_at = datetime.now(tz=UTC) - timedelta(days=1)
    session_row.last_heartbeat_at = datetime.now(tz=UTC) - timedelta(days=1)

    command = await _inflight_command(dbsession, node, dispatched_ago_sec=5)
    command.status = NodeCommandStatus.RUNNING.value
    await dbsession.flush()

    await service.close_stale_sessions()

    await dbsession.refresh(command)
    assert command.status == NodeCommandStatus.FAILED.value
    assert command.error_code == "node_stream_lost"


async def test_a_live_session_keeps_its_commands(dbsession: AsyncSession) -> None:
    """A long transfer on a connected agent must not be cut short."""
    service = _service(dbsession)
    node = await _node(dbsession, status=NodeHealthStatus.HEALTHY.value)

    dao = NodeControlDAO(dbsession)
    credential = await dao.create_credential(
        node_id=node.id, credential_id=uuid.uuid4(), secret_hash="x"
    )
    session_row = await dao.create_session(node_id=node.id, credential_id=credential.id)
    session_row.last_heartbeat_at = datetime.now(tz=UTC)

    command = await _inflight_command(dbsession, node, dispatched_ago_sec=5)
    command.status = NodeCommandStatus.RUNNING.value
    await dbsession.flush()

    await service.close_stale_sessions()

    await dbsession.refresh(command)
    assert command.status == NodeCommandStatus.RUNNING.value


async def test_a_killed_agents_machine_goes_offline_when_its_session_is_reaped(
    dbsession: AsyncSession,
) -> None:
    """Only the clean teardown used to mark a machine offline.

    A killed agent -- or one cut off by a backend restart -- never runs it,
    so its machine read "healthy" with nothing running on it, indefinitely.
    """
    service = _service(dbsession)
    node = await _node(dbsession, status=NodeHealthStatus.HEALTHY.value)
    dao = NodeControlDAO(dbsession)
    credential = await dao.create_credential(node_id=node.id, credential_id=uuid.uuid4(), secret_hash="x")
    session_row = await dao.create_session(node_id=node.id, credential_id=credential.id)
    session_row.connected_at = datetime.now(tz=UTC) - timedelta(days=1)
    session_row.last_heartbeat_at = datetime.now(tz=UTC) - timedelta(days=1)
    await dbsession.flush()

    await service.close_stale_sessions()

    await dbsession.refresh(node)
    assert node.status == NodeHealthStatus.OFFLINE.value


async def test_a_machine_silent_with_no_stream_at_all_goes_offline(dbsession: AsyncSession) -> None:
    """Sessions already closed some other way are never seen by the reaper again."""
    service = _service(dbsession)
    node = await _node(dbsession, status=NodeHealthStatus.HEALTHY.value)
    node.last_seen = datetime.now(tz=UTC) - timedelta(hours=1)
    await dbsession.flush()

    await service.close_stale_sessions()

    await dbsession.refresh(node)
    assert node.status == NodeHealthStatus.OFFLINE.value


async def test_a_machine_that_is_still_connected_stays_up(dbsession: AsyncSession) -> None:
    service = _service(dbsession)
    node = await _node(dbsession, status=NodeHealthStatus.HEALTHY.value)
    node.last_seen = datetime.now(tz=UTC) - timedelta(hours=1)
    dao = NodeControlDAO(dbsession)
    credential = await dao.create_credential(node_id=node.id, credential_id=uuid.uuid4(), secret_hash="x")
    live = await dao.create_session(node_id=node.id, credential_id=credential.id)
    live.last_heartbeat_at = datetime.now(tz=UTC)
    await dbsession.flush()

    await service.close_stale_sessions()

    await dbsession.refresh(node)
    assert node.status == NodeHealthStatus.HEALTHY.value


async def test_a_sweep_that_only_marks_machines_offline_still_asks_to_be_committed(
    dbsession: AsyncSession,
) -> None:
    """The reaper commits only when this returns non-zero."""
    service = _service(dbsession)
    node = await _node(dbsession, status=NodeHealthStatus.HEALTHY.value)
    node.last_seen = datetime.now(tz=UTC) - timedelta(hours=1)
    await dbsession.flush()

    assert await service.close_stale_sessions() >= 1
