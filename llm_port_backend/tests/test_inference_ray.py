"""Phase 2 (managed Ray environment lifecycle) backend tests.

Covers the real Phase 2 seams that ``test_inference_core.py`` only stubbed:

* cluster-status -> environment-status mapping and conditions;
* encrypted cluster-token store/retrieve round-trip against Postgres;
* ``EnvironmentDAO.list_pending_observation`` generation-lag predicate;
* the Ray driver ``GET_RAY_STATUS`` probe via a fake node control service;
* the 8-step environment reconcile loop (head start / worker join / probe /
  capability snapshot / observed-state persistence) and its teardown path;
* the ``GET /api/admin/system/nodes/secrets/{credential_ref}`` delivery
  endpoint (bearer node-credential authn + control-plane membership scoping);
* the reconciler seam dispatching a ``driver="ray"`` control plane to the
  real driver probe instead of the Phase 1 no-op.

All node-side I/O is faked; the only real infrastructure is the Postgres
test database from ``conftest.py``.  ``RayDriver`` is deliberately *not*
imported at module level: importing ``drivers.ray.driver`` registers the
"ray" key in the global driver registry, which would collide with
``test_inference_core``'s same-key re-registration test in a combined run.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.dao.inference_dao import EnvironmentDAO
from llm_port_backend.db.dao.node_control_dao import NodeControlDAO
from llm_port_backend.db.dependencies import get_db_session
from llm_port_backend.db.models.inference import (
    EnvironmentStatus,
    InferenceControlPlane,
    InferenceDeployment,
    InferenceEnvironment,
    InferenceEnvironmentNode,
)
from llm_port_backend.db.models.llm import LLMModel, ModelSource, ModelStatus
from llm_port_backend.db.models.node_control import (
    InfraNode,
    InfraNodeCommand,
    NodeCommandStatus,
    NodeCommandType,
)
from llm_port_backend.services.inference.drivers.ray.schemas import RayClusterStatus
from llm_port_backend.services.inference.drivers.ray.secrets import (
    control_plane_id_from_credential_ref,
    generate_cluster_token,
    retrieve_cluster_token,
    store_cluster_token,
)
from llm_port_backend.services.inference.drivers.ray.status import (
    build_environment_conditions,
    map_cluster_to_environment_status,
)
from llm_port_backend.services.inference.reconciliation import ReconciliationContext
from llm_port_backend.services.nodes.auth import hash_with_pepper
from llm_port_backend.settings import settings
from llm_port_backend.web.api.admin.system.views import router as system_router


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _cluster(
    *,
    alive: bool = True,
    num_nodes: int = 1,
    version: str = "2.58.0",
    total_gpus: float = 2.0,
    cluster_address: str = "10.0.0.1:6379",
) -> RayClusterStatus:
    return RayClusterStatus(
        alive=alive,
        version=version,
        num_nodes=num_nodes,
        nodes=[
            {"node_ip": f"10.0.0.{i + 1}", "state": "ALIVE", "is_head": i == 0}
            for i in range(num_nodes)
        ],
        total_gpus=total_gpus,
        available_gpus=total_gpus,
        cluster_address=cluster_address,
    )


def _command_row(
    command_id: uuid.UUID,
    status: str,
    result_json: dict[str, Any] | None = None,
    command_type: str = NodeCommandType.GET_RAY_STATUS.value,
) -> InfraNodeCommand:
    return InfraNodeCommand(
        id=command_id,
        node_id=uuid.uuid4(),
        command_type=command_type,
        status=status,  # type: ignore[arg-type]
        idempotency_key="test",
        result_json=result_json,
    )


def _services_stub() -> dict[str, Any]:
    return {
        "control_planes": SimpleNamespace(reconcile=AsyncMock()),
        "environments": SimpleNamespace(reconcile=AsyncMock()),
        "deployments": SimpleNamespace(reconcile=AsyncMock()),
    }


def _cp_namespace(cp_id: uuid.UUID) -> Any:
    """A control-plane row double; the driver probe only reads ``id``."""
    return SimpleNamespace(id=cp_id, driver="ray")


def _ray_driver():
    from llm_port_backend.services.inference.drivers.ray.driver import RayDriver

    return RayDriver()


def _healthy_probe_result() -> dict[str, Any]:
    return {
        "alive": True,
        "version": "2.58.0",
        "num_nodes": 2,
        "nodes": [],
        "total_gpus": 2.0,
        "available_gpus": 2.0,
        "cluster_address": "10.0.0.1:6379",
    }


class _FakeNodeControl:
    """Minimal NodeControlService double with a scriptable command lifecycle.

    ``results`` maps command type -> result_json; the single ``result_json``
    argument is the default (used by GET_RAY_STATUS and any type not in
    ``results``).  Per-type scriptable results let a test assert the additive
    GET_RAY_SERVE_STATUS command independently of the cluster probe.
    """

    def __init__(
        self,
        *,
        result_json: dict[str, Any] | None,
        status: str = NodeCommandStatus.SUCCEEDED.value,
        results: dict[str, dict[str, Any] | None] | None = None,
    ) -> None:
        self._result_json = result_json
        self._status = status
        self._results = dict(results or {})
        self.issued: list[dict[str, Any]] = []
        self._rows: dict[uuid.UUID, InfraNodeCommand] = {}

    def _result_for(self, command_type: str) -> dict[str, Any] | None:
        base = self._result_json
        if command_type in self._results:
            return self._results[command_type]
        return base

    async def issue_command(self, **kwargs: Any) -> Any:
        self.issued.append(kwargs)
        row = _command_row(
            uuid.uuid4(),
            self._status,
            self._result_for(kwargs["command_type"])
            if self._status == NodeCommandStatus.SUCCEEDED.value
            else None,
            command_type=kwargs["command_type"],
        )
        if row.id is not None:
            self._rows[row.id] = row
        return row

    async def get_command(self, *, command_id: uuid.UUID) -> InfraNodeCommand:
        if command_id in self._rows:
            return self._rows[command_id]
        # Command we haven't seen (shouldn't happen) — answer as the default.
        return _command_row(command_id, self._status, self._result_json)

    def by_type(self, command_type: str) -> list[dict[str, Any]]:
        return [c for c in self.issued if c["command_type"] == command_type]


# ---------------------------------------------------------------------------
# Status mapping
# ---------------------------------------------------------------------------


def test_map_cluster_not_alive_is_failed() -> None:
    assert map_cluster_to_environment_status(_cluster(alive=False)) is EnvironmentStatus.FAILED


def test_map_cluster_alive_no_nodes_is_preparing() -> None:
    assert map_cluster_to_environment_status(_cluster(num_nodes=0)) is EnvironmentStatus.PREPARING


def test_map_cluster_healthy_is_ready() -> None:
    assert map_cluster_to_environment_status(_cluster(num_nodes=2)) is EnvironmentStatus.READY


def test_map_cluster_dead_worker_is_degraded() -> None:
    """A dead record degrades only when it means an expected member is missing."""
    status = RayClusterStatus(
        alive=True,
        num_nodes=2,
        nodes=[
            {"node_ip": "10.100.0.1", "state": "ALIVE", "is_head": True},
            {"node_ip": "10.100.0.2", "state": "DEAD", "is_head": False},
        ],
    )
    assert status.alive_nodes == 1
    assert map_cluster_to_environment_status(status, 2) is EnvironmentStatus.DEGRADED


def test_stale_dead_record_does_not_degrade_a_complete_cluster() -> None:
    """Ray never drops dead node records, so they cannot gate health.

    After a worker restart the GCS holds the old DEAD record *and* the new
    ALIVE one (verified live on Ray 2.58: a restart-and-rejoin leaves three
    records for two nodes).  Treating any dead record as a degradation pinned
    the environment to DEGRADED until the head's GCS was restarted - and since
    the deployment driver gates on READY, that blocked every deployment after
    the first restart.
    """
    status = RayClusterStatus(
        alive=True,
        # Raw count includes the retained dead record.
        num_nodes=3,
        nodes=[
            {"node_ip": "10.100.0.1", "state": "ALIVE", "alive": True, "is_head": True},
            {"node_ip": "10.100.0.2", "state": "ALIVE", "alive": True, "is_head": False},
            {"node_ip": "10.100.0.2", "state": "DEAD", "alive": False, "is_head": False},
        ],
    )
    assert status.alive_nodes == 2
    assert map_cluster_to_environment_status(status, 2) is EnvironmentStatus.READY

    # ... and the membership condition counts live records, not history.
    by_type = {
        c["type"]: c for c in build_environment_conditions(status, expected_nodes=2)
    }
    assert by_type["WorkersJoined"]["status"] == "True"


def test_workers_joined_is_false_while_a_dead_record_inflates_the_raw_count() -> None:
    """The raw count must not report "all joined" while a member is missing."""
    status = RayClusterStatus(
        alive=True,
        num_nodes=2,
        nodes=[
            {"node_ip": "10.100.0.1", "state": "ALIVE", "alive": True, "is_head": True},
            {"node_ip": "10.100.0.2", "state": "DEAD", "alive": False, "is_head": False},
        ],
    )
    by_type = {
        c["type"]: c for c in build_environment_conditions(status, expected_nodes=2)
    }
    assert by_type["WorkersJoined"]["status"] == "False"
    assert "Only 1 of 2" in by_type["WorkersJoined"]["message"]


def test_conditions_head_and_workers() -> None:
    conditions = build_environment_conditions(_cluster(num_nodes=2), expected_nodes=2)
    by_type = {c["type"]: c for c in conditions}
    assert by_type["HeadActive"]["status"] == "True"
    assert by_type["WorkersJoined"]["status"] == "True"


def test_conditions_partial_workers() -> None:
    # Head + one of three expected nodes: workers condition is "False".
    conditions = build_environment_conditions(_cluster(num_nodes=2), expected_nodes=3)
    by_type = {c["type"]: c for c in conditions}
    assert by_type["HeadActive"]["status"] == "True"
    assert by_type["WorkersJoined"]["status"] == "False"
    # 1 of 2 expected nodes (F31): workers condition is False, not omitted.
    conditions_1_of_2 = build_environment_conditions(_cluster(num_nodes=1), expected_nodes=2)
    by_type_1_of_2 = {c["type"]: c for c in conditions_1_of_2}
    assert by_type_1_of_2["HeadActive"]["status"] == "True"
    assert by_type_1_of_2["WorkersJoined"]["status"] == "False"
    assert by_type_1_of_2["WorkersJoined"]["reason"] == "PartialWorkersJoined"

    # 1 of 3 expected nodes: workers condition is also False.
    conditions_1_of_3 = build_environment_conditions(_cluster(num_nodes=1), expected_nodes=3)
    by_type_1_of_3 = {c["type"]: c for c in conditions_1_of_3}
    assert by_type_1_of_3["WorkersJoined"]["status"] == "False"
    # Legacy results (no serve/metrics tiers reported) carry no tier
    # conditions: health mapping stays stable for older agents.
    by_type_legacy = {c["type"]: c for c in conditions}
    assert "ServeReady" not in by_type_legacy
    assert "MetricsDiscovery" not in by_type_legacy


def _rich_cluster(serve_available: bool = True, metrics_enabled: bool = True) -> RayClusterStatus:
    """An enriched (new-agent) probe result: flat tier + serve + metrics."""
    c = _cluster(num_nodes=2)
    c.total_cpus = 40.0
    c.head_address = "10.0.0.1:6379"
    c.capabilities = {"cluster_sdk": True, "serve": serve_available, "state": False, "metrics": metrics_enabled}
    c.serve = {"available": serve_available, "active": serve_available, "detail": None, "apps": {}}
    c.metrics = {"enabled": metrics_enabled, "targets": []}
    return c


def test_conditions_include_serve_and_metrics_tiers() -> None:
    cond = {c["type"]: c for c in build_environment_conditions(_rich_cluster(), expected_nodes=2)}
    assert cond["ServeReady"]["status"] == "True"
    assert cond["MetricsDiscovery"]["status"] == "True"


def test_conditions_serve_unavailable_is_false() -> None:
    cond = {c["type"]: c for c in build_environment_conditions(_rich_cluster(serve_available=False, metrics_enabled=False), expected_nodes=2)}
    assert cond["ServeReady"]["status"] == "False"
    assert cond["ServeReady"]["reason"] == "ServeNotObserved"
    assert cond["MetricsDiscovery"]["status"] == "False"


def test_conditions_tiers_never_reported_when_dead() -> None:
    dead = _rich_cluster()
    dead.alive = False
    cond = {c["type"] for c in build_environment_conditions(dead, expected_nodes=2)}
    assert "ServeReady" not in cond
    assert "MetricsDiscovery" not in cond
    assert "HeadActive" in cond


# ---------------------------------------------------------------------------
# Enriched GET_RAY_STATUS result parsing (Tier A flat + additive tiers)
# ---------------------------------------------------------------------------


def test_parse_cluster_status_enriched_fields() -> None:
    from llm_port_backend.services.inference.drivers.ray.client import (
        _parse_cluster_status,
        _parse_serve_status,
    )

    enriched = _rich_cluster().model_dump()
    parsed = _parse_cluster_status(enriched)
    assert parsed.total_cpus == 40.0
    assert parsed.head_address == "10.0.0.1:6379"
    assert parsed.capabilities["cluster_sdk"] is True
    assert parsed.serve is not None and parsed.serve_available is True
    assert parsed.metrics is not None and parsed.metrics_enabled is True
    # Legacy mapping inputs are unchanged by the enrichment.
    assert map_cluster_to_environment_status(parsed) is EnvironmentStatus.READY

    serve = _parse_serve_status({"alive": True, "serve": {"available": True, "apps": {"a": {}}} | {"detail": None}})
    assert serve.alive is True and serve.available is True and "a" in serve.apps


def test_parse_cluster_status_legacy_result_still_parses() -> None:
    from llm_port_backend.services.inference.drivers.ray.client import _parse_cluster_status

    legacy = _healthy_probe_result()  # flat fields only, no tiers
    parsed = _parse_cluster_status(legacy)
    assert parsed.total_cpus == 0.0
    assert parsed.head_address is None
    assert parsed.capabilities == {}
    assert parsed.serve is None and parsed.serve_available is False
    assert parsed.metrics is None and parsed.metrics_enabled is False
    # Legacy result must not generate tier conditions.
    types = {c["type"] for c in build_environment_conditions(parsed, expected_nodes=2)}
    assert types <= {"HeadActive", "WorkersJoined"}


# ---------------------------------------------------------------------------
# Secrets: encrypted round-trip against Postgres
# ---------------------------------------------------------------------------


async def test_secret_store_retrieve_round_trip(dbsession: AsyncSession) -> None:
    cp_id = uuid.uuid4()
    token = generate_cluster_token()
    ref = await store_cluster_token(dbsession, cp_id, token)
    assert ref == f"cp-{cp_id}"
    assert "mock-encrypted" not in ref  # real ciphertext, no mock values

    got = await retrieve_cluster_token(dbsession, ref)
    assert got == token

    # Unknown refs (or garbage) resolve to None, never raise.
    assert await retrieve_cluster_token(dbsession, "cp-00000000-0000-0000-0000-000000000000") is None
    assert await retrieve_cluster_token(dbsession, "not-a-ref") is None
    assert await retrieve_cluster_token(dbsession, None) is None


def test_secret_ref_inversion() -> None:
    cp_id = uuid.uuid4()
    assert control_plane_id_from_credential_ref(f"cp-{cp_id}") == cp_id
    assert control_plane_id_from_credential_ref("zz-123") is None
    assert control_plane_id_from_credential_ref(None) is None


# ---------------------------------------------------------------------------
# Pending-observation predicate
# ---------------------------------------------------------------------------


async def _seed_environment(
    session: AsyncSession,
    *,
    generation: int = 1,
    observed_generation: int = 0,
    status: str | None = None,
) -> InferenceEnvironment:
    cp = InferenceControlPlane(name=f"cp-{uuid.uuid4().hex[:12]}", driver="noop")
    session.add(cp)
    await session.flush()
    if status is None:
        status = (
            EnvironmentStatus.READY.value
            if observed_generation >= generation
            else EnvironmentStatus.PENDING.value
        )
    env = InferenceEnvironment(
        control_plane_id=cp.id,
        name=f"env-{uuid.uuid4().hex[:12]}",
        generation=generation,
        observed_generation=observed_generation,
        status=status,
    )
    session.add(env)
    await session.flush()
    return env


async def test_pending_observation_only_lags(dbsession: AsyncSession) -> None:
    pending_env = await _seed_environment(dbsession, generation=1, observed_generation=0)
    observed_env = await _seed_environment(dbsession, generation=2, observed_generation=2)
    failed_env = await _seed_environment(
        dbsession, generation=2, observed_generation=2, status=EnvironmentStatus.FAILED.value
    )
    assert pending_env.id != observed_env.id

    pending = await EnvironmentDAO(dbsession).list_pending_observation()
    ids = {e.id for e in pending}
    assert pending_env.id in ids
    # A non-deleted, fully observed environment must NOT be pending (bug B10).
    assert observed_env.id not in ids
    # A failed environment with desired_state=running must be retried (F05).
    assert failed_env.id in ids


# ---------------------------------------------------------------------------
# Ray driver probe via a fake node control service
# ---------------------------------------------------------------------------


@pytest.fixture()
async def probe_env(
    dbsession: AsyncSession,
) -> tuple[InferenceControlPlane, InferenceEnvironment, list[InfraNode]]:
    """A real Ray control plane + environment with head/worker nodes bound."""
    cp = InferenceControlPlane(name=f"cp-{uuid.uuid4().hex[:12]}", driver="ray")
    dbsession.add(cp)
    await dbsession.flush()

    nodes = [
        InfraNode(agent_id=f"ray-head-{uuid.uuid4().hex[:8]}", host="10.0.0.1"),
        InfraNode(agent_id=f"ray-worker-{uuid.uuid4().hex[:8]}", host="10.0.0.2"),
    ]
    dbsession.add_all(nodes)
    await dbsession.flush()

    env = InferenceEnvironment(
        control_plane_id=cp.id,
        name=f"env-{uuid.uuid4().hex[:12]}",
        head_node_id=nodes[0].id,
    )
    dbsession.add(env)
    await dbsession.flush()  # env.id must exist before membership rows reference it
    dbsession.add(InferenceEnvironmentNode(environment_id=env.id, node_id=nodes[0].id, role="head"))
    dbsession.add(InferenceEnvironmentNode(environment_id=env.id, node_id=nodes[1].id, role="worker"))
    await dbsession.flush()
    return cp, env, nodes


async def test_driver_probe_without_context_is_honest_noop(probe_env) -> None:
    cp, _env, _nodes = probe_env
    report = await _ray_driver().probe(cp)
    assert report["reconciled"] is False
    assert report["probed"] is False
    assert "reason" in report


async def test_driver_probe_dispatches_get_ray_status(probe_env, dbsession: AsyncSession) -> None:
    cp, _env, nodes = probe_env
    fake = _FakeNodeControl(
        result_json=_healthy_probe_result(),
        results={NodeCommandType.GET_RAY_SERVE_STATUS.value: {"alive": True, "serve": {"available": True, "apps": {}}}},
    )
    report = await _ray_driver().probe(cp, session=dbsession, node_control=fake)

    assert report["reconciled"] is True
    assert report["probed"] is True
    assert report["alive"] is True
    assert report["num_nodes"] == 2
    assert report["cluster_address"] == "10.0.0.1:6379"
    # The enriched cluster tier is carried through verbatim.
    assert report["cluster"]["num_nodes"] == 2
    # One cluster probe + one additive Serve probe, both at the head.
    status_cmds = fake.by_type(NodeCommandType.GET_RAY_STATUS.value)
    assert len(status_cmds) == 1
    issued = status_cmds[0]
    assert issued["node_id"] == nodes[0].id
    assert not (issued["payload"] or {}).get("token")
    # The Serve tier is best-effort and reported under the "serve" key.
    serve = fake.by_type(NodeCommandType.GET_RAY_SERVE_STATUS.value)
    assert len(serve) == 1 and serve[0]["node_id"] == nodes[0].id
    assert report["serve"]["alive"] is True
    assert report["serve"]["available"] is True


async def test_driver_probe_serve_failure_never_fails_report(probe_env, dbsession: AsyncSession) -> None:
    """A failed GET_RAY_SERVE_STATUS must not fail the probe report."""
    cp, _env, _nodes = probe_env

    class _ServeFailingControl(_FakeNodeControl):
        """Succeeds the cluster probe, terminally fails the Serve probe."""

        def __init__(self, **kw: Any) -> None:
            super().__init__(**kw)
            self._serve_ids: set[uuid.UUID] = set()

        async def issue_command(self, **kwargs: Any) -> Any:
            row = await super().issue_command(**kwargs)
            if kwargs["command_type"] == NodeCommandType.GET_RAY_SERVE_STATUS.value:
                self._serve_ids.add(row.id)
            return row

        async def get_command(self, *, command_id: uuid.UUID) -> InfraNodeCommand:
            if command_id in self._serve_ids:
                return _command_row(
                    command_id, NodeCommandStatus.FAILED.value, None,
                    command_type=NodeCommandType.GET_RAY_SERVE_STATUS.value,
                )
            return await super().get_command(command_id=command_id)

    fake = _ServeFailingControl(
        result_json=_healthy_probe_result(),
        results={NodeCommandType.GET_RAY_SERVE_STATUS.value: {"alive": True, "serve": {"available": True, "apps": {}}}},
    )
    report = await _ray_driver().probe(cp, session=dbsession, node_control=fake)
    assert report["reconciled"] is True
    assert report["probed"] is True
    assert report["alive"] is True  # cluster health is unaffected
    assert report["serve"]["alive"] is False
    assert report["serve"]["available"] is False


async def test_driver_probe_failed_command_reports_not_alive(probe_env, dbsession: AsyncSession) -> None:
    cp, _env, _nodes = probe_env
    fake = _FakeNodeControl(result_json=None, status=NodeCommandStatus.FAILED.value)
    report = await _ray_driver().probe(cp, session=dbsession, node_control=fake)
    # A terminal failure is a real probe, reporting not alive — never a
    # fabricated healthy status.
    assert report["probed"] is True
    assert report["alive"] is False


# ---------------------------------------------------------------------------
# Environment 8-step reconcile loop (fake node control, real manager)
# ---------------------------------------------------------------------------


async def test_environment_loop_runs_all_steps(probe_env, dbsession: AsyncSession) -> None:
    _cp, env, nodes = probe_env
    fake = _FakeNodeControl(result_json=_healthy_probe_result())
    await _ray_driver().environment_manager.reconcile_environment(dbsession, env, node_control=fake)

    # Step 2: ensure the Ray runtime on every member (head + worker).
    ensures = fake.by_type(NodeCommandType.ENSURE_RAY_RUNTIME.value)
    assert {c["node_id"] for c in ensures} == {nodes[0].id, nodes[1].id}

    # Step 3/4: head start carries a credential_ref (opaque, no raw token).
    heads = fake.by_type(NodeCommandType.START_RAY_HEAD.value)
    assert len(heads) == 1 and heads[0]["node_id"] == nodes[0].id
    assert heads[0]["payload"]["credential_ref"].startswith("cp-")
    assert heads[0]["payload"]["version"] == "2.58.0"

    # Step 5: the worker joins the *real* head address resolved from the
    # InfraNode host — never the 127.0.0.1 mock.
    joins = fake.by_type(NodeCommandType.JOIN_RAY_CLUSTER.value)
    assert len(joins) == 1 and joins[0]["node_id"] == nodes[1].id
    assert joins[0]["payload"]["head_address"] == "10.0.0.1:6379"
    assert joins[0]["payload"]["credential_ref"].startswith("cp-")

    # Step 7: capability snapshot persisted onto the environment.
    assert env.capabilities_json.get("driver") == "ray"
    # Step 8: observed state persisted at the current generation.
    assert env.status is EnvironmentStatus.READY
    assert env.observed_generation == env.generation
    assert env.observed_status_json["observation"]["reconciled"] is True
    assert env.observed_status_json["cluster"]["num_nodes"] == 2


async def test_environment_loop_stopped_state_tears_down(probe_env, dbsession: AsyncSession) -> None:
    _cp, env, nodes = probe_env
    env.desired_state = "stopped"
    fake = _FakeNodeControl(result_json=None)
    await _ray_driver().environment_manager.reconcile_environment(dbsession, env, node_control=fake)

    stops = fake.by_type(NodeCommandType.STOP_RAY.value)
    leaves = fake.by_type(NodeCommandType.LEAVE_RAY_CLUSTER.value)
    assert len(stops) == 1 and stops[0]["node_id"] == nodes[0].id
    assert len(leaves) == 1 and leaves[0]["node_id"] == nodes[1].id
    # F23: Confirmation that workers leave first, head stops last
    issued_types = [c["command_type"] for c in fake.issued]
    assert issued_types == [NodeCommandType.LEAVE_RAY_CLUSTER.value, NodeCommandType.STOP_RAY.value]
    # No lifecycle start/join work for a stopped environment.
    assert fake.by_type(NodeCommandType.START_RAY_HEAD.value) == []
    assert fake.by_type(NodeCommandType.JOIN_RAY_CLUSTER.value) == []
    assert env.status is EnvironmentStatus.STOPPED


async def test_environment_loop_ensure_runtime_failure_marks_failed(probe_env, dbsession: AsyncSession) -> None:
    _cp, env, nodes = probe_env
    # Fake node control where ENSURE_RAY_RUNTIME reports installed: False
    fake = _FakeNodeControl(
        result_json={"alive": True, "num_nodes": 2},
        results={
            NodeCommandType.ENSURE_RAY_RUNTIME.value: {"installed": False, "version": "2.58.0"},
        },
    )
    await _ray_driver().environment_manager.reconcile_environment(dbsession, env, node_control=fake)

    # Failed ENSURE stops the pass immediately with FAILED status and reason
    assert env.status is EnvironmentStatus.FAILED
    assert "not installed" in env.observed_status_json["observation"]["reason"]
    # No START_RAY_HEAD or JOIN_RAY_CLUSTER was attempted
    assert fake.by_type(NodeCommandType.START_RAY_HEAD.value) == []
    assert fake.by_type(NodeCommandType.JOIN_RAY_CLUSTER.value) == []


async def test_environment_loop_without_node_control_is_honest(probe_env, dbsession: AsyncSession) -> None:
    _cp, env, _nodes = probe_env
    await _ray_driver().environment_manager.reconcile_environment(dbsession, env)  # node_control=None
    assert env.status is EnvironmentStatus.FAILED
    assert "node control" in env.observed_status_json["observation"]["reason"]


# ---------------------------------------------------------------------------
# Reconciler seam: driver="ray" goes to the real driver probe, not the
# Phase 1 no-op.
# ---------------------------------------------------------------------------


async def test_reconcile_control_plane_ray_dispatches_real_probe(
    probe_env, dbsession: AsyncSession
) -> None:
    from llm_port_backend.services.inference.reconciliation import reconcile_control_plane

    cp, env, _nodes = probe_env
    context = ReconciliationContext(session=dbsession, **_services_stub())
    fake = _FakeNodeControl(result_json=_healthy_probe_result())
    context._node_control = fake  # noqa: SLF001 - inject the fake service
    report = await reconcile_control_plane(context, cp)

    assert report["reconciled"] is True
    assert report["driver"] == "ray"
    # One cluster probe (+ one additive Serve probe, since the cluster is alive).
    assert len(fake.issued) == 2
    assert fake.issued[0]["command_type"] == NodeCommandType.GET_RAY_STATUS.value
    assert fake.issued[1]["command_type"] == NodeCommandType.GET_RAY_SERVE_STATUS.value
    # A successful live probe must NOT fall back to the domain-service no-op.
    context.control_planes.reconcile.assert_not_awaited()
    # The probe report is stamped as observed at the current generation.
    assert cp.observed_generation == cp.generation


# ---------------------------------------------------------------------------
# Secret delivery endpoint: authn + membership scoping
# ---------------------------------------------------------------------------


@pytest.fixture()
async def secret_app(
    dbsession: AsyncSession,
) -> tuple[AsyncClient, dict[str, Any]]:
    """A minimal app hosting the system router, wired to the test session."""
    app = FastAPI()
    app.include_router(system_router, prefix="/api/admin/system")
    app.dependency_overrides[get_db_session] = lambda: dbsession

    # Enrolled node with a credential the endpoint can verify.
    node = InfraNode(agent_id=f"agent-{uuid.uuid4().hex[:8]}", host="10.0.0.9")
    dbsession.add(node)
    await dbsession.flush()
    cred_id = uuid.uuid4()
    secret = "super-secret-plain"
    dao = NodeControlDAO(dbsession)
    await dao.create_credential(
        node_id=node.id,
        credential_id=cred_id,
        secret_hash=hash_with_pepper(secret, pepper=settings.settings_master_key),
    )
    await dbsession.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, {"session": dbsession, "node": node, "credential": f"{cred_id}.{secret}"}


async def _make_ray_env(
    session: AsyncSession, node: InfraNode, *, include_node: bool = True
) -> str:
    """Create a Ray control plane + environment; returns its credential_ref."""
    cp = InferenceControlPlane(name=f"cp-{uuid.uuid4().hex[:12]}", driver="ray")
    session.add(cp)
    await session.flush()  # cp.id must exist before the environment references it
    env = InferenceEnvironment(control_plane_id=cp.id, name=f"env-{uuid.uuid4().hex[:12]}")
    session.add(env)
    await session.flush()
    if include_node:
        session.add(InferenceEnvironmentNode(environment_id=env.id, node_id=node.id, role="head"))
    ref = await store_cluster_token(session, cp.id, "ray-cluster-token-abc123")
    await session.commit()
    return ref


def _secret_url(ref: str) -> str:
    return f"/api/admin/system/nodes/secrets/{ref}"


async def test_secret_endpoint_serves_token_to_member(secret_app) -> None:
    client, ctx = secret_app
    ref = await _make_ray_env(ctx["session"], ctx["node"], include_node=True)
    resp = await client.get(_secret_url(ref), headers={"Authorization": f"Bearer {ctx['credential']}"})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"token": "ray-cluster-token-abc123"}


async def test_secret_endpoint_rejects_missing_credential(secret_app) -> None:
    client, ctx = secret_app
    ref = await _make_ray_env(ctx["session"], ctx["node"], include_node=True)
    resp = await client.get(_secret_url(ref))
    assert resp.status_code == 401


async def test_secret_endpoint_rejects_bad_credential(secret_app) -> None:
    client, ctx = secret_app
    ref = await _make_ray_env(ctx["session"], ctx["node"], include_node=True)
    bogus = "00000000-0000-0000-0000-000000000001.wrong-secret"
    resp = await client.get(_secret_url(ref), headers={"Authorization": f"Bearer {bogus}"})
    assert resp.status_code == 401


async def test_secret_endpoint_rejects_non_member_node(secret_app) -> None:
    client, ctx = secret_app
    # Valid credentials, but the node is NOT a member of any environment of
    # this control plane -> no token (same 404 as unknown refs to avoid
    # leaking existence).
    ref = await _make_ray_env(ctx["session"], ctx["node"], include_node=False)
    resp = await client.get(_secret_url(ref), headers={"Authorization": f"Bearer {ctx['credential']}"})
    assert resp.status_code == 404


async def test_secret_endpoint_rejects_unknown_ref_format(secret_app) -> None:
    client, ctx = secret_app
    ref = str(uuid.uuid4())  # not a cp-<uuid> ref
    resp = await client.get(_secret_url(ref), headers={"Authorization": f"Bearer {ctx['credential']}"})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Phase 3: Ray deployment orchestrator (run -> readiness -> publish; delete)
# ---------------------------------------------------------------------------


def _deployment_spec(*, replicas: int = 1, path: str = "/v1") -> dict[str, Any]:
    """A minimal valid v1alpha1 spec the compiler accepts end-to-end."""
    return {
        "api_version": "inference.llmport.ai/v1alpha1",
        "engine": {"name": "vllm", "config": {}},
        "scale": {"replicas": replicas},
        "resources": {"replica": {"gpus": 1.0}},
        "service": {"path": path},
    }


def _run_serve_result(app_name: str) -> dict[str, Any]:
    return {"deleted": False, "ran": True, "app_name": app_name, "status": "ok"}


def _serve_status(app_name: str, *, app_status: str = "RUNNING", message: str | None = None) -> dict[str, Any]:
    """A GET_RAY_SERVE_STATUS result shaped like Ray 2.58's.

    ``build_openai_app`` creates an ``LLMServer:<model>`` deployment and an
    ``OpenAiIngress`` deployment; a healthy deployment reports ``HEALTHY``
    and serving replicas are in state ``RUNNING``.
    """
    return {
        "alive": True,
        "serve": {
            "available": True,
            "active": app_status == "RUNNING",
            "detail": None,
            "apps": {
                app_name: {
                    "name": app_name,
                    "status": app_status,
                    "message": message,
                    "deployments": {
                        "LLMServer:tiny-model": {
                            "name": "LLMServer:tiny-model",
                            "status": "HEALTHY",
                            "num_replicas_ready": 1,
                            "num_replicas_pending": 0,
                            "message": "",
                        },
                        "OpenAiIngress": {
                            "name": "OpenAiIngress",
                            "status": "HEALTHY",
                            "num_replicas_ready": 1,
                            "num_replicas_pending": 0,
                            "message": "",
                        },
                    },
                }
            },
        },
    }


def _serve_status_without_apps() -> dict[str, Any]:
    """Serve is up but the app is gone (e.g. the cluster restarted)."""
    return {"alive": True, "serve": {"available": True, "active": False, "detail": None, "apps": {}}}


@pytest.fixture()
async def deployment_env(dbsession: AsyncSession) -> tuple[InferenceDeployment, InfraNode]:
    """A Ray control plane + environment + head node + model + active deployment."""
    cp = InferenceControlPlane(name=f"cp-{uuid.uuid4().hex[:12]}", driver="ray")
    dbsession.add(cp)
    await dbsession.flush()
    node = InfraNode(agent_id=f"ray-head-{uuid.uuid4().hex[:8]}", host="10.0.0.1")
    dbsession.add(node)
    await dbsession.flush()
    env = InferenceEnvironment(
        control_plane_id=cp.id,
        name=f"env-{uuid.uuid4().hex[:12]}",
        head_node_id=node.id,
        # Deployments only act on a ready environment (F40).
        status=EnvironmentStatus.READY.value,
    )
    dbsession.add(env)
    await dbsession.flush()  # env.id must exist before membership rows reference it
    dbsession.add(InferenceEnvironmentNode(environment_id=env.id, node_id=node.id, role="head"))
    model = LLMModel(
        display_name="org/tiny-model",
        source=ModelSource.HUGGINGFACE,
        status=ModelStatus.AVAILABLE,
        hf_repo_id="org/tiny-model",
        hf_revision="main",
    )
    dbsession.add(model)
    await dbsession.flush()
    dep = InferenceDeployment(
        environment_id=env.id,
        model_id=model.id,
        name=f"dep-{uuid.uuid4().hex[:12]}",
        spec_json=_deployment_spec(),
    )
    dbsession.add(dep)
    await dbsession.flush()
    return dep, node


async def test_deployment_active_runs_and_publishes(deployment_env, dbsession: AsyncSession) -> None:
    """Desired active compiles the spec, runs the named app, waits for
    readiness, marks RUNNING, and publishes the OpenAI endpoint."""
    from llm_port_backend.db.dao.inference_dao import EndpointDAO

    dep, node = deployment_env
    app = f"llmport-{dep.id}"
    fake = _FakeNodeControl(
        result_json=_healthy_probe_result(),
        results={
            NodeCommandType.RUN_SERVE_APP.value: _run_serve_result(app),
            NodeCommandType.GET_RAY_SERVE_STATUS.value: _serve_status(app),
        },
    )
    await _ray_driver().deployment_manager.reconcile_deployment(dbsession, dep, node_control=fake)

    assert dep.phase == "running"
    assert dep.ready_replicas == 1
    assert dep.total_replicas == 1
    assert dep.observed_generation == dep.generation
    obs = dep.observed_status_json["observation"]
    assert obs["reconciled"] is True
    assert obs["app"] == app

    # The named-app run went to the head node with the per-(dep,generation)
    # idempotency key and a compiled LLMServingArgs document.
    runs = fake.by_type(NodeCommandType.RUN_SERVE_APP.value)
    assert len(runs) == 1
    assert runs[0]["node_id"] == node.id
    assert runs[0]["payload"]["app_name"] == app
    assert runs[0]["payload"]["llm_serving_args"]
    assert runs[0]["idempotency_key"].startswith(f"inference-dep:run:{dep.id}:{dep.generation}")
    # F13: the Serve proxy is placed on the head and bound to its cluster IP
    # (Ray's default 127.0.0.1 is unreachable off-node).
    assert runs[0]["payload"]["proxy_location"] == "HeadOnly"
    assert runs[0]["payload"]["http_options"] == {"host": "10.0.0.1", "port": 8000}

    # Readiness was observed through the Serve tier (best-effort probe).
    serves = fake.by_type(NodeCommandType.GET_RAY_SERVE_STATUS.value)
    assert len(serves) == 1 and serves[0]["node_id"] == node.id

    # A logical OpenAI endpoint is published for the running app.
    endpoints = await EndpointDAO(dbsession).list_for_deployment(dep.id)
    assert len(endpoints) == 1
    assert endpoints[0].name == "openai"
    assert endpoints[0].status == "published"
    # Reachable URL: scheme + head IP + Serve port + the app's route prefix.
    assert endpoints[0].address == f"http://10.0.0.1:8000/{app}"
    assert endpoints[0].published_json["base_url"] == f"http://10.0.0.1:8000/{app}/v1"


async def test_deployment_delete_converges_and_retires(deployment_env, dbsession: AsyncSession) -> None:
    """Desired deleted removes the named app and retires its endpoints."""
    from llm_port_backend.db.dao.inference_dao import EndpointDAO

    dep, node = deployment_env
    # Seed a published endpoint so the delete has something to retire.
    ep_dao = EndpointDAO(dbsession)
    await ep_dao.create(dep.id, name="openai", address="10.0.0.1/x", path="/v1")
    dep.desired_state = "deleted"
    app = f"llmport-{dep.id}"

    fake = _FakeNodeControl(
        result_json=_healthy_probe_result(),
        results={NodeCommandType.DELETE_SERVE_APP.value: {"deleted": True, "app_name": app}},
    )
    await _ray_driver().deployment_manager.reconcile_deployment(dbsession, dep, node_control=fake)

    assert dep.phase == "deleted"
    deletions = fake.by_type(NodeCommandType.DELETE_SERVE_APP.value)
    assert len(deletions) == 1
    assert deletions[0]["node_id"] == node.id
    assert deletions[0]["payload"]["app_name"] == app
    # No lifecycle run is issued for a delete.
    assert fake.by_type(NodeCommandType.RUN_SERVE_APP.value) == []
    endpoints = await EndpointDAO(dbsession).list_for_deployment(dep.id)
    assert len(endpoints) == 1
    assert endpoints[0].status == "retired"
    assert dep.observed_status_json["observation"]["action"] == "delete"


async def test_deployment_without_node_control_is_honest(deployment_env, dbsession: AsyncSession) -> None:
    """Without a node control service the row stays pending and honest - no
    live action is taken and it is NOT marked observed."""
    dep, _node = deployment_env
    await _ray_driver().deployment_manager.reconcile_deployment(dbsession, dep)  # node_control=None
    assert dep.phase == "pending"
    assert "node control" in dep.observed_status_json["observation"]["reason"]
    assert dep.observed_generation != dep.generation  # stays in the pending queue


async def test_deployment_missing_model_is_failed(deployment_env, dbsession: AsyncSession) -> None:
    """A deployment whose model row is gone is a terminal (non-transient)
    FAILED phase; no live action is taken."""
    dep, _node = deployment_env
    dep.model_id = uuid.uuid4()  # not present in llm_models
    fake = _FakeNodeControl(result_json=_healthy_probe_result())
    await _ray_driver().deployment_manager.reconcile_deployment(dbsession, dep, node_control=fake)
    assert dep.phase == "failed"
    assert fake.issued == []  # no node command was dispatched


async def test_reconcile_deployment_ray_dispatches_real_manager(deployment_env, dbsession: AsyncSession) -> None:
    """The reconciler seam drives a driver="ray" deployment through the real
    RayDeploymentManager instead of the Phase 1 no-op."""
    from llm_port_backend.services.inference.reconciliation import reconcile_deployment

    dep, _node = deployment_env
    context = ReconciliationContext(session=dbsession, **_services_stub())
    app = f"llmport-{dep.id}"
    fake = _FakeNodeControl(
        result_json=_healthy_probe_result(),
        results={
            NodeCommandType.RUN_SERVE_APP.value: _run_serve_result(app),
            NodeCommandType.GET_RAY_SERVE_STATUS.value: _serve_status(app),
        },
    )
    context._node_control = fake  # noqa: SLF001 - inject the fake service
    report = await reconcile_deployment(context, dep)

    assert report["reconciled"] is True
    assert report["reason"] == "dispatched to driver"
    # The real manager ran the app and converged to RUNNING.
    # The domain-service no-op was never invoked for a live dispatch.
    context.deployments.reconcile.assert_not_awaited()


async def test_reconcile_deployment_no_driver_is_domain_noop(
    deployment_env, dbsession: AsyncSession
) -> None:
    """A deployment whose control plane uses an unregistered driver falls
    through to the domain service no-op (the Phase 1 behavior)."""
    from llm_port_backend.services.inference.reconciliation import reconcile_deployment

    dep, _node = deployment_env
    # Reparent the deployment's environment to an unregistered (noop) control
    # plane so the registry lookup returns None.
    cp = InferenceControlPlane(name=f"cp-{uuid.uuid4().hex[:12]}", driver="noop")
    dbsession.add(cp)
    await dbsession.flush()
    env = InferenceEnvironment(control_plane_id=cp.id, name=f"env-{uuid.uuid4().hex[:12]}")
    dbsession.add(env)
    await dbsession.flush()
    dep.environment_id = env.id
    await dbsession.flush()

    dep_reconcile = AsyncMock()
    context = ReconciliationContext(
        session=dbsession,
        control_planes=SimpleNamespace(reconcile=AsyncMock()),
        environments=SimpleNamespace(reconcile=AsyncMock()),
        deployments=SimpleNamespace(reconcile=dep_reconcile),
    )
    report = await reconcile_deployment(context, dep)
    assert report["reconciled"] is False
    assert report["reason"] == "no driver registered"
    dep_reconcile.assert_awaited_once_with(dep.id)


# ---------------------------------------------------------------------------
# Real-path regressions (2026-09-19 implementation review).  Each test pins a
# defect the earlier suite could not see because its fakes bypassed the
# production wiring.
# ---------------------------------------------------------------------------


async def test_probes_never_resume_an_earlier_in_flight_probe(probe_env) -> None:
    """A probe stuck on a dead agent must not capture every later probe."""
    from llm_port_backend.services.inference.drivers.ray.client import RayClusterClient

    _cp, _env, nodes = probe_env
    fake = _FakeNodeControl(result_json=_healthy_probe_result())
    client = RayClusterClient(fake)
    await client.probe_cluster(head_node_id=nodes[0].id)
    await client.probe_cluster(head_node_id=nodes[0].id)
    keys = [c["idempotency_key"] for c in fake.by_type(NodeCommandType.GET_RAY_STATUS.value)]
    assert len(keys) == 2 and keys[0] != keys[1]


def test_ray_command_error_is_raisable() -> None:
    """Raising the error must not itself fail (``self`` was keyword-only)."""
    from llm_port_backend.services.inference.drivers.ray.client import RayCommandError

    with pytest.raises(RayCommandError) as info:
        raise RayCommandError(
            command_type="run_serve_app", node_id=None, error_code="x", error_message="boom"
        )
    assert info.value.detail == "boom"
    assert info.value.error_code == "x"


async def test_environment_loop_accepts_command_gateway(probe_env, dbsession: AsyncSession) -> None:
    """The reconciler hands managers a NodeCommandGateway, not a NodeControlService."""
    from llm_port_backend.services.inference.drivers.ray.commands import NodeCommandGateway

    _cp, env, _nodes = probe_env
    fake = _FakeNodeControl(result_json=_healthy_probe_result())
    await _ray_driver().environment_manager.reconcile_environment(
        dbsession, env, node_control=NodeCommandGateway(fake)
    )
    assert env.status is EnvironmentStatus.READY
    assert fake.by_type(NodeCommandType.START_RAY_HEAD.value)


async def test_environment_missing_runtime_fails_with_reason(probe_env, dbsession: AsyncSession) -> None:
    """ENSURE results are awaited and checked: a missing runtime fails the env."""
    _cp, env, _nodes = probe_env
    fake = _FakeNodeControl(
        result_json=_healthy_probe_result(),
        results={NodeCommandType.ENSURE_RAY_RUNTIME.value: {"installed": False}},
    )
    await _ray_driver().environment_manager.reconcile_environment(dbsession, env, node_control=fake)
    assert env.status is EnvironmentStatus.FAILED
    assert "not installed" in env.observed_status_json["observation"]["reason"]
    assert not fake.by_type(NodeCommandType.START_RAY_HEAD.value)


async def test_environment_teardown_not_confirmed_is_not_stopped(
    probe_env, dbsession: AsyncSession
) -> None:
    """STOPPED is recorded only after the stop commands succeed."""
    _cp, env, _nodes = probe_env
    env.desired_state = "stopped"
    env.generation += 1
    await dbsession.flush()
    before = env.observed_generation
    fake = _FakeNodeControl(result_json=None, status=NodeCommandStatus.FAILED.value)
    await _ray_driver().environment_manager.reconcile_environment(dbsession, env, node_control=fake)
    assert env.status != EnvironmentStatus.STOPPED
    assert env.observed_generation == before  # still queued for a retry
    assert "teardown not confirmed" in env.observed_status_json["observation"]["reason"]


async def test_deployment_waits_for_ready_environment(deployment_env, dbsession: AsyncSession) -> None:
    """F40: no RUN against an environment that is not ready."""
    dep, _node = deployment_env
    env = await dbsession.get(InferenceEnvironment, dep.environment_id)
    env.status = EnvironmentStatus.PREPARING.value
    await dbsession.flush()
    fake = _FakeNodeControl(result_json=_healthy_probe_result())
    await _ray_driver().deployment_manager.reconcile_deployment(dbsession, dep, node_control=fake)
    assert dep.phase == "pending"
    assert dep.observed_generation != dep.generation
    assert not fake.by_type(NodeCommandType.RUN_SERVE_APP.value)


def _fast_readiness(monkeypatch: pytest.MonkeyPatch) -> None:
    from llm_port_backend.services.inference.drivers.ray import deployment as deployment_mod

    monkeypatch.setattr(deployment_mod, "_READINESS_PASS_BUDGET_SEC", 0.0)
    monkeypatch.setattr(deployment_mod, "_READINESS_POLL_SEC", 0.0)


async def test_deployment_observes_only_then_reapplies_when_app_lost(
    deployment_env, dbsession: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unchanged config + app present -> no RUN (a RUN restarts replicas);
    app missing after a cluster restart -> re-apply."""
    _fast_readiness(monkeypatch)
    dep, _node = deployment_env
    app = f"llmport-{dep.id}"

    def _fake(serve_result: dict[str, Any]) -> _FakeNodeControl:
        return _FakeNodeControl(
            result_json=_healthy_probe_result(),
            results={
                NodeCommandType.RUN_SERVE_APP.value: _run_serve_result(app),
                NodeCommandType.GET_RAY_SERVE_STATUS.value: serve_result,
            },
        )

    first = _fake(_serve_status(app))
    await _ray_driver().deployment_manager.reconcile_deployment(dbsession, dep, node_control=first)
    assert dep.phase == "running"
    assert len(first.by_type(NodeCommandType.RUN_SERVE_APP.value)) == 1

    again = _fake(_serve_status(app))
    await _ray_driver().deployment_manager.reconcile_deployment(dbsession, dep, node_control=again)
    assert again.by_type(NodeCommandType.RUN_SERVE_APP.value) == []
    assert dep.phase == "running"

    lost = _fake(_serve_status_without_apps())
    await _ray_driver().deployment_manager.reconcile_deployment(dbsession, dep, node_control=lost)
    assert len(lost.by_type(NodeCommandType.RUN_SERVE_APP.value)) == 1


