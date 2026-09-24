"""The machines' history is pruned; what the product reads is kept.

Each machine reports its inventory about once a minute and the health checks
issue commands every minute: measured on the release VM, one machine added
~1,440 snapshots (7 MB) a day and the pair ~36,000 command events. Nothing
deleted any of it (7,294 snapshots for one machine on the workstation).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.dao.node_control_dao import NodeControlDAO
from llm_port_backend.db.models.node_control import (
    InfraNode,
    InfraNodeCommand,
    InfraNodeCommandEvent,
    InfraNodeEvent,
    InfraNodeInventorySnapshot,
    NodeCommandStatus,
)
from llm_port_backend.services.nodes.service import NodeControlService

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)


def _node(name: str) -> InfraNode:
    return InfraNode(agent_id=f"{name}-{uuid.uuid4().hex[:6]}", host="10.0.0.1", status="healthy")


async def _command(dao: NodeControlDAO, node: InfraNode, *, status: str, age: timedelta) -> InfraNodeCommand:
    command = await dao.create_command(
        node_id=node.id, command_type="get_ray_status", payload_json={}, idempotency_key=uuid.uuid4().hex,
        issued_by=None, correlation_id=None, timeout_sec=60,
    )
    command.status = status
    command.issued_at = NOW - age
    return command


@pytest.mark.anyio
async def test_old_history_goes_and_what_is_read_stays(dbsession: AsyncSession) -> None:
    here, gone = _node("here"), _node("gone")
    dbsession.add_all([here, gone])
    await dbsession.flush()
    snapshots = {
        "here-2d": InfraNodeInventorySnapshot(node_id=here.id, inventory_json={}, utilization_json={},
                                              created_at=NOW - timedelta(days=2)),
        "here-1h": InfraNodeInventorySnapshot(node_id=here.id, inventory_json={}, utilization_json={},
                                              created_at=NOW - timedelta(hours=1)),
        # A machine that stopped reporting three days ago: its last word stays.
        "gone-4d": InfraNodeInventorySnapshot(node_id=gone.id, inventory_json={}, utilization_json={},
                                              created_at=NOW - timedelta(days=4)),
        "gone-3d": InfraNodeInventorySnapshot(node_id=gone.id, inventory_json={}, utilization_json={},
                                              created_at=NOW - timedelta(days=3)),
    }
    dbsession.add_all(snapshots.values())
    dao = NodeControlDAO(dbsession)
    old_done = await _command(dao, here, status=NodeCommandStatus.SUCCEEDED.value, age=timedelta(days=8))
    old_running = await _command(dao, here, status=NodeCommandStatus.RUNNING.value, age=timedelta(days=8))
    recent_done = await _command(dao, here, status=NodeCommandStatus.FAILED.value, age=timedelta(days=1))
    dbsession.add_all([
        InfraNodeEvent(node_id=here.id, event_type="x", created_at=NOW - timedelta(days=31)),
        InfraNodeEvent(node_id=here.id, event_type="y", created_at=NOW - timedelta(days=2)),
    ])
    await dbsession.commit()

    service = NodeControlService(dao, pepper="p", enrollment_ttl_minutes=10, default_command_timeout_sec=60)
    pruned = await service.prune_history(inventory_hours=24, command_days=7, event_days=30, now=NOW)

    assert pruned == {"inventory_snapshots": 2, "commands": 1, "events": 1}
    left = {s.id for s in (await dbsession.execute(select(InfraNodeInventorySnapshot))).scalars()}
    assert left == {snapshots["here-1h"].id, snapshots["gone-3d"].id}
    latest = await dao.latest_inventory_snapshots(node_ids=[here.id, gone.id])
    assert latest[gone.id].id == snapshots["gone-3d"].id, "still readable"

    commands = {c.id for c in (await dbsession.execute(select(InfraNodeCommand))).scalars()}
    assert old_done.id not in commands
    assert {old_running.id, recent_done.id} <= commands, "in flight is the reaper's; recent stays"
    orphans = (await dbsession.execute(
        select(InfraNodeCommandEvent).where(InfraNodeCommandEvent.command_id == old_done.id))).scalars().all()
    assert orphans == [], "its events went with it"
    assert [e.event_type for e in (await dbsession.execute(select(InfraNodeEvent))).scalars()] == ["y"]


@pytest.mark.anyio
async def test_a_large_backlog_is_deleted_in_batches(dbsession: AsyncSession) -> None:
    node = _node("busy")
    dbsession.add(node)
    await dbsession.flush()
    dbsession.add_all([
        InfraNodeEvent(node_id=node.id, event_type="old", created_at=NOW - timedelta(days=40, minutes=i))
        for i in range(12)
    ])
    await dbsession.commit()
    dao = NodeControlDAO(dbsession)
    assert await dao.prune_node_events(before=NOW - timedelta(days=30), batch=5) == 5
    await dbsession.commit()
    service = NodeControlService(dao, pepper="p", enrollment_ttl_minutes=10, default_command_timeout_sec=60)
    pruned = await service.prune_history(inventory_hours=24, command_days=7, event_days=30, now=NOW)
    assert pruned["events"] == 7
