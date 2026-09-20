"""Planner against the real DGX Spark inventory (Phase 4A exit criterion).

``tests/fixtures/dgx_spark_network_inventory.json`` is not hand-written: it is
the verbatim output of ``summarize_network_inventory()`` executed by agent
0.1.8 on ``spark-ts3202`` (10.88.10.49) and ``spark-3201`` (10.88.10.71) on
2026-09-20, with the Tier-2 ``all_interfaces`` block dropped exactly as
``record_inventory`` drops it.

Every previous planner test seeded ``InfraNode.capabilities_json`` with a
network block the production path never produced, which is precisely how the
planner shipped reading a field nothing wrote.  This one drives the real
payload through the real ingest path.
"""

from __future__ import annotations

import json
import pathlib
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

_FIXTURE = (
    pathlib.Path(__file__).parent / "fixtures" / "dgx_spark_network_inventory.json"
)


def _hardware_inventory() -> dict:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def _service(session: AsyncSession) -> NodeControlService:
    return NodeControlService(
        NodeControlDAO(session),
        pepper="pep",
        enrollment_ttl_minutes=10,
        default_command_timeout_sec=300,
    )


async def _dgx_environment(dbsession: AsyncSession) -> tuple[InferenceEnvironment, dict]:
    """Enroll both real nodes through ``record_inventory`` and assign them."""
    hardware = _hardware_inventory()
    cp = InferenceControlPlane(name=f"cp-{uuid.uuid4().hex[:6]}", driver="ray")
    dbsession.add(cp)
    await dbsession.flush()
    env = InferenceEnvironment(
        control_plane_id=cp.id, name=f"env-{uuid.uuid4().hex[:6]}", desired_state="running",
    )
    dbsession.add(env)
    await dbsession.flush()

    service = _service(dbsession)
    nodes: dict[str, InfraNode] = {}
    for hostname, record in hardware.items():
        node = InfraNode(agent_id=hostname, host=record["lan_ip"], status="healthy")
        dbsession.add(node)
        await dbsession.flush()
        await service.record_inventory(
            node=node,
            inventory={"network": record["network"], "gpu_count": 1},
            utilization={},
        )
        nodes[hostname] = node
    await dbsession.flush()

    dbsession.add_all([
        InferenceEnvironmentNode(
            environment_id=env.id,
            node_id=n.id,
            role="head" if h == "spark-ts3202" else "worker",
        )
        for h, n in nodes.items()
    ])
    await dbsession.flush()
    return env, nodes


def test_fixture_is_real_dgx_output() -> None:
    """Guard the fixture's provenance: these are hardware facts, not invention."""
    hardware = _hardware_inventory()
    assert set(hardware) == {"spark-ts3202", "spark-3201"}

    head = hardware["spark-ts3202"]["network"]
    # The head really does carry five docker bridges plus docker0, and 21
    # ephemeral veth devices the agent keeps out of the Tier-2 snapshot.
    assert head["omitted_ephemeral_interfaces"] == 21
    assert head["default_route_interface"] == "enP7s7"
    virtual = [f["interface"] for f in head["fabrics"] if f["link_type"] == "virtual"]
    assert "docker0" in virtual
    assert sum(1 for v in virtual if v.startswith("br-")) == 5
    # Facts only: no agent-side verdict anywhere in the payload.
    assert all("is_management" not in f for f in head["fabrics"])


@pytest.mark.anyio()
async def test_zero_config_planning_picks_roce_on_the_real_pair(
    dbsession: AsyncSession,
) -> None:
    """Phase 4A exit criterion, on the actual inventory of the DGX pair.

    No operator types an interface name; the planner is handed what the agents
    reported and must land on a 200 Gb/s RoCE link.
    """
    env, nodes = await _dgx_environment(dbsession)

    plan = await MultiNodeFabricPlanner(dbsession).plan_environment(env.id, validate=False)

    assert not plan.blockers, plan.blockers
    recommended = next(
        c for c in plan.candidates if c.candidate_id == plan.recommended_candidate_id
    )
    assert recommended.fabric_type == "roce"
    assert recommended.speed_gbps == 200.0
    assert recommended.cidr == "10.100.0.0/24"
    assert recommended.confidence == "high"
    assert recommended.is_management is False
    assert {b.ip for b in recommended.node_bindings.values()} == {"10.100.0.1", "10.100.0.2"}
    assert {b.interface for b in recommended.node_bindings.values()} == {"enp1s0f1np1"}
    assert {b.rdma_device for b in recommended.node_bindings.values()} == {"rocep1s0f1"}


