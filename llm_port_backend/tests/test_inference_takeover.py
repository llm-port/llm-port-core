"""Taking over a Ray cluster the machines still run, after the server lost its own record of it.

The cluster in these tests is the DGX pair's as the agent described it, read
from Ray (2.58) on the running cluster: two machines on a RoCE network, and
qwen-chat -- whose rebuilt configuration the running cluster confirmed equal.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.models.inference import (
    DeploymentPhase,
    EnvironmentStatus,
    InferenceControlPlane,
    InferenceDeployment,
    InferenceEnvironment,
    InferenceEnvironmentNode,
)
from llm_port_backend.db.models.node_control import InfraNode, NodeCommandType
from llm_port_backend.services.inference import takeover
from llm_port_backend.services.inference.drivers.ray.compiler import compile_deployment
from llm_port_backend.services.inference.drivers.ray.secrets import retrieve_cluster_token, seal_token
from tests.platform_fixtures import DGX_SPARK_PLATFORM

APP = "llmport-e783c0c2-cde1-4385-9d2e-08ea4bd2a52e"
SOURCE = "/root/.cache/huggingface/models--Qwen--Qwen2.5-0.5B-Instruct/snapshots/7ae557604adf67be50417f59c2c2f167def9a775"
#: qwen-chat's LLMConfig, as the agent read it from the DGX pair's cluster.
RUNNING = {
    "model_loading_config": {"model_id": "Qwen2.5-0.5B-Instruct", "model_source": SOURCE, "tokenizer_source": None},
    "llm_engine": "vLLM",
    "engine_kwargs": {"revision": "main", "kv_cache_metrics": True, "tool_call_parser": "hermes",
                      "gpu_memory_utilization": 0.8, "enable_auto_tool_choice": True},
    "deployment_config": {"num_replicas": 1, "logging_config": {"encoding": "JSON", "enable_access_log": False}},
    "placement_group_config": None,
    "accelerator_type": None,
    "runtime_env": None,
}


def _fabric(ip: str, cidr: str, interface: str, link: str, gbps: float, *, default: bool = False) -> dict[str, Any]:
    return {"ip": ip, "mtu": 1500, "cidr": cidr, "is_up": True, "netmask": "255.255.255.0", "interface": interface,
            "link_type": link, "operstate": "up", "is_virtual": False, "speed_gbps": gbps,
            "speed_mbps": int(gbps * 1000), "pci_address": None, "rdma_device": "rocep1s0f1" if link == "roce" else None,
            "has_default_route": default}


def _machine(name: str, host: str, roce: str, roce2: str) -> InfraNode:
    """A DGX Spark as it reports itself: management, and two RoCE networks."""
    network = {"fabrics": [
        _fabric(roce2, "10.100.1.0/24", "enP2p1s0f1np1", "roce", 200.0),
        _fabric(host, "10.88.10.0/24", "enP7s7", "ethernet", 1.0, default=True),
        _fabric(roce, "10.100.0.0/24", "enp1s0f1np1", "roce", 200.0),
    ]}
    return InfraNode(agent_id=f"{name}-{uuid.uuid4().hex[:6]}", host=host, status="healthy",
                     capabilities_json={**DGX_SPARK_PLATFORM, "network": network})


def _doc(*, apps: list[dict[str, Any]] | None = None, extra_nodes: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """The cluster as DESCRIBE_RAY_CLUSTER answers (the DGX pair's)."""
    return {
        "attached": True, "ray_version": "2.58.0", "gcs_address": "10.100.0.2:6379", "errors": [],
        "container": {"running": True, "image": "llmport/ray-vllm-gb10:ray2.58-nv26.08"},
        "nodes": [
            {"node_id": "ray-head", "ip": "10.100.0.2", "hostname": "spark-3201", "alive": True, "is_head": True, "gpus": 1.0},
            {"node_id": "ray-worker", "ip": "10.100.0.1", "hostname": "spark-ts3202", "alive": True, "is_head": False, "gpus": 1.0},
            # Ray keeps records of nodes long gone.
            {"node_id": "ray-old", "ip": "10.100.0.1", "hostname": "spark-ts3202", "alive": False, "is_head": False, "gpus": 0.0},
            *(extra_nodes or []),
        ],
        "apps": apps if apps is not None else [{
            "name": APP, "route_prefix": f"/{APP}", "status": "RUNNING",
            "deployments": [{"name": "LLMServer:Qwen2_5-0_5B-Instruct", "status": "HEALTHY", "running_replicas": 1},
                            {"name": "OpenAiIngress", "status": "HEALTHY", "running_replicas": 1}],
            "llm_configs": [RUNNING],
        }],
    }


class _Machine:
    """What the head machine answers to DESCRIBE_RAY_CLUSTER."""

    def __init__(self, doc: dict[str, Any], *, equal: bool = True, token: str | None = "cluster-tok3n") -> None:
        self.doc, self.equal, self.token = doc, equal, token
        self.asked: list[dict[str, Any]] = []

    async def describe_cluster(self, *, node_id: Any, verify: dict | None = None,
                               hand_over_token: bool = False, budget_sec: float | None = None) -> dict[str, Any]:
        self.asked.append({"verify": verify, "hand_over_token": hand_over_token})
        doc = {**self.doc, "apps": [dict(a) for a in self.doc["apps"]]}
        if verify:
            for app in doc["apps"]:
                if app["name"] in verify:
                    app["verify"] = ({"equal": True, "diff": []} if self.equal else {"equal": False, "diff": [
                        {"path": "llm_configs.0.engine_kwargs.tool_call_parser", "running": "hermes", "candidate": "x"}]})
        if hand_over_token and self.token:
            doc["cluster_token_sealed"] = seal_token(self.token)  # as the backend recorded it
        return doc


@pytest.fixture()
async def pair(dbsession: AsyncSession) -> tuple[InfraNode, InfraNode]:
    head = _machine("spark-3201", "10.88.10.71", "10.100.0.2", "10.100.1.2")
    worker = _machine("spark-ts3202", "10.88.10.49", "10.100.0.1", "10.100.1.1")
    dbsession.add_all([head, worker])
    await dbsession.commit()
    return head, worker


def _answering(monkeypatch: pytest.MonkeyPatch, machine: _Machine) -> _Machine:
    monkeypatch.setattr(takeover, "_client", lambda gateway: machine)
    return machine


# ── Rebuilding a deployment from what runs ────────────────────────


def test_the_dgx_config_rebuilds_into_the_spec_it_came_from() -> None:
    recovered = takeover.recover_deployment({"name": APP, "llm_configs": [RUNNING]}, alias="qwen2.5-0.5b-instruct")
    assert recovered is not None
    assert recovered.deployment_id == "e783c0c2-cde1-4385-9d2e-08ea4bd2a52e"
    assert recovered.hf_repo_id == "Qwen/Qwen2.5-0.5B-Instruct"
    assert recovered.spec["artifacts"] == {"source": "local_path", "root_path": SOURCE}
    assert recovered.spec["engine"]["config"]["tool_call_parser"] == "hermes"
    assert recovered.notes == []
    args = compile_deployment(spec_data=recovered.spec, model_display_name="Qwen2.5-0.5B-Instruct",
                              model_source="huggingface", hf_repo_id=recovered.hf_repo_id)
    compiled = args["llm_configs"][0]
    assert compiled["model_loading_config"] == {"model_id": "Qwen2.5-0.5B-Instruct", "model_source": SOURCE}
    assert compiled["engine_kwargs"] == RUNNING["engine_kwargs"]
    assert compiled["deployment_config"] == RUNNING["deployment_config"]


_BASE = {"api_version": "inference.llmport.ai/v1alpha1", "engine": {"name": "vllm", "config": {}},
         "artifacts": {"source": "local_path", "root_path": "/models/org/m"}, "service": {"path": "/v1"}}


@pytest.mark.parametrize("spec", [
    {"scale": {"replicas": 1}, "resources": {"replica": {"gpus": 1}},
     "engine": {"name": "vllm", "config": {"tool_call_parser": "hermes", "enable_auto_tool_choice": True}}},
    {"scale": {"replicas": 2}, "topology": {"tensor_parallel_size": 2}, "resources": {"replica": {"gpus": 2}}},
    {"scale": {"autoscale": {"min_replicas": 1, "max_replicas": 3, "scale_up_timeout": 30, "scale_down_timeout": 300}},
     "resources": {"replica": {"gpus": 1}}},
    {"scale": {"replicas": 1}, "resources": {"replica": {"gpus": 0.5}}},
    {"scale": {"replicas": 1}, "resources": {"replica": {"gpus": 1, "cpu": 4}}},
    {"scale": {"replicas": 1}, "topology": {"tensor_parallel_size": 2, "nodes": 2}, "resources": {"replica": {"gpus": 2}}},
    {"scale": {"replicas": 1}, "topology": {"tensor_parallel_size": 2, "pipeline_parallel_size": 2},
     "resources": {"replica": {"gpus": 4}}},
    {"scale": {"autoscale": {"min_replicas": 1, "max_replicas": 2}}, "resources": {"replica": {"gpus": 1}},
     "extensions": {"ray": {"target_ongoing_requests": 8}}},
], ids=["tools", "tp2", "autoscale", "half-gpu", "cpu", "tp2-2-nodes", "tp2-pp2", "autoscale-target"])
def test_what_llm_port_deploys_compiles_back_to_itself(spec: dict[str, Any]) -> None:
    """The inverse holds for what LLM.Port itself emits: compile, rebuild, compile again."""
    first = compile_deployment(spec_data={**_BASE, **spec}, model_display_name="m", model_source="huggingface")
    rebuilt, _notes = takeover.spec_from_llm_config(first["llm_configs"][0])
    again = compile_deployment(spec_data=rebuilt, model_display_name="m", model_source="huggingface")
    assert again == first


def test_an_app_llm_port_did_not_deploy_is_not_taken() -> None:
    assert takeover.recover_deployment({"name": "someone-elses-app", "llm_configs": [RUNNING]}) is None
    assert takeover.recover_deployment({"name": APP, "llm_configs": []}) is None


# ── Seeing the cluster ────────────────────────────────────────────


@pytest.mark.anyio
async def test_members_are_matched_by_the_network_ray_knows_them_on(
    dbsession: AsyncSession, pair: tuple[InfraNode, InfraNode],
) -> None:
    head, worker = pair
    seen = takeover.view(_doc(), machines=[head, worker], members_elsewhere=set(), existing_deployments=set())
    assert seen["can_take_over"] is True, seen["blockers"]
    assert [(m["name"], m["role"], m["ip"]) for m in seen["members"]] == [
        (head.agent_id, "head", "10.100.0.2"), (worker.agent_id, "worker", "10.100.0.1")]
    assert seen["apps"][0]["model_id"] == "Qwen2.5-0.5B-Instruct" and seen["apps"][0]["copies"] == 1


@pytest.mark.anyio
async def test_what_stops_a_takeover_is_said(dbsession: AsyncSession, pair: tuple[InfraNode, InfraNode]) -> None:
    head, worker = pair
    stranger = {"node_id": "ray-x", "ip": "10.100.0.9", "hostname": "spark-9", "alive": True, "is_head": False}
    seen = takeover.view(_doc(extra_nodes=[stranger]), machines=[head, worker],
                         members_elsewhere={worker.id}, existing_deployments={"e783c0c2-cde1-4385-9d2e-08ea4bd2a52e"})
    assert seen["can_take_over"] is False
    text = " ".join(seen["blockers"])
    assert "spark-9 is in the cluster but not in this fleet" in text
    assert f"{worker.agent_id} already belong" in text
    assert "already has the deployment of Qwen2.5-0.5B-Instruct" in text


@pytest.mark.anyio
async def test_found_clusters_are_listed_once_and_silent_machines_apart(
    dbsession: AsyncSession, pair: tuple[InfraNode, InfraNode], monkeypatch: pytest.MonkeyPatch,
) -> None:
    from llm_port_backend.db.dao.node_control_dao import NodeControlDAO

    head, worker = pair
    silent = InfraNode(agent_id="old-agent", host="10.88.10.50", status="healthy")
    idle = InfraNode(agent_id="no-runtime", host="10.88.10.51", status="healthy")
    dbsession.add_all([silent, idle])
    await dbsession.flush()
    dao = NodeControlDAO(dbsession)
    for node, running in ((head, True), (worker, True), (silent, True), (idle, False)):
        await dao.upsert_inventory_snapshot(node_id=node.id, inventory_json={"ray_runtime": {"running": running}},
                                            utilization_json={})
    await dbsession.commit()

    asked: list[Any] = []

    class _Fleet:
        async def describe_cluster(self, *, node_id: Any, **_kw: Any) -> dict[str, Any] | None:
            asked.append(node_id)
            return None if node_id == silent.id else _doc()

    monkeypatch.setattr(takeover, "_client", lambda gateway: _Fleet())
    found = await takeover.find(dbsession, None)

    assert set(asked) == {head.id, worker.id, silent.id}, "only machines that run the runtime are asked"
    assert len(found["clusters"]) == 1, "both members describe the same cluster"
    assert found["clusters"][0]["can_take_over"] is True
    assert [u["name"] for u in found["unreadable"]] == ["old-agent"]
    assert "0.1.12" in found["unreadable"][0]["error"]


# ── Taking it over ────────────────────────────────────────────────


@pytest.mark.anyio
async def test_the_cluster_and_its_model_are_recorded_as_they_run(
    dbsession: AsyncSession, pair: tuple[InfraNode, InfraNode], monkeypatch: pytest.MonkeyPatch,
) -> None:
    head, worker = pair
    machine = _answering(monkeypatch, _Machine(_doc()))

    result = await takeover.take_over(dbsession, None, node_id=head.id, name="dgx-pair",
                                      aliases={APP: "qwen2.5-0.5b-instruct"})

    env = (await dbsession.execute(select(InferenceEnvironment).where(InferenceEnvironment.name == "dgx-pair"))).scalar_one()
    assert env.status == EnvironmentStatus.READY.value and env.head_node_id == head.id
    assert env.observed_status_json["converged_generation"] == env.generation, "only health-checked from now on"
    bindings = env.observed_status_json["resolved_fabric"]["node_bindings"]
    assert {bindings[str(head.id)]["ip"], bindings[str(worker.id)]["ip"]} == {"10.100.0.2", "10.100.0.1"}, (
        "the RoCE network Ray runs on, not the other one or the management network")
    members = (await dbsession.execute(select(InferenceEnvironmentNode).where(
        InferenceEnvironmentNode.environment_id == env.id))).scalars().all()
    assert {(m.node_id, m.role) for m in members} == {(head.id, "head"), (worker.id, "worker")}
    assert all(m.compute_pool_id for m in members)

    dep = await dbsession.get(InferenceDeployment, uuid.UUID("e783c0c2-cde1-4385-9d2e-08ea4bd2a52e"))
    assert dep is not None and dep.environment_id == env.id, "the app is named after it: the id is kept"
    assert dep.phase == DeploymentPhase.RUNNING.value
    assert dep.spec_json["service"]["alias"] == "qwen2.5-0.5b-instruct"
    assert dep.observed_status_json["applied_head"] == "ray-head"
    plane = await dbsession.get(InferenceControlPlane, env.control_plane_id)
    assert await retrieve_cluster_token(dbsession, plane.credential_ref) == "cluster-tok3n"
    assert machine.asked[-1]["hand_over_token"] is True and APP in machine.asked[-1]["verify"]
    assert result["deployments"][0]["alias"] == "qwen2.5-0.5b-instruct"


@pytest.mark.anyio
async def test_a_model_that_would_restart_stops_the_takeover_and_nothing_is_recorded(
    dbsession: AsyncSession, pair: tuple[InfraNode, InfraNode], monkeypatch: pytest.MonkeyPatch,
) -> None:
    head, _worker = pair
    _answering(monkeypatch, _Machine(_doc(), equal=False))
    with pytest.raises(takeover.TakeoverError, match="would restart Qwen2.5-0.5B-Instruct.*tool_call_parser"):
        await takeover.take_over(dbsession, None, node_id=head.id, name="dgx-pair")
    assert (await dbsession.execute(select(InferenceEnvironment).where(InferenceEnvironment.name == "dgx-pair"))).scalar_one_or_none() is None
    assert await dbsession.get(InferenceDeployment, uuid.UUID("e783c0c2-cde1-4385-9d2e-08ea4bd2a52e")) is None


@pytest.mark.anyio
async def test_no_token_no_takeover(
    dbsession: AsyncSession, pair: tuple[InfraNode, InfraNode], monkeypatch: pytest.MonkeyPatch,
) -> None:
    head, _worker = pair
    _answering(monkeypatch, _Machine(_doc(), token=None))
    with pytest.raises(takeover.TakeoverError, match="token"):
        await takeover.take_over(dbsession, None, node_id=head.id, name="dgx-pair")
    assert (await dbsession.execute(select(InferenceEnvironment).where(InferenceEnvironment.name == "dgx-pair"))).scalar_one_or_none() is None


@pytest.mark.anyio
async def test_after_the_takeover_the_reconciler_restarts_nothing(
    dbsession: AsyncSession, pair: tuple[InfraNode, InfraNode], monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The point of it all: the next pass observes, and neither re-applies the app nor copies the model."""
    from tests.test_inference_deployment_preparation import _FakeNodeControlService, _make_serve_status

    from llm_port_backend.db.dao.inference_dao import EndpointDAO
    from llm_port_backend.db.models.inference import EndpointStatus
    from llm_port_backend.services.inference.drivers.ray.client import _parse_serve_status
    from llm_port_backend.services.inference.drivers.ray.deployment import RayDeploymentManager

    head, _worker = pair
    _answering(monkeypatch, _Machine(_doc()))
    await takeover.take_over(dbsession, None, node_id=head.id, name="dgx-pair", aliases={APP: "qwen2.5-0.5b-instruct"})
    dep = await dbsession.get(InferenceDeployment, uuid.UUID("e783c0c2-cde1-4385-9d2e-08ea4bd2a52e"))

    manager = RayDeploymentManager()

    async def serving(*args: Any, **kwargs: Any) -> Any:
        return _parse_serve_status(_make_serve_status(APP, status="RUNNING", ready=1))

    monkeypatch.setattr(manager, "_probe_serve", serving)
    control = _FakeNodeControlService()
    await manager.reconcile_deployment(dbsession, dep, node_control=control)

    issued = [c["command_type"] for c in control.issued]
    assert NodeCommandType.RUN_SERVE_APP.value not in issued, issued
    assert NodeCommandType.SYNC_MODEL.value not in issued, issued
    assert dep.phase == DeploymentPhase.RUNNING.value, dep.phase_message
    endpoints = await EndpointDAO(dbsession).list_for_deployment(dep.id)
    assert [(e.name, e.status) for e in endpoints] == [("openai", EndpointStatus.PUBLISHED.value)]
    assert endpoints[0].address.endswith(f":8000/{APP}"), endpoints[0].address


@pytest.mark.anyio
async def test_the_token_is_sealed_before_the_command_result_is_stored(dbsession: AsyncSession) -> None:
    from llm_port_backend.db.dao.node_control_dao import NodeControlDAO
    from llm_port_backend.services.inference.drivers.ray.secrets import unseal_token
    from llm_port_backend.services.nodes.service import NodeControlService

    node = InfraNode(agent_id=f"a-{uuid.uuid4().hex[:6]}", host="10.0.0.1", status="healthy")
    dbsession.add(node)
    await dbsession.commit()
    dao = NodeControlDAO(dbsession)
    service = NodeControlService(dao, pepper="p", enrollment_ttl_minutes=10, default_command_timeout_sec=60)
    cmd = await dao.create_command(node_id=node.id, command_type=NodeCommandType.DESCRIBE_RAY_CLUSTER.value,
                                   payload_json={"hand_over_token": True}, idempotency_key=uuid.uuid4().hex,
                                   issued_by=None, correlation_id=None, timeout_sec=60)
    await dbsession.commit()

    await service.record_command_result(node_id=node.id, command_id=cmd.id, payload={
        "success": True, "result": {"attached": True, "cluster_token": "plain-secret"}})
    await dbsession.commit()

    stored = await dao.get_command(cmd.id)
    assert "cluster_token" not in stored.result_json
    assert unseal_token(stored.result_json["cluster_token_sealed"]) == "plain-secret"
    events = await dao.list_command_events(command_id=cmd.id)
    assert "plain-secret" not in str([e.payload_json for e in events])