async def test_deployment_deploy_failed_surfaces_ray_message(
    deployment_env, dbsession: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fast_readiness(monkeypatch)
    dep, _node = deployment_env
    app = f"llmport-{dep.id}"
    fake = _FakeNodeControl(
        result_json=_healthy_probe_result(),
        results={
            NodeCommandType.RUN_SERVE_APP.value: _run_serve_result(app),
            NodeCommandType.GET_RAY_SERVE_STATUS.value: _serve_status(
                app, app_status="DEPLOY_FAILED", message="Engine core initialization failed"
            ),
        },
    )
    await _ray_driver().deployment_manager.reconcile_deployment(dbsession, dep, node_control=fake)
    assert dep.phase == "failed"
    assert "Engine core initialization failed" in dep.phase_message


async def test_deployment_run_timeout_stays_applying(
    deployment_env, dbsession: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A command timeout is 'outcome unknown', never FAILED."""
    from llm_port_backend.services.inference.drivers.ray.client import (
        RayClusterClient,
        RayCommandError,
    )

    async def _timeout(self: Any, **_kwargs: Any) -> dict[str, Any]:
        raise RayCommandError(
            command_type="run_serve_app", node_id=None,
            error_code="command_timeout", error_message="no result",
        )

    monkeypatch.setattr(RayClusterClient, "run_serve_app", _timeout)
    dep, _node = deployment_env
    fake = _FakeNodeControl(result_json=_healthy_probe_result())
    await _ray_driver().deployment_manager.reconcile_deployment(dbsession, dep, node_control=fake)
    assert dep.phase == "applying"
    assert dep.observed_generation != dep.generation


async def test_request_reconcile_preserves_driver_bookkeeping(
    deployment_env, dbsession: AsyncSession
) -> None:
    """The API's reconcile request must not erase the applied config hash."""
    from llm_port_backend.services.inference.service import DeploymentService

    dep, _node = deployment_env
    dep.observed_generation = dep.generation
    dep.observed_status_json = {"applied_config_hash": "abc", "observation": {"reconciled": True}}
    await dbsession.flush()
    await DeploymentService(dbsession).request_reconcile(dep.id)
    assert dep.observed_generation == dep.generation - 1
    assert dep.observed_status_json["applied_config_hash"] == "abc"


async def test_reconcile_pass_hands_context_the_session_factory(monkeypatch: pytest.MonkeyPatch) -> None:
    """The loop must give the context its session factory so node commands are
    committed in their own transactions (visible to the stream handler)."""
    from llm_port_backend.db.dao import inference_dao
    from llm_port_backend.services.inference import reconciliation
    from llm_port_backend.web import lifespan

    row = SimpleNamespace(id=uuid.uuid4())

    class _Session:
        async def __aenter__(self) -> "_Session":
            return self

        async def __aexit__(self, *exc: Any) -> bool:
            return False

        async def get(self, _model: Any, _id: Any) -> Any:
            return row

        async def commit(self) -> None:
            return None

        async def rollback(self) -> None:
            return None

    def factory() -> _Session:
        return _Session()

    seen: dict[str, Any] = {}

    def _for_session(session: Any, session_factory: Any = None) -> Any:
        seen["factory"] = session_factory
        return SimpleNamespace()

    async def _pending(self: Any) -> list[Any]:
        return [row]

    reconciled: list[Any] = []

    async def _reconcile(_context: Any, obj: Any) -> None:
        reconciled.append(obj)

    monkeypatch.setattr(reconciliation.ReconciliationContext, "for_session", staticmethod(_for_session))
    monkeypatch.setattr(inference_dao.EnvironmentDAO, "list_pending_observation", _pending)
    monkeypatch.setattr(inference_dao.DeploymentDAO, "list_pending_observation", _pending)
    monkeypatch.setattr(reconciliation, "reconcile_environment", _reconcile)
    monkeypatch.setattr(reconciliation, "reconcile_deployment", _reconcile)

    app = SimpleNamespace(state=SimpleNamespace(db_session_factory=factory))
    await lifespan._run_inference_reconcile_pass(app)
    assert seen["factory"] is factory
    assert len(reconciled) == 2
