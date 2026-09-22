"""The inventory -> capabilities -> planner boundary (Phase 4A, F-01).

These tests deliberately go through ``NodeControlService.record_inventory``
instead of seeding ``InfraNode.capabilities_json`` by hand.  Seeding the column
is what hid the defect in the first place: the agent's network summary travels
in the *inventory* message, which lands in ``InfraNodeInventorySnapshot``,
while the planner reads ``capabilities_json`` - a column only the heartbeat
writes.  Every planner test passed and every real node produced zero
candidates.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.dao.node_control_dao import NodeControlDAO
from llm_port_backend.db.models.inference import (
    InferenceControlPlane,
    InferenceEnvironment,
    InferenceEnvironmentNode,
)
from llm_port_backend.db.models.node_control import InfraNode
from llm_port_backend.services.inference.planner import MultiNodeFabricPlanner
from llm_port_backend.services.nodes.service import NodeControlService


def _service(session: AsyncSession) -> NodeControlService:
    return NodeControlService(
        NodeControlDAO(session),
        pepper="pep",
        enrollment_ttl_minutes=10,
        default_command_timeout_sec=300,
    )


def _agent_inventory(*, roce_ip: str, mgmt_ip: str) -> dict:
    """An inventory message shaped exactly like the agent's.

    Mirrors ``summarize_network_inventory``: Tier-1 facts in ``fabrics``, the
    raw per-interface detail in ``all_interfaces``.
    """
    return {
        "cpu_count_logical": 20,
        "memory_total_bytes": 130_000_000_000,
        "gpu_count": 1,
        "network_interfaces": ["lo", "docker0", "enp1s0f1np1", "enP7s7"],
        "network": {
            "default_route_interface": "enP7s7",
            "default_route_ip": mgmt_ip,
            "management_ip": mgmt_ip,
            "fabrics": [
                {
                    "interface": "docker0",
                    "ip": "172.17.0.1",
                    "netmask": "255.255.0.0",
                    "cidr": "172.17.0.0/16",
                    "speed_gbps": 10.0,
                    "speed_mbps": 10000,
                    "link_type": "virtual",
                    "rdma_device": None,
                    "pci_address": None,
                    "mtu": 1500,
                    "operstate": "up",
                    "has_default_route": False,
                    "is_virtual": True,
                    "is_up": True,
                },
                {
                    "interface": "enp1s0f1np1",
                    "ip": roce_ip,
                    "netmask": "255.255.255.0",
                    "cidr": "10.100.0.0/24",
                    "speed_gbps": 200.0,
                    "speed_mbps": 200000,
                    "link_type": "roce",
                    "rdma_device": "rocep1s0f1",
                    "pci_address": "0000:01:00.1",
                    "mtu": 9000,
                    "operstate": "up",
                    "has_default_route": False,
                    "is_virtual": False,
                    "is_up": True,
                },
                {
                    "interface": "enP7s7",
                    "ip": mgmt_ip,
                    "netmask": "255.255.255.0",
                    "cidr": "10.88.10.0/24",
                    "speed_gbps": 1.0,
                    "speed_mbps": 1000,
                    "link_type": "ethernet",
                    "rdma_device": None,
                    "pci_address": "0007:07:00.0",
                    "mtu": 1500,
                    "operstate": "up",
                    "has_default_route": True,
                    "is_virtual": False,
                    "is_up": True,
                },
            ],
            "all_interfaces": [
                {"name": "lo", "link_type": "loopback"},
                {"name": "enp1s0f1np1", "link_type": "roce"},
            ],
            "omitted_ephemeral_interfaces": 19,
        },
    }


@pytest.mark.anyio()
async def test_record_inventory_projects_network_into_capabilities(
    dbsession: AsyncSession,
) -> None:
    """The Tier-1 summary must land on the column the planner reads."""
    node = InfraNode(agent_id="spark-ts3202", host="10.88.10.49", status="healthy")
    dbsession.add(node)
    await dbsession.flush()
    assert (node.capabilities_json or {}).get("network") is None

    await _service(dbsession).record_inventory(
        node=node,
        inventory=_agent_inventory(roce_ip="10.100.0.1", mgmt_ip="10.88.10.49"),
        utilization={"cpu_percent": 3.0},
    )
    await dbsession.flush()

    network = node.capabilities_json["network"]
    assert [f["interface"] for f in network["fabrics"]] == [
        "docker0", "enp1s0f1np1", "enP7s7",
    ]
    assert network["default_route_interface"] == "enP7s7"
    # Tier-2 detail stays in the snapshot table; it must not bloat the node row.
    assert "all_interfaces" not in network

    snapshot = await NodeControlDAO(dbsession).get_latest_inventory_snapshot(node_id=node.id)
    assert snapshot is not None
    assert "all_interfaces" in snapshot.inventory_json["network"]


@pytest.mark.anyio()
async def test_record_inventory_leaves_other_capabilities_alone(
    dbsession: AsyncSession,
) -> None:
    """The projection merges; it must not clobber the heartbeat's capabilities."""
    node = InfraNode(
        agent_id=f"spark-{uuid.uuid4().hex[:6]}",
        host="10.88.10.49",
        status="healthy",
        capabilities_json={"gpu_count": 1, "gpu_vendor": "nvidia", "machine": "aarch64"},
    )
    dbsession.add(node)
    await dbsession.flush()

    await _service(dbsession).record_inventory(
        node=node,
        inventory=_agent_inventory(roce_ip="10.100.0.1", mgmt_ip="10.88.10.49"),
        utilization={},
    )
    await dbsession.flush()

    assert node.capabilities_json["gpu_vendor"] == "nvidia"
    assert node.capabilities_json["machine"] == "aarch64"
    assert node.capabilities_json["network"]["fabrics"]


