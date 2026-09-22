"""What the cluster knows about a machine, written onto the machine's row.

``member_status`` was renamed from ``ray_status`` when the column was
generalised across backends. The rename moved the column and left nothing
writing to it, so every membership row stayed ``NULL`` however healthy the
cluster was.

That is not a cosmetic gap. The topology diagram picks each machine's ring
colour from this field and treats null as "not reporting", so a two-node
cluster that was serving traffic drew two grey circles joined by dashed lines
-- the same picture it draws for a cluster that is down. The one distinction
that view exists to make was the one it could not make.

The hard part is matching Ray's nodes to ours: Ray identifies a node by its own
56-hex id, which is not one of ours, and addresses it by whatever address the
cluster was brought up on -- the isolated fabric link here, not the address we
know the machine by.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.models.inference import (
    InferenceControlPlane,
    InferenceEnvironment,
    InferenceEnvironmentNode,
)
from llm_port_backend.db.models.node_control import InfraNode
from llm_port_backend.services.inference.reconciliation import _sync_member_status


class _Ctx:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session


async def _node(session: AsyncSession, host: str) -> InfraNode:
    node = InfraNode(
        agent_id=f"agent-{uuid.uuid4().hex[:8]}",
        host=host,
        status="healthy",
        capabilities_json={},
    )
    session.add(node)
    await session.flush()
    return node


def _ray_node(ip: str, *, alive: bool, head: bool = False) -> dict[str, Any]:
    return {
        "alive": alive,
        "is_head": head,
        # Ray's own id, deliberately unlike ours.
        "node_id": uuid.uuid4().hex + uuid.uuid4().hex[:24],
        "node_ip": ip,
        "node_name": ip,
        "node_manager_address": ip,
        "metrics_export_port": 40535,
    }


async def _environment(
    session: AsyncSession,
    *,
    ray_nodes: list[dict[str, Any]],
    bindings: dict[str, Any] | None = None,
) -> tuple[InferenceEnvironment, list[InferenceEnvironmentNode]]:
    control_plane = InferenceControlPlane(
        name=f"cp-{uuid.uuid4().hex[:6]}", driver="ray"
    )
    session.add(control_plane)
    await session.flush()
    env = InferenceEnvironment(
        control_plane_id=control_plane.id,
        name=f"env-{uuid.uuid4().hex[:6]}",
        desired_state="running",
        config_json={"resolved_fabric": {"node_bindings": bindings or {}}},
        observed_status_json={"cluster": {"alive": True, "nodes": ray_nodes}},
    )
    session.add(env)
    await session.flush()
    return env, []


async def _member(
    session: AsyncSession,
    env: InferenceEnvironment,
    node: InfraNode,
    role: str,
) -> InferenceEnvironmentNode:
    member = InferenceEnvironmentNode(
        environment_id=env.id, node_id=node.id, role=role, observed_json={}
    )
    session.add(member)
    await session.flush()
    return member


# ── the regression ───────────────────────────────────────────────────────


@pytest.mark.anyio()
async def test_a_live_cluster_stops_reading_as_not_reporting(
    dbsession: AsyncSession,
) -> None:
    """The exact case that drew two grey circles on a working cluster."""
    head = await _node(dbsession, "10.88.10.49")
    worker = await _node(dbsession, "10.88.10.71")
    env, _ = await _environment(
        dbsession,
        ray_nodes=[
            _ray_node("10.100.0.2", alive=True, head=True),
            _ray_node("10.100.0.1", alive=True),
        ],
        bindings={
            str(head.id): {"ip": "10.100.0.2"},
            str(worker.id): {"ip": "10.100.0.1"},
        },
    )
    head_member = await _member(dbsession, env, head, "head")
    worker_member = await _member(dbsession, env, worker, "worker")
    assert head_member.member_status is None  # the state being fixed

    await _sync_member_status(_Ctx(dbsession), env)

    assert head_member.member_status == "alive"
    assert worker_member.member_status == "alive"


@pytest.mark.anyio()
async def test_a_node_ray_reports_dead_is_recorded_dead(
    dbsession: AsyncSession,
) -> None:
    head = await _node(dbsession, "10.88.10.49")
    worker = await _node(dbsession, "10.88.10.71")
    env, _ = await _environment(
        dbsession,
        ray_nodes=[
            _ray_node("10.100.0.2", alive=True, head=True),
            _ray_node("10.100.0.1", alive=False),
        ],
        bindings={
            str(head.id): {"ip": "10.100.0.2"},
            str(worker.id): {"ip": "10.100.0.1"},
        },
    )
    head_member = await _member(dbsession, env, head, "head")
    worker_member = await _member(dbsession, env, worker, "worker")

    await _sync_member_status(_Ctx(dbsession), env)

    assert head_member.member_status == "alive"
    assert worker_member.member_status == "dead"


# ── matching Ray's nodes to ours ─────────────────────────────────────────


@pytest.mark.anyio()
async def test_a_cluster_on_management_addresses_matches_too(
    dbsession: AsyncSession,
) -> None:
    """Which address Ray reports depends on how the cluster came up.

    A cluster started without a fabric advertises the management address, and
    there is then no binding to bridge through -- so the node's own host has
    to be tried as well, or the whole fleet reads as unknown.
    """
    head = await _node(dbsession, "10.88.10.49")
    env, _ = await _environment(
        dbsession,
        ray_nodes=[_ray_node("10.88.10.49", alive=True, head=True)],
        bindings={},
    )
    member = await _member(dbsession, env, head, "head")

    await _sync_member_status(_Ctx(dbsession), env)

    assert member.member_status == "alive"


@pytest.mark.anyio()
async def test_a_machine_the_cluster_does_not_know_is_unknown_not_dead(
    dbsession: AsyncSession,
) -> None:
    """"Has not joined" and "has failed" are different things.

    Calling it dead would put a red ring on a machine that is simply still
    coming up, which is the kind of false alarm that gets a screen ignored.
    """
    head = await _node(dbsession, "10.88.10.49")
    stranger = await _node(dbsession, "10.88.10.99")
    env, _ = await _environment(
        dbsession,
        ray_nodes=[_ray_node("10.100.0.2", alive=True, head=True)],
        bindings={str(head.id): {"ip": "10.100.0.2"}},
    )
    await _member(dbsession, env, head, "head")
    outsider = await _member(dbsession, env, stranger, "worker")

    await _sync_member_status(_Ctx(dbsession), env)

    assert outsider.member_status == "unknown"


@pytest.mark.anyio()
async def test_an_unobserved_cluster_leaves_the_rows_alone(
    dbsession: AsyncSession,
) -> None:
    """A probe that has not happened is not evidence that a node is down.

    Writing "unknown" here would mean a backend restart made every healthy
    cluster look degraded until the next reconcile.
    """
    head = await _node(dbsession, "10.88.10.49")
    env, _ = await _environment(dbsession, ray_nodes=[])
    member = await _member(dbsession, env, head, "head")
    member.member_status = "alive"

    await _sync_member_status(_Ctx(dbsession), env)

    assert member.member_status == "alive"


# ── joined_at ────────────────────────────────────────────────────────────


@pytest.mark.anyio()
async def test_joining_is_stamped_once_and_not_rewritten(
    dbsession: AsyncSession,
) -> None:
    """The reconcile runs continuously; a churning timestamp is not a date."""
    head = await _node(dbsession, "10.88.10.49")
    env, _ = await _environment(
        dbsession,
        ray_nodes=[_ray_node("10.100.0.2", alive=True, head=True)],
        bindings={str(head.id): {"ip": "10.100.0.2"}},
    )
    member = await _member(dbsession, env, head, "head")

    await _sync_member_status(_Ctx(dbsession), env)
    first = member.joined_at
    assert first is not None

    await _sync_member_status(_Ctx(dbsession), env)
    assert member.joined_at == first


@pytest.mark.anyio()
async def test_a_node_that_never_joined_has_no_join_date(
    dbsession: AsyncSession,
) -> None:
    head = await _node(dbsession, "10.88.10.49")
    worker = await _node(dbsession, "10.88.10.71")
    env, _ = await _environment(
        dbsession,
        ray_nodes=[
            _ray_node("10.100.0.2", alive=True, head=True),
            _ray_node("10.100.0.1", alive=False),
        ],
        bindings={
            str(head.id): {"ip": "10.100.0.2"},
            str(worker.id): {"ip": "10.100.0.1"},
        },
    )
    await _member(dbsession, env, head, "head")
    worker_member = await _member(dbsession, env, worker, "worker")

    await _sync_member_status(_Ctx(dbsession), env)

    assert worker_member.joined_at is None


# ── never fail a reconcile over a display field ──────────────────────────


@pytest.mark.anyio()
async def test_a_malformed_observation_does_not_raise(
    dbsession: AsyncSession,
) -> None:
    head = await _node(dbsession, "10.88.10.49")
    env, _ = await _environment(dbsession, ray_nodes=[])
    env.observed_status_json = {"cluster": {"nodes": ["not a node", None, 7]}}
    await _member(dbsession, env, head, "head")

    # No exception, and nothing invented from nonsense.
    await _sync_member_status(_Ctx(dbsession), env)


# ── the other half of the same picture ───────────────────────────────────


@pytest.mark.anyio()
async def test_the_fleet_list_carries_each_machines_utilization(
    dbsession: AsyncSession,
) -> None:
    """The allocation arc's data, which the list endpoint used to drop.

    ``get_node`` attached the latest inventory snapshot and ``list_nodes`` did
    not, so anything reading the fleet saw ``latest_utilization: null`` for
    every machine however recently it had reported. The topology sizes each
    node's allocation arc from exactly that field, so the arc never drew -- it
    looked like a rendering bug and was a missing join.
    """
    from llm_port_backend.db.dao.node_control_dao import NodeControlDAO
    from llm_port_backend.services.nodes import NodeControlService

    node = await _node(dbsession, "10.88.10.49")
    dao = NodeControlDAO(dbsession)
    await dao.upsert_inventory_snapshot(
        node_id=node.id,
        inventory_json={"cpu": {"cores": 20}},
        utilization_json={"cpu_percent": 12.5, "gpu": {"used_percent": 64.0}},
    )
    await dbsession.flush()

    service = NodeControlService(
        dao,
        pepper="pepper",
        enrollment_ttl_minutes=60,
        default_command_timeout_sec=90,
    )
    rows = await service.list_nodes()

    mine = next(r for r in rows if r["id"] == str(node.id))
    assert mine["latest_utilization"] == {
        "cpu_percent": 12.5,
        "gpu": {"used_percent": 64.0},
    }


@pytest.mark.anyio()
async def test_a_machine_that_has_never_reported_says_so(
    dbsession: AsyncSession,
) -> None:
    """Null, not an invented zero -- a node with no snapshot has no arc."""
    from llm_port_backend.db.dao.node_control_dao import NodeControlDAO
    from llm_port_backend.services.nodes import NodeControlService

    node = await _node(dbsession, "10.88.10.71")
    service = NodeControlService(
        NodeControlDAO(dbsession),
        pepper="pepper",
        enrollment_ttl_minutes=60,
        default_command_timeout_sec=90,
    )
    rows = await service.list_nodes()

    mine = next(r for r in rows if r["id"] == str(node.id))
    assert mine["latest_utilization"] is None


@pytest.mark.anyio()
async def test_the_newest_snapshot_wins(dbsession: AsyncSession) -> None:
    """Inventory ticks every ~15s; the list must show the latest, not the first.

    The timestamps are set explicitly rather than left to the column default.
    ``created_at`` defaults to ``now()``, which in Postgres is the
    *transaction's* start time, and this session runs inside one -- so every
    row the test writes would otherwise carry the same value and the ordering
    under test would not be exercised at all. In production each inventory
    frame is its own transaction, so the default does distinguish them.
    """
    from datetime import timedelta

    from llm_port_backend.db.dao.node_control_dao import NodeControlDAO
    from llm_port_backend.db.models.node_control import InfraNodeInventorySnapshot
    from llm_port_backend.services.nodes import NodeControlService

    node = await _node(dbsession, "10.88.10.49")
    base = datetime.now(timezone.utc)
    # Written oldest-last on purpose: insertion order must not decide this.
    for offset, percent in ((0, 91.0), (-15, 55.0), (-30, 10.0)):
        dbsession.add(
            InfraNodeInventorySnapshot(
                node_id=node.id,
                inventory_json={},
                utilization_json={"gpu": {"used_percent": percent}},
                created_at=base + timedelta(seconds=offset),
            )
        )
    await dbsession.flush()

    service = NodeControlService(
        NodeControlDAO(dbsession),
        pepper="pepper",
        enrollment_ttl_minutes=60,
        default_command_timeout_sec=90,
    )
    rows = await service.list_nodes()
    mine = next(r for r in rows if r["id"] == str(node.id))
    assert mine["latest_utilization"]["gpu"]["used_percent"] == 91.0


@pytest.mark.anyio()
async def test_each_machine_gets_its_own_snapshot(dbsession: AsyncSession) -> None:
    """One row per node, not the newest row overall.

    ``DISTINCT ON`` is easy to write so that a single busy machine's snapshot
    is handed to every node in the fleet.
    """
    from llm_port_backend.db.dao.node_control_dao import NodeControlDAO
    from llm_port_backend.services.nodes import NodeControlService

    head = await _node(dbsession, "10.88.10.49")
    worker = await _node(dbsession, "10.88.10.71")
    dao = NodeControlDAO(dbsession)
    await dao.upsert_inventory_snapshot(
        node_id=head.id, inventory_json={}, utilization_json={"cpu_percent": 5.0}
    )
    await dao.upsert_inventory_snapshot(
        node_id=worker.id, inventory_json={}, utilization_json={"cpu_percent": 80.0}
    )
    await dbsession.flush()

    service = NodeControlService(
        dao,
        pepper="pepper",
        enrollment_ttl_minutes=60,
        default_command_timeout_sec=90,
    )
    by_id = {r["id"]: r for r in await service.list_nodes()}
    assert by_id[str(head.id)]["latest_utilization"]["cpu_percent"] == 5.0
    assert by_id[str(worker.id)]["latest_utilization"]["cpu_percent"] == 80.0
