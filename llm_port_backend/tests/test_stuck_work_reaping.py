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
        dbsession, node, dispatched_ago_sec=NodeControlService._REAPER_SILENCE_SEC + 600
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
        dbsession, node, dispatched_ago_sec=NodeControlService._REAPER_SILENCE_SEC + 600
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
