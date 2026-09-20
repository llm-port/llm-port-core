"""End-to-End Zero-Config Multi-Node Verification (Gate J).

Tests the complete integrated flow across all Phase 4 subsystems:
1. Node Inventory with 200 Gb/s RoCE sysfs discovery.
2. Control Plane and Environment creation via neutral API.
3. Multi-Node Fabric Planner API (/plan) generating scored candidates.
4. Auto-recommendation selecting 200 Gb/s RoCE over 1 Gb/s management LAN.
5. Plan commit API (/apply-plan) binding resolved fabric with stale protection.
6. Runtime Bundle manifest tuning injection (bundle-dgx-spark-gb10-v1).
7. Ray Driver cluster bootstrap (START_RAY_HEAD and JOIN_RAY_CLUSTER)
   routing all Ray, NCCL, and UCX traffic strictly over the 200 Gb/s RoCE fabric IP.
8. Multi-node placement compiler enforcing SPREAD placement (preventing STRICT_PACK deadlock).
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.models.inference import (
    EnvironmentStatus,
    InferenceControlPlane,
    InferenceEnvironment,
    InferenceEnvironmentNode,
)
from llm_port_backend.db.models.node_control import (
    InfraNode,
    InfraNodeCommand,
    NodeCommandStatus,
    NodeCommandType,
)
from llm_port_backend.db.models.users import User, current_active_user
from llm_port_backend.services.inference.drivers.ray.compiler import (
    DeploymentValidationError,
    compile_deployment,
)
from llm_port_backend.services.inference.drivers.ray.environment import RayEnvironmentManager

API = "/api/inference"


def _make_superuser() -> User:
    user = MagicMock(spec=User)
    user.id = uuid.uuid4()
    user.is_active = True
    user.is_superuser = True
    user.is_verified = True
    return user


@pytest.fixture()
def authed_fapp(fastapi_app: FastAPI) -> FastAPI:
    fastapi_app.dependency_overrides[current_active_user] = lambda: _make_superuser()
    return fastapi_app


class _FakeNodeControl:
    """Mock NodeControlService capturing issued commands and returning scriptable responses."""

    def __init__(self) -> None:
        self.issued: list[dict[str, Any]] = []
        self._rows: dict[uuid.UUID, InfraNodeCommand] = {}

    async def issue_command(self, **kwargs: Any) -> InfraNodeCommand:
        self.issued.append(kwargs)
        cmd_type = kwargs["command_type"]
        cmd_id = uuid.uuid4()

        result_json: dict[str, Any] = {}
        if cmd_type == NodeCommandType.ENSURE_RUNTIME_IMAGE.value:
            # The agent answers with the identity it actually verified, so the
            # fake echoes the digest it was asked for rather than asserting
            # success unconditionally.
            bundle = (kwargs.get("payload") or {}).get("runtime_bundle") or {}
            result_json = {
                "present": True,
                "verified": True,
                "image": bundle.get("image"),
                "image_id": bundle.get("digest"),
                "runtime_handler": "docker",
            }
        elif cmd_type == NodeCommandType.ENSURE_RAY_RUNTIME.value:
            result_json = {"installed": True, "version": "2.58.0", "runtime": "container"}
        elif cmd_type == NodeCommandType.START_RAY_HEAD.value:
            result_json = {"started": True, "cluster_address": "10.100.0.1:6379"}
        elif cmd_type == NodeCommandType.JOIN_RAY_CLUSTER.value:
            result_json = {"joined": True}
        elif cmd_type == NodeCommandType.GET_RAY_STATUS.value:
            result_json = {
                "alive": True,
                "version": "2.58.0",
                "num_nodes": 2,
                "total_gpus": 2.0,
                "available_gpus": 2.0,
                "cluster_address": "10.100.0.1:6379",
                "nodes": [
                    {"node_ip": "10.100.0.1", "state": "ALIVE", "is_head": True},
                    {"node_ip": "10.100.0.2", "state": "ALIVE", "is_head": False},
                ],
            }

        row = InfraNodeCommand(
            id=cmd_id,
            node_id=kwargs.get("node_id") or uuid.uuid4(),
            command_type=cmd_type,
            status=NodeCommandStatus.SUCCEEDED.value,
            idempotency_key=kwargs.get("idempotency_key", "key"),
            result_json=result_json,
        )
        self._rows[cmd_id] = row
        return row

    async def get_command(self, *, command_id: uuid.UUID) -> InfraNodeCommand:
        return self._rows[command_id]

    def by_type(self, command_type: str) -> list[dict[str, Any]]:
        return [c for c in self.issued if c["command_type"] == command_type]


async def _create_dgx_spark_node(
    dbsession: AsyncSession,
    *,
    agent_id: str,
    mgmt_ip: str,
    roce_ip: str,
) -> InfraNode:
    """Create InfraNode representing physical DGX Spark GB10 hardware."""
    node = InfraNode(
        agent_id=agent_id,
        host=mgmt_ip,
        status="healthy",
        scheduler_eligible=True,
        draining=False,
        maintenance_mode=False,
        capabilities_json={
            "accelerator": {
                "vendor": "nvidia",
                "family": "Blackwell",
                "model": "NVIDIA GB10",
                "compute_capability": "12.1",
                "memory_total_gb": 121.7,
            },
            "network": {
                "management_ip": mgmt_ip,
                "fabrics": [
                    {
                        "interface": "enp1s0f1np1",
                        "ip": roce_ip,
                        "cidr": "10.100.0.0/24",
                        "speed_gbps": 200.0,
                        "link_type": "roce",
                        "rdma_device": "rocep1s0f1",
                        "pci_address": "0000:01:00.1",
                        "mtu": 9000,
                        "is_up": True,
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
                    {
                        # Present on both nodes with the *same* address, as on
                        # the real pair.  It must never become a candidate.
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
                ],
            },
        },
    )
    dbsession.add(node)
    await dbsession.flush()
    return node


@pytest.mark.anyio()
async def test_end_to_end_zero_config_multi_node_flow(
    client: AsyncClient, authed_fapp: FastAPI, dbsession: AsyncSession
) -> None:
    """Execute complete Gate J end-to-end zero-config verification."""

    # 1. Hardware Inventory: Enroll 2 NVIDIA DGX Spark nodes
    node_head = await _create_dgx_spark_node(
        dbsession,
        agent_id="spark-head-ts3202",
        mgmt_ip="10.88.10.49",
        roce_ip="10.100.0.1",
    )
    node_worker = await _create_dgx_spark_node(
        dbsession,
        agent_id="spark-worker-3201",
        mgmt_ip="10.88.10.71",
        roce_ip="10.100.0.2",
    )

    # 2. Neutral Control Plane & Environment Creation
    cp_resp = await client.post(
        f"{API}/control-planes",
        json={"name": f"cp-e2e-{uuid.uuid4().hex[:6]}", "driver": "ray"},
    )
    assert cp_resp.status_code == 201
    cp_data = cp_resp.json()

    env_resp = await client.post(
        f"{API}/environments",
        json={
            "control_plane_id": cp_data["id"],
            "name": f"env-e2e-{uuid.uuid4().hex[:6]}",
            "config": {"runtime_bundle_id": "bundle-dgx-spark-gb10-v1"},
        },
    )
    assert env_resp.status_code == 201
    env_data = env_resp.json()
    env_id = env_data["id"]

    # 3. Node Assignment
    r1 = await client.post(
        f"{API}/environments/{env_id}/nodes",
        json={"node_id": str(node_head.id), "role": "head"},
    )
    assert r1.status_code == 201
    r2 = await client.post(
        f"{API}/environments/{env_id}/nodes",
        json={"node_id": str(node_worker.id), "role": "worker"},
    )
    assert r2.status_code == 201

    # 4. Multi-Node Fabric Autodetection & Recommendation
    plan_resp = await client.post(f"{API}/environments/{env_id}/plan")
    assert plan_resp.status_code == 200
    plan = plan_resp.json()

    assert len(plan["candidates"]) == 2, "the docker bridge must not be a candidate"
    assert "172.17.0.0/16" not in {c["cidr"] for c in plan["candidates"]}
    assert "172.17.0.0/16" in {r["cidr"] for r in plan["rejected"]}
    rec_candidate = next(
        c for c in plan["candidates"] if c["candidate_id"] == plan["recommended_candidate_id"]
    )
    # Verification: RoCE selected over management LAN
    assert rec_candidate["fabric_type"] == "roce"
    assert rec_candidate["speed_gbps"] == 200.0
    assert rec_candidate["mtu"] == 9000
    assert rec_candidate["cidr"] == "10.100.0.0/24"
    assert rec_candidate["score"] >= 10500
    assert rec_candidate["confidence"] == "high"
    assert rec_candidate["isolation_level"] == "isolated_direct"
    assert rec_candidate["recommended"] is True

    # 5. Apply Plan with Stale-Plan Protection Verification
    apply_resp = await client.post(
        f"{API}/environments/{env_id}/apply-plan",
        json={"plan": plan},
    )
    assert apply_resp.status_code == 200
    applied_env = apply_resp.json()
    resolved = applied_env["config"]["resolved_fabric"]
    assert resolved["fabric_type"] == "roce"
    assert resolved["speed_gbps"] == 200.0
    assert resolved["node_bindings"][str(node_head.id)]["ip"] == "10.100.0.1"
    assert resolved["node_bindings"][str(node_worker.id)]["ip"] == "10.100.0.2"
    # Authoritative resolution stored in observed_status per Amendment 1
    assert applied_env["observed_status"]["resolved_fabric"]["fabric_type"] == "roce"

    # 6. Ray Environment Reconciliation over 200 Gb/s RoCE Interconnect
    fake_node_control = _FakeNodeControl()
    manager = RayEnvironmentManager()

    db_env = await dbsession.get(InferenceEnvironment, uuid.UUID(env_id))
    assert db_env is not None

    await manager.reconcile_environment(dbsession, db_env, node_control=fake_node_control)

    # 7. Assert Process Command Synthesis & Platform Tuning Injection
    rec_head_id = plan["recommended_head_node_id"]
    if rec_head_id == str(node_head.id):
        expected_head_ip = "10.100.0.1"
        expected_worker_ip = "10.100.0.2"
    else:
        expected_head_ip = "10.100.0.2"
        expected_worker_ip = "10.100.0.1"

    # Phase 4B: every member node must have its pinned image verified before
    # anything tries to run out of it, and the bundle travels with the
    # lifecycle commands so the agent bootstraps inside the container.
    image_cmds = fake_node_control.by_type(NodeCommandType.ENSURE_RUNTIME_IMAGE.value)
    assert len(image_cmds) == 2
    for cmd in image_cmds:
        bundle = cmd["payload"]["runtime_bundle"]
        assert bundle["image"] == "llmport/ray-vllm-gb10:ray2.58-nv26.08"
        assert bundle["digest"].startswith("sha256:")
        assert bundle["requirements"]["network_mode"] == "host"
        assert bundle["requirements"]["ipc_mode"] == "host"

    head_cmds = fake_node_control.by_type(NodeCommandType.START_RAY_HEAD.value)
    assert len(head_cmds) == 1
    head_payload = head_cmds[0]["payload"]
    assert head_payload["node_ip_address"] == expected_head_ip  # Uses RoCE IP!
    assert head_payload["env"]["NCCL_SOCKET_IFNAME"] == "enp1s0f1np1"
    assert head_payload["env"]["VLLM_HOST_IP"] == expected_head_ip
    assert head_payload["env"]["NCCL_IB_HCA"] == "rocep1s0f1"
    assert head_payload["env"]["UCX_NET_DEVICES"] == "rocep1s0f1:1"
    # GB10 platform tuning from bundle manifest
    assert head_payload["env"]["RAY_memory_monitor_refresh_ms"] == "0"
    # Diagnostics stay opt-in even when the bundle carries them.
    assert "NCCL_DEBUG" not in head_payload["env"]
    assert head_payload["runtime_bundle"]["digest"].startswith("sha256:")

    worker_cmds = fake_node_control.by_type(NodeCommandType.JOIN_RAY_CLUSTER.value)
    assert len(worker_cmds) == 1
    worker_payload = worker_cmds[0]["payload"]
    assert worker_payload["head_address"] == f"{expected_head_ip}:6379"  # Targets RoCE IP!
    assert worker_payload["node_ip_address"] == expected_worker_ip
    assert worker_payload["env"]["NCCL_SOCKET_IFNAME"] == "enp1s0f1np1"
    assert worker_payload["env"]["VLLM_HOST_IP"] == expected_worker_ip
    assert worker_payload["env"]["NCCL_IB_HCA"] == "rocep1s0f1"
    assert worker_payload["env"]["RAY_memory_monitor_refresh_ms"] == "0"
    assert worker_payload["runtime_bundle"]["digest"].startswith("sha256:")

    # Cluster health verified
    assert db_env.status == EnvironmentStatus.READY.value
    assert db_env.address == f"{expected_head_ip}:6379"

    # 8. Multi-Node Placement Strategy Verification (Compiler Lock)
    # Default multi-node uses Ray default placement group scheduling
    multi_node_auto_spec = {
        "api_version": "inference.llmport.ai/v1alpha1",
        "engine": {"name": "vllm", "config": {}},
        "scale": {"replicas": 1},
        "resources": {"replica": {}},
        "topology": {"tensor_parallel_size": 2, "nodes": 2},
        "service": {"path": "/v1"},
    }
    compiled_auto = compile_deployment(
        spec_data=multi_node_auto_spec,
        model_display_name="Qwen/Qwen2.5-0.5B-Instruct",
        model_source="remote",
    )
    llm_config_auto = compiled_auto["llm_configs"][0]
    # topology.nodes == 2 compiles to SPREAD (section 15 rule lock): the
    # certified TP=2 run used exactly that, and Ray's default soft PACK may
    # place both bundles on one host.
    assert llm_config_auto["placement_group_config"] == {
        "bundle_per_worker": {"GPU": 1.0},
        "strategy": "SPREAD",
    }

    # Explicit SPREAD placement strategy via extensions.ray
    multi_node_spread_spec = {
        **multi_node_auto_spec,
        "extensions": {"ray": {"placementStrategy": "SPREAD"}},
    }
    compiled_spread = compile_deployment(
        spec_data=multi_node_spread_spec,
        model_display_name="Qwen/Qwen2.5-0.5B-Instruct",
        model_source="remote",
    )
    llm_config_spread = compiled_spread["llm_configs"][0]
    assert llm_config_spread["placement_group_config"] == {
        "bundle_per_worker": {"GPU": 1.0},
        "strategy": "SPREAD",
    }

    # Verify STRICT_PACK rejection on multi-node to prevent deadlock
    invalid_spec = {
        "api_version": "inference.llmport.ai/v1alpha1",
        "engine": {"name": "vllm", "config": {}},
        "scale": {"replicas": 1},
        "resources": {"placement": "STRICT_PACK", "replica": {"gpus": 2}},
        "topology": {"tensor_parallel_size": 2, "nodes": 2},
        "service": {"path": "/v1"},
    }
    with pytest.raises(DeploymentValidationError, match="STRICT_PACK.*deadlock"):
        compile_deployment(
            spec_data=invalid_spec,
            model_display_name="Qwen/Qwen2.5-0.5B-Instruct",
            model_source="remote",
        )
