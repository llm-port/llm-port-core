"""Unit tests for MultiNodeFabricPlanner and fabric recommendation engine."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.models.inference import (
    InferenceControlPlane,
    InferenceEnvironment,
    InferenceEnvironmentNode,
)
from llm_port_backend.db.models.node_control import InfraNode
from llm_port_backend.services.inference.planner import (
    FabricCandidate,
    InferenceEnvironmentPlan,
    MultiNodeFabricPlanner,
    NodeFabricBinding,
    StalePlanError,
    compute_fabric_fingerprint,
    score_fabric_candidate,
)
from llm_port_backend.services.inference.service import ConflictError


def test_fingerprint_deterministic() -> None:
    """Test that candidate fingerprints are deterministic and independent of binding order."""
    b1 = NodeFabricBinding(
        node_id="node-1",
        interface="enp1s0f1np1",
        ip="10.100.0.1",
        link_type="roce",
        rdma_device="rocep1s0f1",
    )
    b2 = NodeFabricBinding(
        node_id="node-2",
        interface="enp1s0f1np1",
        ip="10.100.0.2",
        link_type="roce",
        rdma_device="rocep1s0f1",
    )

    fp1 = compute_fabric_fingerprint("10.100.0.0/24", [b1, b2])
    fp2 = compute_fabric_fingerprint("10.100.0.0/24", [b2, b1])

    assert fp1.startswith("fabric-")
    assert fp1 == fp2


def test_scoring_prefers_roce_over_management_lan() -> None:
    """Test that 200 Gb/s RoCE with jumbo frames easily outscores 1 Gb/s management LAN."""
    b_roce = [
        NodeFabricBinding(
            node_id="node-1",
            interface="enp1s0f1np1",
            ip="10.100.0.1",
            link_type="roce",
            rdma_device="rocep1s0f1",
        ),
        NodeFabricBinding(
            node_id="node-2",
            interface="enp1s0f1np1",
            ip="10.100.0.2",
            link_type="roce",
            rdma_device="rocep1s0f1",
        ),
    ]
    score_roce, reason_roce, conf_roce, iso_roce, reasons_roce = score_fabric_candidate(
        fabric_type="roce",
        speed_gbps=200.0,
        mtu=9000,
        is_management=False,
        bindings=b_roce,
    )

    b_mgmt = [
        NodeFabricBinding(
            node_id="node-1",
            interface="enP7s7",
            ip="10.88.10.49",
            link_type="ethernet",
            is_management=True,
        ),
        NodeFabricBinding(
            node_id="node-2",
            interface="enP7s7",
            ip="10.88.10.71",
            link_type="ethernet",
            is_management=True,
        ),
    ]
    score_mgmt, reason_mgmt, conf_mgmt, iso_mgmt, reasons_mgmt = score_fabric_candidate(
        fabric_type="ethernet",
        speed_gbps=1.0,
        mtu=1500,
        is_management=True,
        bindings=b_mgmt,
    )

    assert score_roce > score_mgmt
    # RoCE: 8000 (roce) + 2000 (speed: 200 * 10) + 500 (mtu 9000) = 10500
    assert score_roce >= 10500
    assert conf_roce == "high"
    assert iso_roce == "isolated_direct"
    assert len(reasons_roce) >= 3

    # Mgmt: 1000 (ethernet) + 10 (speed) - 5000 (mgmt penalty) = -3990
    assert score_mgmt < 0
    assert conf_mgmt == "low"
    assert iso_mgmt == "shared_management"


@pytest.mark.anyio()
async def test_plan_environment_discovers_dgx_roce(dbsession: AsyncSession) -> None:
    """Test that planning an environment with 2 DGX Spark nodes selects RoCE as recommended."""
    # 1. Create Control Plane & Environment
    cp = InferenceControlPlane(name=f"cp-{uuid.uuid4().hex[:6]}", driver="ray")
    dbsession.add(cp)
    await dbsession.flush()

    env = InferenceEnvironment(
        control_plane_id=cp.id,
        name=f"env-{uuid.uuid4().hex[:6]}",
        desired_state="running",
    )
    dbsession.add(env)
    await dbsession.flush()

    # 2. Create Node 1 (spark-ts3202)
    node1 = InfraNode(
        agent_id="spark-ts3202",
        host="10.88.10.49",
        status="healthy",
        capabilities_json={
            "network": {
                "management_ip": "10.88.10.49",
                "fabrics": [
                    {
                        "interface": "enp1s0f1np1",
                        "ip": "10.100.0.1",
                        "cidr": "10.100.0.0/24",
                        "speed_gbps": 200.0,
                        "link_type": "roce",
                        "rdma_device": "rocep1s0f1",
                        "mtu": 9000,
                        "is_management": False,
                    },
                    {
                        "interface": "enP7s7",
                        "ip": "10.88.10.49",
                        "cidr": "10.88.10.0/24",
                        "speed_gbps": 1.0,
                        "link_type": "ethernet",
                        "mtu": 1500,
                        "is_management": True,
                    },
                ],
            }
        },
    )
    # Node 2 (spark-3201)
    node2 = InfraNode(
        agent_id="spark-3201",
        host="10.88.10.71",
        status="healthy",
        capabilities_json={
            "network": {
                "management_ip": "10.88.10.71",
                "fabrics": [
                    {
                        "interface": "enp1s0f1np1",
                        "ip": "10.100.0.2",
                        "cidr": "10.100.0.0/24",
                        "speed_gbps": 200.0,
                        "link_type": "roce",
                        "rdma_device": "rocep1s0f1",
                        "mtu": 9000,
                        "is_management": False,
                    },
                    {
                        "interface": "enP7s7",
                        "ip": "10.88.10.71",
                        "cidr": "10.88.10.0/24",
                        "speed_gbps": 1.0,
                        "link_type": "ethernet",
                        "mtu": 1500,
                        "is_management": True,
                    },
                ],
            }
        },
    )
    dbsession.add_all([node1, node2])
    await dbsession.flush()

    # Associate nodes with environment
    en1 = InferenceEnvironmentNode(environment_id=env.id, node_id=node1.id, role="head")
    en2 = InferenceEnvironmentNode(environment_id=env.id, node_id=node2.id, role="worker")
    dbsession.add_all([en1, en2])
    await dbsession.flush()

    # 3. Plan environment
    planner = MultiNodeFabricPlanner(dbsession)
    plan = await planner.plan_environment(env.id)

    assert len(plan.candidates) == 2
    # Recommended candidate must be RoCE
    rec_cand = next(c for c in plan.candidates if c.candidate_id == plan.recommended_candidate_id)
    assert rec_cand.fabric_type == "roce"
    assert rec_cand.speed_gbps == 200.0
    assert rec_cand.cidr == "10.100.0.0/24"
    assert rec_cand.recommended is True

    # Check node bindings
    assert str(node1.id) in rec_cand.node_bindings
    assert rec_cand.node_bindings[str(node1.id)].ip == "10.100.0.1"
    assert str(node2.id) in rec_cand.node_bindings
    assert rec_cand.node_bindings[str(node2.id)].ip == "10.100.0.2"

    # 4. Apply Plan
    applied_env = await planner.apply_plan(env.id, plan)
    assert applied_env.config_json is not None
    assert applied_env.observed_status_json is not None
    # The observation owns what was resolved.
    resolved_obs = applied_env.observed_status_json.get("resolved_fabric")
    assert resolved_obs is not None
    assert resolved_obs["fabric_type"] == "roce"
    assert resolved_obs["speed_gbps"] == 200.0
    # ...and config_json holds only what the operator asked for. A copy of the
    # resolution used to live here too, which is how a PATCH setting a port
    # deleted an applied fabric binding.
    assert applied_env.config_json.get("resolved_fabric") is None
    assert applied_env.config_json.get("interconnect_policy") is not None


@pytest.mark.anyio()
async def test_apply_plan_stale_detection(dbsession: AsyncSession) -> None:
    """Test that applying a plan fails with StalePlanError if node inventory changes."""
    cp = InferenceControlPlane(name=f"cp-{uuid.uuid4().hex[:6]}", driver="ray")
    dbsession.add(cp)
    await dbsession.flush()

    env = InferenceEnvironment(
        control_plane_id=cp.id,
        name=f"env-{uuid.uuid4().hex[:6]}",
        desired_state="running",
    )
    dbsession.add(env)

    node = InfraNode(
        agent_id="test-node",
        host="10.0.0.1",
        status="healthy",
        capabilities_json={
            "network": {
                "fabrics": [
                    {
                        "interface": "eth0",
                        "ip": "10.0.0.1",
                        "cidr": "10.0.0.0/24",
                        "speed_gbps": 10.0,
                        "link_type": "ethernet",
                    }
                ]
            }
        },
    )
    dbsession.add(node)
    await dbsession.flush()

    en = InferenceEnvironmentNode(environment_id=env.id, node_id=node.id, role="head")
    dbsession.add(en)
    await dbsession.flush()

    planner = MultiNodeFabricPlanner(dbsession)
    plan = await planner.plan_environment(env.id)

    # A heartbeat alone must NOT invalidate a plan: the node row is touched
    # every ~15s, so a timestamp-based revision would make every plan stale
    # before an operator could approve it.
    node.updated_at = datetime.now(tz=UTC) + timedelta(seconds=10)
    await dbsession.flush()
    await planner.apply_plan(env.id, plan)

    # Changed network facts, on the other hand, do.
    node.capabilities_json = {
        "network": {
            "fabrics": [
                {
                    "interface": "eth0",
                    "ip": "10.0.0.9",
                    "cidr": "10.0.0.0/24",
                    "speed_gbps": 10.0,
                    "link_type": "ethernet",
                }
            ]
        }
    }
    await dbsession.flush()

    with pytest.raises(StalePlanError):
        await planner.apply_plan(env.id, plan)


@pytest.mark.anyio()
async def test_plan_rejects_docker_bridges_and_down_links(dbsession: AsyncSession) -> None:
    """Virtual bridges and down links can never carry a cluster fabric.

    ``172.17.0.0/16`` exists on every Docker host with the *same* ``.1``
    address, so it satisfies a naive "present on all nodes" test and, on these
    real DGX nodes, scores high enough to be picked whenever RoCE is explicitly
    overridden or down - binding both Ray nodes to 172.17.0.1.
    """
    cp = InferenceControlPlane(name=f"cp-{uuid.uuid4().hex[:6]}", driver="ray")
    dbsession.add(cp)
    await dbsession.flush()
    env = InferenceEnvironment(
        control_plane_id=cp.id, name=f"env-{uuid.uuid4().hex[:6]}", desired_state="running",
    )
    dbsession.add(env)
    await dbsession.flush()

    def _caps(roce_ip: str, mgmt_ip: str) -> dict:
        return {
            "network": {
                "default_route_interface": "enP7s7",
                "fabrics": [
                    {
                        "interface": "docker0",
                        "ip": "172.17.0.1",
                        "cidr": "172.17.0.0/16",
                        "speed_gbps": 10.0,
                        "link_type": "virtual",
                        "is_virtual": True,
                        "mtu": 1500,
                        "is_up": True,
                        "has_default_route": False,
                    },
                    {
                        "interface": "enp1s0f1np1",
                        "ip": roce_ip,
                        "cidr": "10.100.0.0/24",
                        "speed_gbps": 200.0,
                        "link_type": "roce",
                        "rdma_device": "rocep1s0f1",
                        "mtu": 9000,
                        "is_up": True,
                        "has_default_route": False,
                    },
                    {
                        "interface": "enP2p1s0f1np1",
                        "ip": roce_ip.replace("10.100.0", "10.100.1"),
                        "cidr": "10.100.1.0/24",
                        "speed_gbps": 200.0,
                        "link_type": "roce",
                        "rdma_device": "rocep2s0f1",
                        "mtu": 9000,
                        "is_up": False,
                        "has_default_route": False,
                    },
                    {
                        "interface": "enP7s7",
                        "ip": mgmt_ip,
                        "cidr": "10.88.10.0/24",
                        "speed_gbps": 1.0,
                        "link_type": "ethernet",
                        "mtu": 1500,
                        "is_up": True,
                        "has_default_route": True,
                    },
                ],
            }
        }

    node1 = InfraNode(
        agent_id="spark-ts3202", host="10.88.10.49", status="healthy",
        capabilities_json=_caps("10.100.0.1", "10.88.10.49"),
    )
    node2 = InfraNode(
        agent_id="spark-3201", host="10.88.10.71", status="healthy",
        capabilities_json=_caps("10.100.0.2", "10.88.10.71"),
    )
    dbsession.add_all([node1, node2])
    await dbsession.flush()
    dbsession.add_all([
        InferenceEnvironmentNode(environment_id=env.id, node_id=node1.id, role="head"),
        InferenceEnvironmentNode(environment_id=env.id, node_id=node2.id, role="worker"),
    ])
    await dbsession.flush()

    plan = await MultiNodeFabricPlanner(dbsession).plan_environment(env.id, validate=False)

    cidrs = {c.cidr for c in plan.candidates}
    assert "172.17.0.0/16" not in cidrs, "a Docker bridge must never be a candidate"
    assert "10.100.1.0/24" not in cidrs, "a down link must never be a candidate"
    assert cidrs == {"10.100.0.0/24", "10.88.10.0/24"}

    rejected = {r.cidr: r.reason for r in plan.rejected}
    assert "172.17.0.0/16" in rejected
    assert "10.100.1.0/24" in rejected

    # The management LAN is identified by the backend from the routing fact,
    # not by a conclusion the agent shipped.
    mgmt = next(c for c in plan.candidates if c.cidr == "10.88.10.0/24")
    assert mgmt.is_management is True
    assert mgmt.isolation_level == "shared_management"

    rec = next(c for c in plan.candidates if c.candidate_id == plan.recommended_candidate_id)
    assert rec.cidr == "10.100.0.0/24"


@pytest.mark.anyio()
async def test_plan_rejects_a_fabric_with_colliding_addresses(dbsession: AsyncSession) -> None:
    """Two nodes holding the same address cannot form a cluster on it."""
    cp = InferenceControlPlane(name=f"cp-{uuid.uuid4().hex[:6]}", driver="ray")
    dbsession.add(cp)
    await dbsession.flush()
    env = InferenceEnvironment(
        control_plane_id=cp.id, name=f"env-{uuid.uuid4().hex[:6]}", desired_state="running",
    )
    dbsession.add(env)
    await dbsession.flush()

    caps = {
        "network": {
            "fabrics": [
                {
                    "interface": "br0",
                    "ip": "172.18.0.1",
                    "cidr": "172.18.0.0/16",
                    "speed_gbps": 10.0,
                    # Deliberately typed as a real link: the duplicate-address
                    # rule must stand on its own, not lean on the type filter.
                    "link_type": "ethernet",
                    "mtu": 1500,
                    "is_up": True,
                }
            ]
        }
    }
    node1 = InfraNode(agent_id="n1", host="10.0.0.1", status="healthy", capabilities_json=caps)
    node2 = InfraNode(agent_id="n2", host="10.0.0.2", status="healthy", capabilities_json=caps)
    dbsession.add_all([node1, node2])
    await dbsession.flush()
    dbsession.add_all([
        InferenceEnvironmentNode(environment_id=env.id, node_id=node1.id, role="head"),
        InferenceEnvironmentNode(environment_id=env.id, node_id=node2.id, role="worker"),
    ])
    await dbsession.flush()

    plan = await MultiNodeFabricPlanner(dbsession).plan_environment(env.id, validate=False)
    assert plan.candidates == []
    assert any("more than one node" in r.reason for r in plan.rejected)
    assert plan.blockers


@pytest.mark.anyio()
async def test_equal_scoring_fabrics_break_ties_deterministically(
    dbsession: AsyncSession,
) -> None:
    """The DGX pair has two equal 200 Gb/s RoCE fabrics; the winner must be stable.

    ``candidates.sort`` is stable, so before the explicit tie-break the winner
    was whichever fabric the sysfs scan (and therefore the stored fabric list)
    happened to yield first - a recommendation decided by iteration order, not
    by any stated rule.
    """
    cp = InferenceControlPlane(name=f"cp-{uuid.uuid4().hex[:6]}", driver="ray")
    dbsession.add(cp)
    await dbsession.flush()
    env = InferenceEnvironment(
        control_plane_id=cp.id, name=f"env-{uuid.uuid4().hex[:6]}", desired_state="running",
    )
    dbsession.add(env)
    await dbsession.flush()

    def _caps(last_octet: int, *, reverse: bool) -> dict:
        fabrics = [
            {
                "interface": "enp1s0f1np1",
                "ip": f"10.100.0.{last_octet}",
                "cidr": "10.100.0.0/24",
                "speed_gbps": 200.0,
                "link_type": "roce",
                "rdma_device": "rocep1s0f1",
                "mtu": 9000,
                "is_up": True,
            },
            {
                "interface": "enP2p1s0f1np1",
                "ip": f"10.100.1.{last_octet}",
                "cidr": "10.100.1.0/24",
                "speed_gbps": 200.0,
                "link_type": "roce",
                "rdma_device": "rocep2s0f1",
                "mtu": 9000,
                "is_up": True,
            },
        ]
        if reverse:
            fabrics.reverse()
        return {"network": {"fabrics": fabrics}}

    n1 = InfraNode(agent_id="spark-ts3202", host="10.88.10.49", status="healthy",
                   capabilities_json=_caps(1, reverse=False))
    n2 = InfraNode(agent_id="spark-3201", host="10.88.10.71", status="healthy",
                   capabilities_json=_caps(2, reverse=False))
    dbsession.add_all([n1, n2])
    await dbsession.flush()
    dbsession.add_all([
        InferenceEnvironmentNode(environment_id=env.id, node_id=n1.id, role="head"),
        InferenceEnvironmentNode(environment_id=env.id, node_id=n2.id, role="worker"),
    ])
    await dbsession.flush()

    planner = MultiNodeFabricPlanner(dbsession)
    first = await planner.plan_environment(env.id, validate=False)
    assert len(first.candidates) == 2
    assert first.candidates[0].score == first.candidates[1].score, "the tie is the point"
    first_choice = next(
        c.cidr for c in first.candidates if c.candidate_id == first.recommended_candidate_id
    )

    # Same physical facts, opposite enumeration order.
    n1.capabilities_json = _caps(1, reverse=True)
    n2.capabilities_json = _caps(2, reverse=True)
    await dbsession.flush()

    second = await planner.plan_environment(env.id, validate=False)
    second_choice = next(
        c.cidr for c in second.candidates if c.candidate_id == second.recommended_candidate_id
    )
    assert first_choice == second_choice == "10.100.0.0/24"


@pytest.mark.anyio()
async def test_apply_plan_ignores_client_supplied_bindings(dbsession: AsyncSession) -> None:
    """The request body is an approval receipt, never the source of bindings."""
    cp = InferenceControlPlane(name=f"cp-{uuid.uuid4().hex[:6]}", driver="ray")
    dbsession.add(cp)
    await dbsession.flush()
    env = InferenceEnvironment(
        control_plane_id=cp.id, name=f"env-{uuid.uuid4().hex[:6]}", desired_state="running",
    )
    dbsession.add(env)
    await dbsession.flush()

    def _caps(ip: str) -> dict:
        return {
            "network": {
                "fabrics": [{
                    "interface": "enp1s0f1np1",
                    "ip": ip,
                    "cidr": "10.100.0.0/24",
                    "speed_gbps": 200.0,
                    "link_type": "roce",
                    "rdma_device": "rocep1s0f1",
                    "mtu": 9000,
                    "is_up": True,
                }]
            }
        }

    n1 = InfraNode(agent_id="spark-a", host="10.88.10.49", status="healthy",
                   capabilities_json=_caps("10.100.0.1"))
    n2 = InfraNode(agent_id="spark-b", host="10.88.10.71", status="healthy",
                   capabilities_json=_caps("10.100.0.2"))
    dbsession.add_all([n1, n2])
    await dbsession.flush()
    dbsession.add_all([
        InferenceEnvironmentNode(environment_id=env.id, node_id=n1.id, role="head"),
        InferenceEnvironmentNode(environment_id=env.id, node_id=n2.id, role="worker"),
    ])
    await dbsession.flush()

    planner = MultiNodeFabricPlanner(dbsession)
    plan = await planner.plan_environment(env.id, validate=False)

    # Tamper with the approved document the way a hostile operator would.
    for binding in plan.candidates[0].node_bindings.values():
        binding.ip = "10.88.10.200"
        binding.interface = "attacker0"

    applied = await planner.apply_plan(env.id, plan)
    bound = applied.observed_status_json["resolved_fabric"]["node_bindings"]
    assert {b["ip"] for b in bound.values()} == {"10.100.0.1", "10.100.0.2"}
    assert all(b["interface"] == "enp1s0f1np1" for b in bound.values())


@pytest.mark.anyio()
async def test_apply_plan_rejects_a_plan_for_another_environment(
    dbsession: AsyncSession,
) -> None:
    """A plan generated for environment A must not apply to environment B."""
    cp = InferenceControlPlane(name=f"cp-{uuid.uuid4().hex[:6]}", driver="ray")
    dbsession.add(cp)
    await dbsession.flush()
    envs = []
    for _ in range(2):
        e = InferenceEnvironment(
            control_plane_id=cp.id, name=f"env-{uuid.uuid4().hex[:6]}", desired_state="running",
        )
        dbsession.add(e)
        envs.append(e)
    await dbsession.flush()

    node = InfraNode(
        agent_id=f"n-{uuid.uuid4().hex[:4]}", host="10.0.0.1", status="healthy",
        capabilities_json={
            "network": {"fabrics": [{
                "interface": "eth0", "ip": "10.0.0.1", "cidr": "10.0.0.0/24",
                "speed_gbps": 10.0, "link_type": "ethernet", "is_up": True,
            }]}
        },
    )
    dbsession.add(node)
    await dbsession.flush()
    for e in envs:
        dbsession.add(
            InferenceEnvironmentNode(environment_id=e.id, node_id=node.id, role="head")
        )
    await dbsession.flush()

    planner = MultiNodeFabricPlanner(dbsession)
    plan_a = await planner.plan_environment(envs[0].id, validate=False)

    with pytest.raises(ConflictError):
        await planner.apply_plan(envs[1].id, plan_a)
