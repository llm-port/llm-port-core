"""Dispatch latency: the backend pushes rather than waits to be spoken to.

Measured on the DGX pair before this change, a trivial node command took
30-44s end to end, of which 0.0-0.1s was the work.  The rest was structural:
the stream loop drained the command queue only after a frame arrived *from the
agent*, so a command's latency was really "time until the agent's next
heartbeat".  Nothing was slow; nothing was looking.

These pin the three things that have to hold for a push to be both fast and
safe:

  * the wake-up fires when the command is **visible**, not when it is written;
  * the receive in flight is not thrown away to service a wake-up;
  * a node that has genuinely gone quiet still times out on schedule, however
    often the loop was woken in the meantime.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.services.nodes.wakeup import (
    CommandNotifier,
    dsn_for_asyncpg,
)


@pytest.fixture()
def notifier() -> CommandNotifier:
    return CommandNotifier()


# ── the event itself ─────────────────────────────────────────────────────


@pytest.mark.anyio()
async def test_a_waiter_is_released_when_its_node_is_woken(
    notifier: CommandNotifier,
) -> None:
    node_id = uuid.uuid4()
    waiter = asyncio.create_task(notifier.wait_for(node_id))
    await asyncio.sleep(0)

    notifier.wake(node_id)
    assert await asyncio.wait_for(waiter, timeout=1.0) is True


@pytest.mark.anyio()
async def test_one_nodes_command_does_not_wake_another(
    notifier: CommandNotifier,
) -> None:
    """A shared event would turn every command into a fleet-wide poll."""
    mine, theirs = uuid.uuid4(), uuid.uuid4()
    waiter = asyncio.create_task(notifier.wait_for(mine, timeout=0.2))
    await asyncio.sleep(0)

    notifier.wake(theirs)
    assert await waiter is False


@pytest.mark.anyio()
async def test_a_wake_up_arriving_first_is_not_lost(
    notifier: CommandNotifier,
) -> None:
    """The command may be queued before the stream comes round to waiting.

    Losing that edge would leave the command sitting until the next heartbeat
    -- exactly the behaviour being removed.
    """
    node_id = uuid.uuid4()
    notifier.wake(node_id)

    assert await notifier.wait_for(node_id, timeout=0.5) is True


@pytest.mark.anyio()
async def test_the_event_is_cleared_so_the_next_command_wakes_again(
    notifier: CommandNotifier,
) -> None:
    node_id = uuid.uuid4()
    notifier.wake(node_id)
    assert await notifier.wait_for(node_id, timeout=0.5) is True

    # Nothing new queued -> nothing to report.
    assert await notifier.wait_for(node_id, timeout=0.05) is False

    notifier.wake(node_id)
    assert await notifier.wait_for(node_id, timeout=0.5) is True


@pytest.mark.anyio()
async def test_waiting_for_a_silent_node_times_out(notifier: CommandNotifier) -> None:
    assert await notifier.wait_for(uuid.uuid4(), timeout=0.05) is False


@pytest.mark.anyio()
async def test_forgetting_a_node_does_not_disturb_the_others(
    notifier: CommandNotifier,
) -> None:
    """Streams close constantly, and a close must disturb nothing."""
    kept, dropped = uuid.uuid4(), uuid.uuid4()
    notifier.wake(kept)
    notifier.forget(dropped)
    notifier.forget(dropped)  # idempotent: a double close is normal

    assert await notifier.wait_for(kept, timeout=0.5) is True


@pytest.mark.anyio()
async def test_a_reconnect_does_not_strand_the_new_stream(
    notifier: CommandNotifier,
) -> None:
    """The race that put a node back on 30-second dispatch.

    An agent reconnect overlaps: the new stream starts waiting before the old
    one's cleanup runs. If that cleanup drops the node's event, the new stream
    is left holding an event nobody will ever set, the next command creates a
    third one, and the queue is only drained when the idle timeout fires.

    Measured on the DGX head after a restart: every command dispatched exactly
    30.0s after it was issued -- ``node_stream_idle_timeout_sec`` to the
    second -- while the node that had not reconnected dispatched in under a
    second. Two nodes, same build, same code path, and the only difference was
    that one of them had reconnected.
    """
    node_id = uuid.uuid4()

    # The new stream is already waiting...
    waiter = asyncio.create_task(notifier.wait_for(node_id, timeout=2.0))
    await asyncio.sleep(0)

    # ...when the old connection's cleanup finally runs.
    notifier.forget(node_id)

    notifier.wake(node_id)
    assert await waiter is True, "the reconnected stream was left stranded"


@pytest.mark.anyio()
async def test_repeated_reconnects_do_not_accumulate(
    notifier: CommandNotifier,
) -> None:
    """Keyed by node, not by connection.

    This is why dropping the event on close buys nothing: however many times
    a machine reconnects, it holds one entry.
    """
    node_id = uuid.uuid4()
    for _ in range(50):
        notifier.wake(node_id)
        assert await notifier.wait_for(node_id, timeout=0.5) is True
        notifier.forget(node_id)

    assert len(notifier._events) == 1  # noqa: SLF001 - the point of the test


# ── the commit boundary ──────────────────────────────────────────────────


@pytest.mark.anyio()
async def test_nothing_is_woken_before_the_command_is_visible(
    dbsession: AsyncSession, notifier: CommandNotifier
) -> None:
    """The whole point of hanging the wake-up off the commit.

    A reader woken while the insert is still uncommitted queries the queue,
    finds nothing, and goes back to sleep for a full idle period -- which is
    worse than not pushing at all, because the wake-up was consumed.
    """
    node_id = uuid.uuid4()
    await notifier.notify(dbsession, node_id)

    assert await notifier.wait_for(node_id, timeout=0.05) is False


@pytest.mark.anyio()
async def test_the_commit_is_what_wakes_the_stream(
    dbsession: AsyncSession, notifier: CommandNotifier
) -> None:
    node_id = uuid.uuid4()
    await notifier.notify(dbsession, node_id)
    await dbsession.commit()

    assert await notifier.wait_for(node_id, timeout=1.0) is True


@pytest.mark.anyio()
async def test_a_rolled_back_command_wakes_nobody(
    dbsession: AsyncSession, notifier: CommandNotifier
) -> None:
    """There is no command to dispatch, so there is nothing to wake for."""
    node_id = uuid.uuid4()
    await notifier.notify(dbsession, node_id)
    await dbsession.rollback()

    assert await notifier.wait_for(node_id, timeout=0.05) is False


@pytest.mark.anyio()
async def test_two_commands_in_one_transaction_wake_once_each(
    dbsession: AsyncSession, notifier: CommandNotifier
) -> None:
    """Batched work (a fleet-wide deploy) must not drop a node's wake-up."""
    first, second = uuid.uuid4(), uuid.uuid4()
    await notifier.notify(dbsession, first)
    await notifier.notify(dbsession, second)
    await dbsession.commit()

    assert await notifier.wait_for(first, timeout=1.0) is True
    assert await notifier.wait_for(second, timeout=1.0) is True


