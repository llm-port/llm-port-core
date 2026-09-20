"""Observed-state ownership and re-binding safety (F-08, F-10).

Both defects live in the same place: ``_observe`` used to *replace*
``observed_status_json`` wholesale, and ``ray start --head`` is issued with
``tolerate_already_running``, so the control plane could report a binding the
cluster was not actually using.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
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
from llm_port_backend.services.inference.drivers.ray.environment import RayEnvironmentManager
from llm_port_backend.services.inference.planner import MultiNodeFabricPlanner


class _FakeNodeControl:
    """Node control double that always succeeds and reports a healthy cluster."""

    def __init__(self, *, head_ip: str = "10.100.0.1") -> None:
        self.issued: list[dict[str, Any]] = []
        self._rows: dict[uuid.UUID, InfraNodeCommand] = {}
        self._head_ip = head_ip

    async def issue_command(self, **kwargs: Any) -> InfraNodeCommand:
        self.issued.append(kwargs)
        cmd_type = kwargs["command_type"]
        result: dict[str, Any] = {}
        if cmd_type == NodeCommandType.ENSURE_RAY_RUNTIME.value:
            result = {"installed": True, "version": "2.58.0"}
        elif cmd_type == NodeCommandType.ENSURE_RUNTIME_IMAGE.value:
            bundle = (kwargs.get("payload") or {}).get("runtime_bundle") or {}
            result = {"present": True, "verified": True, "image_id": bundle.get("digest")}
        elif cmd_type == NodeCommandType.START_RAY_HEAD.value:
            result = {"started": True, "cluster_address": f"{self._head_ip}:6379"}
        elif cmd_type == NodeCommandType.JOIN_RAY_CLUSTER.value:
            result = {"joined": True}
        elif cmd_type == NodeCommandType.GET_RAY_STATUS.value:
            result = {
                "alive": True,
                "version": "2.58.0",
                "num_nodes": 2,
                "head_address": self._head_ip,
                "cluster_address": f"{self._head_ip}:6379",
                "nodes": [
                    {"node_ip": self._head_ip, "state": "ALIVE", "alive": True, "is_head": True},
                    {"node_ip": "10.100.0.2", "state": "ALIVE", "alive": True, "is_head": False},
                ],
            }
        cmd_id = uuid.uuid4()
        row = InfraNodeCommand(
            id=cmd_id,
            node_id=kwargs.get("node_id") or uuid.uuid4(),
            command_type=cmd_type,
            status=NodeCommandStatus.SUCCEEDED.value,
            idempotency_key=kwargs.get("idempotency_key", "key"),
            result_json=result,
        )
        self._rows[cmd_id] = row
        return row

    async def get_command(self, *, command_id: uuid.UUID) -> InfraNodeCommand:
        return self._rows[command_id]

    def by_type(self, command_type: str) -> list[dict[str, Any]]:
        return [c for c in self.issued if c["command_type"] == command_type]


def _caps(roce_ip: str) -> dict:
    return {
        "network": {
            "fabrics": [{
                "interface": "enp1s0f1np1",
                "ip": roce_ip,
                "cidr": "10.100.0.0/24",
                "speed_gbps": 200.0,
                "link_type": "roce",
                "rdma_device": "rocep1s0f1",
                "mtu": 9000,
                "is_up": True,
                "has_default_route": False,
            }]
        }
    }


async def _bound_environment(dbsession: AsyncSession) -> InferenceEnvironment:
    """An environment with an applied RoCE plan, ready to reconcile."""
    cp = InferenceControlPlane(name=f"cp-{uuid.uuid4().hex[:6]}", driver="ray")
    dbsession.add(cp)
    await dbsession.flush()
    env = InferenceEnvironment(
        control_plane_id=cp.id, name=f"env-{uuid.uuid4().hex[:6]}", desired_state="running",
    )
    dbsession.add(env)
    await dbsession.flush()

    head = InfraNode(agent_id="spark-a", host="10.88.10.49", status="healthy",
                     capabilities_json=_caps("10.100.0.1"))
    worker = InfraNode(agent_id="spark-b", host="10.88.10.71", status="healthy",
                       capabilities_json=_caps("10.100.0.2"))
    dbsession.add_all([head, worker])
    await dbsession.flush()
    dbsession.add_all([
        InferenceEnvironmentNode(environment_id=env.id, node_id=head.id, role="head"),
        InferenceEnvironmentNode(environment_id=env.id, node_id=worker.id, role="worker"),
    ])
    await dbsession.flush()

    planner = MultiNodeFabricPlanner(dbsession)
    plan = await planner.plan_environment(env.id, validate=False)
    return await planner.apply_plan(env.id, plan)


@pytest.mark.anyio()
async def test_resolved_fabric_survives_the_reconcile_it_schedules(
    dbsession: AsyncSession,
) -> None:
    """apply-plan bumps the generation, which schedules a pass immediately.

    The documented, authoritative location for the binding is
    ``observed_status_json['resolved_fabric']`` (Amendment 1).  Replacing the
    whole document on every pass erased it one pass later, and it only kept
    working at all through the ``config_json`` fallback - so any consumer
    reading the documented location (UI, API clients) lost it.
    """
    env = await _bound_environment(dbsession)
    assert env.observed_status_json["resolved_fabric"]["cidr"] == "10.100.0.0/24"
    assert env.observed_status_json["network"]["selected_candidate_id"]

    await RayEnvironmentManager().reconcile_environment(
        dbsession, env, node_control=_FakeNodeControl(),
    )

    observed = env.observed_status_json
    # The observer's own keys are refreshed ...
    assert observed["observation"]["status"] == EnvironmentStatus.READY.value
    assert observed["cluster"]["alive"] is True
    assert observed["conditions"]
    # ... and the planner's keys are still there.
    assert observed["resolved_fabric"]["cidr"] == "10.100.0.0/24"
    assert observed["network"]["selected_candidate_id"]


@pytest.mark.anyio()
async def test_rebinding_a_live_head_is_refused(dbsession: AsyncSession) -> None:
    """A live head cannot be moved to a new fabric by a second apply.

    ``ray start --head`` is tolerated when the node is already up, so the
    agent would report the *requested* address as started while the cluster
    kept running on the old one - the control plane silently reporting a
    fabric nothing uses.
    """
    env = await _bound_environment(dbsession)
    manager = RayEnvironmentManager()
    await manager.reconcile_environment(dbsession, env, node_control=_FakeNodeControl())
    assert env.status == EnvironmentStatus.READY.value

    # Re-bind the environment to the management LAN while the head is live.
    resolved = dict(env.observed_status_json["resolved_fabric"])
    bindings = {
        nid: {**b, "ip": "10.88.10.49", "interface": "enP7s7"}
        for nid, b in resolved["node_bindings"].items()
    }
    resolved["node_bindings"] = bindings
    resolved["cidr"] = "10.88.10.0/24"
    observed = dict(env.observed_status_json)
    observed["resolved_fabric"] = resolved
    env.observed_status_json = observed
    env.generation += 1
    await dbsession.flush()

    node_control = _FakeNodeControl()
    await manager.reconcile_environment(dbsession, env, node_control=node_control)

    assert env.status == EnvironmentStatus.FAILED.value
    reason = env.observed_status_json["observation"]["reason"]
    assert "must be stopped to re-bind" in reason
    # Crucially, nothing at all was issued against the new address - the
    # refusal happens before the pass touches a node.
    assert node_control.issued == []


@pytest.mark.anyio()
async def test_rebinding_is_allowed_once_the_cluster_is_down(
    dbsession: AsyncSession,
) -> None:
    """The guard is about a *live* head, not about re-binding in general."""
    env = await _bound_environment(dbsession)
    manager = RayEnvironmentManager()
    await manager.reconcile_environment(dbsession, env, node_control=_FakeNodeControl())

    # Stop: the observation now records a dead cluster.
    env.desired_state = "stopped"
    env.generation += 1
    await manager.reconcile_environment(dbsession, env, node_control=_FakeNodeControl())
    assert env.status == EnvironmentStatus.STOPPED.value

    env.desired_state = "running"
    env.generation += 1
    node_control = _FakeNodeControl(head_ip="10.88.10.49")
    await manager.reconcile_environment(dbsession, env, node_control=node_control)

    assert node_control.by_type(NodeCommandType.START_RAY_HEAD.value)
