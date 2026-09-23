"""A cluster that is gone, stopped, or lost its machines is not scraped.

Found on the dev workstation: ``targets.json`` still listed two deleted
clusters and one whose only machine had been offline since the night before,
and Prometheus went on dialling every one of their random metrics ports. The
startup rebuild carried every cluster-keyed entry across, nothing removed a
cluster's entries when it stopped or was deleted, and a cluster whose machine
vanished was never looked at again, so it read "ready" indefinitely.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.dao.node_control_dao import NodeControlDAO
from llm_port_backend.db.models.inference import (
    EnvironmentStatus,
    InferenceControlPlane,
    InferenceEnvironment,
    InferenceEnvironmentNode,
)
from llm_port_backend.db.models.node_control import InfraNode, NodeHealthStatus
from llm_port_backend.services.inference import reconciliation
from llm_port_backend.services.inference.drivers.ray.environment import RayEnvironmentManager
from llm_port_backend.services.llm import monitoring
from llm_port_backend.services.llm.monitoring import MonitoringProvisioner, dashboard_uid_for
from llm_port_backend.services.nodes.service import NodeControlService
from tests.test_inference_env_observation import _bound_environment, _FakeNodeControl


def _prov(tmp_path: Path) -> MonitoringProvisioner:
    (tmp_path / "targets.json").write_text("[]", encoding="utf-8")
    (tmp_path / "dash").mkdir(exist_ok=True)
    return MonitoringProvisioner(
        targets_file=str(tmp_path / "targets.json"), dashboard_dir=str(tmp_path / "dash")
    )


def _targets(tmp_path: Path) -> list[dict[str, Any]]:
    return json.loads((tmp_path / "targets.json").read_text(encoding="utf-8"))


def _env_ids(tmp_path: Path) -> set[str]:
    return {t["labels"].get("environment_id") for t in _targets(tmp_path)}


async def _cluster(session: AsyncSession, name: str = "") -> InferenceEnvironment:
    cp = InferenceControlPlane(name=f"cp-{uuid.uuid4().hex[:6]}", driver="ray")
    session.add(cp)
    await session.flush()
    env = InferenceEnvironment(
        control_plane_id=cp.id, name=name or f"env-{uuid.uuid4().hex[:6]}", desired_state="running",
    )
    session.add(env)
    await session.flush()
    return env


# ── the startup rebuild ──────────────────────────────────────────────────


@pytest.mark.anyio()
async def test_the_rebuild_drops_clusters_that_no_longer_exist(
    dbsession: AsyncSession, tmp_path: Path
) -> None:
    prov = _prov(tmp_path)
    alive = await _cluster(dbsession, "gpu-pair")
    deleted = uuid.uuid4()
    await prov.sync_ray_targets(environment_id=alive.id, environment_name="gpu-pair",
                                targets=[{"address": "10.88.10.71", "port": 36061}])
    await prov.sync_ray_targets(environment_id=deleted, environment_name="e2e-muaua9sj",
                                targets=[{"address": "10.88.10.49", "port": 45129}])

    await prov.rebuild_all(dbsession)

    assert _env_ids(tmp_path) == {str(alive.id)}


# ── removing one cluster's targets ───────────────────────────────────────


@pytest.mark.anyio()
async def test_removing_a_clusters_targets_leaves_the_others(tmp_path: Path) -> None:
    prov = _prov(tmp_path)
    a, b = uuid.uuid4(), uuid.uuid4()
    await prov.sync_ray_targets(environment_id=a, environment_name="a",
                                targets=[{"address": "10.0.0.1", "port": 1}, {"address": "10.0.0.2", "port": 2}])
    await prov.sync_ray_targets(environment_id=b, environment_name="b",
                                targets=[{"address": "10.0.0.3", "port": 3}])

    assert await prov.remove_ray_targets(a) == 2
    assert _env_ids(tmp_path) == {str(b)}
    assert await prov.remove_ray_targets(a) == 0, "idempotent"


@pytest.mark.anyio()
async def test_a_stopped_cluster_keeps_its_dashboard_a_deleted_one_does_not(tmp_path: Path) -> None:
    prov = _prov(tmp_path)
    env_id = uuid.uuid4()
    await prov.provision_environment(environment_id=env_id, environment_name="gpu-pair")
    dashboard = tmp_path / "dash" / f"{dashboard_uid_for(env_id)}.json"
    assert dashboard.exists()

    await prov.remove_ray_targets(env_id)
    assert dashboard.exists(), "history stays browsable for a stopped cluster"

    await prov.remove_ray_targets(env_id, drop_dashboard=True)
    assert not dashboard.exists()


# ── the reconcile pass ───────────────────────────────────────────────────


class _Recorder:
    def __init__(self) -> None:
        self.removed: list[str] = []
        self.synced: list[str] = []

    async def remove_ray_targets(self, environment_id: Any, **_: Any) -> int:
        self.removed.append(str(environment_id))
        return 1

    async def sync_ray_targets(self, **kwargs: Any) -> int:
        self.synced.append(str(kwargs["environment_id"]))
        return 1


@pytest.mark.anyio()
@pytest.mark.parametrize(
    ("desired", "status"),
    [("running", "failed"), ("running", "stopped"), ("stopped", "ready")],
)
async def test_a_cluster_that_is_not_running_is_not_scraped(
    monkeypatch: pytest.MonkeyPatch, desired: str, status: str
) -> None:
    recorder = _Recorder()
    monkeypatch.setattr(monitoring, "get_monitoring_provisioner", lambda: recorder)
    env = SimpleNamespace(
        id=uuid.uuid4(), name="gpu-pair", desired_state=desired, status=status,
        observed_status_json={"cluster": {"metrics": {"targets": [{"address": "x", "port": 1}]}}},
    )

    await reconciliation._sync_prometheus_targets(SimpleNamespace(session=None), env)

    assert recorder.removed == [str(env.id)]
    assert recorder.synced == []


# ── a machine that is gone ───────────────────────────────────────────────


async def _set_status(session: AsyncSession, env: InferenceEnvironment, agent: str, status: str) -> None:
    from sqlalchemy import select

    node = (await session.execute(select(InfraNode).where(InfraNode.agent_id == agent))).scalar_one()
    node.status = status
    await session.flush()


@pytest.mark.anyio()
async def test_a_cluster_says_which_machine_is_offline_and_sends_it_nothing(
    dbsession: AsyncSession,
) -> None:
    env = await _bound_environment(dbsession)
    await _set_status(dbsession, env, "spark-a", NodeHealthStatus.OFFLINE)  # the head
    control = _FakeNodeControl()

    await RayEnvironmentManager().reconcile_environment(dbsession, env, node_control=control)

    assert env.status == EnvironmentStatus.FAILED
    assert "spark-a is offline" in (env.status_message or "")
    assert control.issued == [], "no command for a machine with no agent to take it"


@pytest.mark.anyio()
async def test_a_missing_worker_degrades_rather_than_fails(dbsession: AsyncSession) -> None:
    env = await _bound_environment(dbsession)
    await _set_status(dbsession, env, "spark-b", NodeHealthStatus.OFFLINE)  # the worker

    await RayEnvironmentManager().reconcile_environment(dbsession, env, node_control=_FakeNodeControl())

    assert env.status == EnvironmentStatus.DEGRADED
    assert "spark-b is offline" in (env.status_message or "")


@pytest.mark.anyio()
async def test_a_machine_that_just_dropped_is_given_time_to_reconnect(
    dbsession: AsyncSession,
) -> None:
    """A backend restart drops every agent at once.

    Declaring the cluster failed straight away showed a healthy cluster as
    failed for a minute after every restart -- observed live: both DGX agents
    back 51 s after the reload, the cluster still "failed" 25 s later.
    """
    from datetime import UTC, datetime

    from sqlalchemy import select

    env = await _bound_environment(dbsession)
    env.status = EnvironmentStatus.READY
    env.observed_generation = env.generation - 1  # queued by the offline hook
    for agent in ("spark-a", "spark-b"):
        node = (await dbsession.execute(select(InfraNode).where(InfraNode.agent_id == agent))).scalar_one()
        node.status = NodeHealthStatus.OFFLINE
        node.last_seen = datetime.now(tz=UTC)
    await dbsession.flush()
    control = _FakeNodeControl()

    await RayEnvironmentManager().reconcile_environment(dbsession, env, node_control=control)

    assert env.status == EnvironmentStatus.READY, "left as it was"
    assert env.observed_generation < env.generation, "and looked at again next pass"
    assert control.issued == []


def _nodes_service(session: AsyncSession) -> NodeControlService:
    return NodeControlService(
        NodeControlDAO(session), pepper="p", enrollment_ttl_minutes=60, default_command_timeout_sec=60,
    )


async def _member_cluster(session: AsyncSession) -> tuple[InferenceEnvironment, InfraNode]:
    env = await _cluster(session)
    node = InfraNode(agent_id=f"agent-{uuid.uuid4().hex[:6]}", host="10.88.10.220",
                     status="healthy", capabilities_json={})
    session.add(node)
    await session.flush()
    session.add(InferenceEnvironmentNode(environment_id=env.id, node_id=node.id, role="head"))
    env.generation = 3
    env.observed_generation = 3  # settled: the reconciler would not look at it
    await session.flush()
    return env, node


@pytest.mark.anyio()
async def test_losing_a_machine_puts_its_clusters_back_in_the_queue(dbsession: AsyncSession) -> None:
    env, node = await _member_cluster(dbsession)

    await _nodes_service(dbsession)._node_lost_its_last_stream(node)

    assert node.status == NodeHealthStatus.OFFLINE
    assert env.observed_generation < env.generation, "the cluster will be looked at again"


@pytest.mark.anyio()
async def test_a_machine_coming_back_puts_its_clusters_back_in_the_queue(dbsession: AsyncSession) -> None:
    env, node = await _member_cluster(dbsession)
    node.status = NodeHealthStatus.OFFLINE
    await dbsession.flush()

    await _nodes_service(dbsession).heartbeat_node(node=node, status="healthy")

    assert env.observed_generation < env.generation


@pytest.mark.anyio()
async def test_an_ordinary_heartbeat_leaves_the_queue_alone(dbsession: AsyncSession) -> None:
    env, node = await _member_cluster(dbsession)

    await _nodes_service(dbsession).heartbeat_node(node=node, status="healthy")

    assert env.observed_generation == env.generation