# ── issuing a command ────────────────────────────────────────────────────


@pytest.mark.anyio()
async def test_issuing_a_command_arms_a_wake_up(dbsession: AsyncSession) -> None:
    """The integration point: the service, not the caller, arms the push."""
    from llm_port_backend.db.dao.node_control_dao import NodeControlDAO
    from llm_port_backend.db.models.node_control import InfraNode
    from llm_port_backend.services.nodes import NodeControlService
    from llm_port_backend.services.nodes.wakeup import get_command_notifier

    node = InfraNode(
        agent_id=f"agent-{uuid.uuid4().hex[:8]}",
        host="10.88.10.49",
        status="healthy",
        capabilities_json={},
    )
    dbsession.add(node)
    await dbsession.flush()

    service = NodeControlService(
        NodeControlDAO(dbsession),
        pepper="pepper",
        enrollment_ttl_minutes=60,
        default_command_timeout_sec=90,
    )
    await service.issue_command(
        node_id=node.id,
        command_type="cluster-status",
        payload={},
        issued_by=None,
        correlation_id=None,
        timeout_sec=None,
        idempotency_key=None,
    )
    await dbsession.commit()

    assert await get_command_notifier().wait_for(node.id, timeout=1.0) is True


# ── the listener's connection string ─────────────────────────────────────


def test_the_listener_dials_without_sqlalchemys_driver_marker() -> None:
    """asyncpg does not understand ``postgresql+asyncpg://`` and will not say so
    helpfully; it raises on the scheme."""
    assert (
        dsn_for_asyncpg("postgresql+asyncpg://u:p@db:5432/llmport")
        == "postgresql://u:p@db:5432/llmport"
    )