@pytest.mark.anyio()
async def test_unchanged_inventory_does_not_rewrite_the_node_row(
    dbsession: AsyncSession,
) -> None:
    """Inventory ticks every ~15s; identical facts must not churn the row.

    Plan staleness is content-based for the same reason, but a pointless write
    per tick would still be a pointless write.
    """
    node = InfraNode(agent_id=f"spark-{uuid.uuid4().hex[:6]}", host="10.88.10.49")
    dbsession.add(node)
    await dbsession.flush()

    service = _service(dbsession)
    inventory = _agent_inventory(roce_ip="10.100.0.1", mgmt_ip="10.88.10.49")
    await service.record_inventory(node=node, inventory=inventory, utilization={})
    await dbsession.flush()
    first = node.capabilities_json

    await service.record_inventory(node=node, inventory=inventory, utilization={})
    assert node.capabilities_json is first  # identity: no reassignment happened


@pytest.mark.anyio()
async def test_planner_sees_candidates_after_real_inventory_ingest(
    dbsession: AsyncSession,
) -> None:
    """End to end: agent inventory in, RoCE recommendation out.

    This is the Phase 4A exit criterion in miniature - zero-config planning
    picking the 200 Gb/s RoCE link without an operator typing an interface
    name - driven by the production ingest path rather than a seeded column.
    """
    cp = InferenceControlPlane(name=f"cp-{uuid.uuid4().hex[:6]}", driver="ray")
    dbsession.add(cp)
    await dbsession.flush()
    env = InferenceEnvironment(
        control_plane_id=cp.id, name=f"env-{uuid.uuid4().hex[:6]}", desired_state="running",
    )
    dbsession.add(env)

    head = InfraNode(agent_id="spark-3201", host="10.88.10.71", status="healthy")
    worker = InfraNode(agent_id="spark-ts3202", host="10.88.10.49", status="healthy")
    dbsession.add_all([head, worker])
    await dbsession.flush()
    dbsession.add_all([
        InferenceEnvironmentNode(environment_id=env.id, node_id=head.id, role="head"),
        InferenceEnvironmentNode(environment_id=env.id, node_id=worker.id, role="worker"),
    ])
    await dbsession.flush()

    service = _service(dbsession)
    await service.record_inventory(
        node=head,
        inventory=_agent_inventory(roce_ip="10.100.0.1", mgmt_ip="10.88.10.71"),
        utilization={},
    )
    await service.record_inventory(
        node=worker,
        inventory=_agent_inventory(roce_ip="10.100.0.2", mgmt_ip="10.88.10.49"),
        utilization={},
    )
    await dbsession.flush()

    plan = await MultiNodeFabricPlanner(dbsession).plan_environment(env.id, validate=False)

    assert plan.candidates, "a real node's inventory must produce candidates"
    recommended = next(
        c for c in plan.candidates if c.candidate_id == plan.recommended_candidate_id
    )
    assert recommended.fabric_type == "roce"
    assert recommended.cidr == "10.100.0.0/24"
    assert recommended.speed_gbps == 200.0
    assert {b.ip for b in recommended.node_bindings.values()} == {"10.100.0.1", "10.100.0.2"}
    assert not plan.blockers

    # And the plan applies without the operator naming an interface.
    applied = await MultiNodeFabricPlanner(dbsession).apply_plan(env.id, plan)
    resolved = applied.observed_status_json["resolved_fabric"]
    assert resolved["fabric_type"] == "roce"
    assert resolved["cidr"] == "10.100.0.0/24"


@pytest.mark.anyio
async def test_heartbeat_keeps_the_projected_network_summary(
    dbsession: AsyncSession,
) -> None:
    """A heartbeat must not erase what the inventory projected.

    The heartbeat carries *static* capabilities and replaces
    ``capabilities_json`` wholesale.  The fabric planner reads
    ``capabilities_json['network']``, which only the inventory projection
    writes -- so before this, planning worked only in the gap between an
    inventory message and the next heartbeat, and a plan that had just
    succeeded would report "no network facts reported" seconds later.
    """
    service = NodeControlService(
        dao=NodeControlDAO(dbsession),
        pepper="test-pepper",
        enrollment_ttl_minutes=60,
        default_command_timeout_sec=30,
    )
    node = InfraNode(
        agent_id=f"node-{uuid.uuid4().hex[:8]}",
        host="10.0.0.1",
        status="healthy",
        capabilities_json={"hostname": "n1", "machine": "aarch64"},
    )
    dbsession.add(node)
    await dbsession.flush()

    await service.record_inventory(
        node=node,
        inventory={
            "network": {
                "fabrics": [{"cidr": "10.100.0.0/24", "ip": "10.100.0.1"}],
                "all_interfaces": [{"name": "eth0"}],
            }
        },
        utilization={},
    )
    assert node.capabilities_json["network"]["fabrics"]

    # A heartbeat reporting only static capabilities.
    await service.heartbeat_node(
        node=node,
        status="healthy",
        capabilities={"hostname": "n1", "machine": "aarch64", "gpu_count": 1},
    )

    assert node.capabilities_json["gpu_count"] == 1
    assert node.capabilities_json["network"]["fabrics"], (
        "the heartbeat erased the inventory-projected network summary"
    )
    # Tier-2 detail still stays out of the node row.
    assert "all_interfaces" not in node.capabilities_json["network"]