@pytest.mark.anyio()
async def test_real_docker_bridges_never_become_candidates(
    dbsession: AsyncSession,
) -> None:
    """The collision hazard, measured on the hosts that actually have it.

    172.17.0.0/16 and 172.18.0.0/16 exist on *both* nodes with the identical
    .1 address, so a planner that only asks "is this subnet on every node?"
    would happily bind both Ray nodes to 172.17.0.1.
    """
    env, _ = await _dgx_environment(dbsession)
    plan = await MultiNodeFabricPlanner(dbsession).plan_environment(env.id, validate=False)

    candidate_cidrs = {c.cidr for c in plan.candidates}
    assert not any(c.startswith("172.1") or c.startswith("172.2") for c in candidate_cidrs), (
        f"a container bridge became a candidate: {candidate_cidrs}"
    )
    # ... and the operator is told why, rather than the subnet just vanishing.
    rejected = {r.cidr: r.reason for r in plan.rejected}
    assert "172.17.0.0/16" in rejected
    assert "172.18.0.0/16" in rejected

    # Only the two RoCE fabrics and the management LAN survive to candidacy.
    assert candidate_cidrs == {"10.100.0.0/24", "10.100.1.0/24", "10.88.10.0/24"}


@pytest.mark.anyio()
async def test_management_lan_is_scored_down_from_the_routing_fact(
    dbsession: AsyncSession,
) -> None:
    """The backend draws the management conclusion the agent refuses to draw."""
    env, _ = await _dgx_environment(dbsession)
    plan = await MultiNodeFabricPlanner(dbsession).plan_environment(env.id, validate=False)

    mgmt = next(c for c in plan.candidates if c.cidr == "10.88.10.0/24")
    assert mgmt.is_management is True
    assert mgmt.isolation_level == "shared_management"
    assert mgmt.score < 0
    assert mgmt.candidate_id != plan.recommended_candidate_id


@pytest.mark.anyio()
async def test_the_two_equal_roce_fabrics_resolve_deterministically(
    dbsession: AsyncSession,
) -> None:
    """Both DGX RoCE links are 200 Gb/s MTU 1500 - a genuine tie in the wild."""
    env, _ = await _dgx_environment(dbsession)
    plan = await MultiNodeFabricPlanner(dbsession).plan_environment(env.id, validate=False)

    roce = [c for c in plan.candidates if c.fabric_type == "roce"]
    assert len(roce) == 2
    assert roce[0].score == roce[1].score, "the tie is real, not an artifact of the fixture"
    assert plan.recommended_candidate_id == roce[0].candidate_id
    assert roce[0].cidr == "10.100.0.0/24"


@pytest.mark.anyio()
async def test_the_real_plan_applies_and_binds_the_roce_link(
    dbsession: AsyncSession,
) -> None:
    """End to end on real facts: plan -> apply -> bound fabric."""
    env, _ = await _dgx_environment(dbsession)
    planner = MultiNodeFabricPlanner(dbsession)
    plan = await planner.plan_environment(env.id, validate=False)

    applied = await planner.apply_plan(env.id, plan)

    resolved = applied.observed_status_json["resolved_fabric"]
    assert resolved["fabric_type"] == "roce"
    assert resolved["cidr"] == "10.100.0.0/24"
    bindings = resolved["node_bindings"]
    assert {b["ip"] for b in bindings.values()} == {"10.100.0.1", "10.100.0.2"}
    assert applied.head_node_id is not None