def test_a_plain_url_is_left_alone() -> None:
    assert (
        dsn_for_asyncpg("postgresql://u:p@db:5432/llmport")
        == "postgresql://u:p@db:5432/llmport"
    )


# ── the stream loop's wait ───────────────────────────────────────────────


@pytest.mark.anyio()
async def test_a_frame_returns_as_a_frame(notifier: CommandNotifier) -> None:
    from llm_port_backend.web.api.admin.system.views import _await_frame_or_wakeup

    async def receive() -> dict[str, str]:
        return {"type": "heartbeat"}

    payload, in_flight = await _await_frame_or_wakeup(
        None, receive, notifier, uuid.uuid4(), timeout=1.0
    )
    assert payload == {"type": "heartbeat"}
    assert in_flight is None


@pytest.mark.anyio()
async def test_a_wake_up_does_not_discard_the_receive_in_flight(
    notifier: CommandNotifier,
) -> None:
    """The reason the receive is handed back rather than cancelled.

    ``receive_json`` reassembles a frame across reads.  Cancelling it to
    service a wake-up throws that away, and on this socket the frame is a
    command result or a heartbeat -- so the command that was just dispatched
    would look like it never reported back.
    """
    node_id = uuid.uuid4()
    started = asyncio.Event()
    cancelled = False

    async def receive() -> dict[str, str]:
        nonlocal cancelled
        started.set()
        try:
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            cancelled = True
            raise
        return {"type": "command_result"}

    first = asyncio.create_task(
        _call(notifier, node_id, receive, timeout=2.0, recv_task=None)
    )
    await started.wait()
    notifier.wake(node_id)

    payload, in_flight = await asyncio.wait_for(first, timeout=2.0)
    assert payload is None, "a wake-up is not a frame"
    assert in_flight is not None and not in_flight.done()
    assert cancelled is False

    in_flight.cancel()


@pytest.mark.anyio()
async def test_the_carried_receive_is_the_same_task(
    notifier: CommandNotifier,
) -> None:
    """Passing it back in must resume it, not start a second read."""
    from llm_port_backend.web.api.admin.system.views import _await_frame_or_wakeup

    node_id = uuid.uuid4()
    starts = 0
    release = asyncio.Event()

    async def receive() -> dict[str, str]:
        nonlocal starts
        starts += 1
        await release.wait()
        return {"type": "heartbeat"}

    notifier.wake(node_id)
    _payload, in_flight = await _await_frame_or_wakeup(
        None, receive, notifier, node_id, timeout=1.0
    )
    assert starts == 1

    release.set()
    payload, _ = await _await_frame_or_wakeup(
        in_flight, receive, notifier, node_id, timeout=1.0
    )
    assert payload == {"type": "heartbeat"}
    assert starts == 1, "the receive was restarted, so a frame was dropped"


@pytest.mark.anyio()
async def test_nothing_happening_returns_neither(notifier: CommandNotifier) -> None:
    """How the caller learns to check the idle clock."""
    from llm_port_backend.web.api.admin.system.views import _await_frame_or_wakeup

    async def receive() -> dict[str, str]:
        await asyncio.sleep(5)
        return {}

    payload, in_flight = await _await_frame_or_wakeup(
        None, receive, notifier, uuid.uuid4(), timeout=0.05
    )
    assert payload is None
    assert in_flight is not None
    in_flight.cancel()


@pytest.mark.anyio()
async def test_a_disconnect_reaches_the_caller(notifier: CommandNotifier) -> None:
    """Swallowing it would leave the loop spinning on a dead socket."""
    from starlette.websockets import WebSocketDisconnect

    from llm_port_backend.web.api.admin.system.views import _await_frame_or_wakeup

    async def receive() -> dict[str, str]:
        raise WebSocketDisconnect(code=1001)

    with pytest.raises(WebSocketDisconnect):
        await _await_frame_or_wakeup(
            None, receive, notifier, uuid.uuid4(), timeout=1.0
        )


async def _call(notifier, node_id, receive, *, timeout, recv_task):
    from llm_port_backend.web.api.admin.system.views import _await_frame_or_wakeup

    return await _await_frame_or_wakeup(
        recv_task, receive, notifier, node_id, timeout=timeout
    )
