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
    InferenceEnvironment,
    InferenceEnvironmentNode,
)
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
        nodes=[{"node_ip": "10.0.0.1", "state": "ALIVE", "is_head": True}][:num_nodes],
        total_gpus=total_gpus,
        available_gpus=total_gpus,
        cluster_address=cluster_address,
    )


def _command_row(
    command_id: uuid.UUID,
    status: str,
    result_json: dict[str, Any] | None = None,
) -> InfraNodeCommand:
    return InfraNodeCommand(
        id=command_id,
        node_id=uuid.uuid4(),
        command_type=NodeCommandType.GET_RAY_STATUS.value,
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
    """Minimal NodeControlService double with a scriptable command lifecycle."""

    def __init__(
        self,
        *,
        result_json: dict[str, Any] | None,
        status: str = NodeCommandStatus.SUCCEEDED.value,
    ) -> None:
        self._result_json = result_json
        self._status = status
        self.issued: list[dict[str, Any]] = []

    async def issue_command(self, **kwargs: Any) -> Any:
        self.issued.append(kwargs)
        return _command_row(
            uuid.uuid4(),
            self._status,
            self._result_json if self._status == NodeCommandStatus.SUCCEEDED.value else None,
        )

    async def get_command(self, *, command_id: uuid.UUID) -> InfraNodeCommand:
        return _command_row(
            command_id,
            self._status,
            self._result_json if self._status == NodeCommandStatus.SUCCEEDED.value else None,
        )

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
    # A head-only cluster (exactly one node) has no workers condition yet.
    head_only = {c["type"] for c in build_environment_conditions(_cluster(num_nodes=1), expected_nodes=3)}
    assert head_only == {"HeadActive"}


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
    session: AsyncSession, *, generation: int = 1, observed_generation: int = 0
) -> InferenceEnvironment:
    cp = InferenceControlPlane(name=f"cp-{uuid.uuid4().hex[:12]}", driver="noop")
    session.add(cp)
    await session.flush()
    env = InferenceEnvironment(
        control_plane_id=cp.id,
        name=f"env-{uuid.uuid4().hex[:12]}",
        generation=generation,
        observed_generation=observed_generation,
    )
    session.add(env)
    await session.flush()
    return env


async def test_pending_observation_only_lags(dbsession: AsyncSession) -> None:
    pending_env = await _seed_environment(dbsession, generation=1, observed_generation=0)
    observed_env = await _seed_environment(dbsession, generation=2, observed_generation=2)
    assert pending_env.id != observed_env.id

    pending = await EnvironmentDAO(dbsession).list_pending_observation()
    ids = {e.id for e in pending}
    assert pending_env.id in ids
    # A non-deleted, fully observed environment must NOT be pending (bug B10).
    assert observed_env.id not in ids


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
    fake = _FakeNodeControl(result_json=_healthy_probe_result())
    report = await _ray_driver().probe(cp, session=dbsession, node_control=fake)

    assert report["reconciled"] is True
    assert report["probed"] is True
    assert report["alive"] is True
    assert report["num_nodes"] == 2
    assert report["cluster_address"] == "10.0.0.1:6379"
    # Exactly one GET_RAY_STATUS, addressed at the head node, no secrets shipped.
    assert len(fake.issued) == 1
    issued = fake.issued[0]
    assert issued["command_type"] == NodeCommandType.GET_RAY_STATUS.value
    assert issued["node_id"] == nodes[0].id
    assert not (issued["payload"] or {}).get("token")


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
    # No lifecycle start/join work for a stopped environment.
    assert fake.by_type(NodeCommandType.START_RAY_HEAD.value) == []
    assert fake.by_type(NodeCommandType.JOIN_RAY_CLUSTER.value) == []
    assert env.status is EnvironmentStatus.STOPPED


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
    assert len(fake.issued) == 1
    assert fake.issued[0]["command_type"] == NodeCommandType.GET_RAY_STATUS.value
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
