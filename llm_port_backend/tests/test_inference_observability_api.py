"""API tests for the inference observability routes (Phase 6, WI-3).

Covers the acceptance the plan names for this work item: happy path, RBAC,
unknown deployment, a driver with no log surface, and the ``tail`` bound.

The contract these tests defend is that the API stays backend-neutral: a
driver translates its own output into :class:`LogPage` / :class:`Metrics*`,
and nothing Ray-shaped reaches the response.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.dao.rbac_dao import RbacDAO
from llm_port_backend.db.models.inference import (
    InferenceControlPlane,
    InferenceDeployment,
    InferenceEnvironment,
    InferenceEnvironmentNode,
)
from llm_port_backend.db.models.llm import LLMModel, ModelSource, ModelStatus
from llm_port_backend.db.models.node_control import InfraNode
from llm_port_backend.db.models.users import User, current_active_user
from llm_port_backend.services.inference.contracts import InferenceDriver
from llm_port_backend.services.inference.drivers.ray.driver import RayDriver
from llm_port_backend.services.inference.observability import (
    DeploymentMetrics,
    EnvironmentMetrics,
    LogLine,
    LogPage,
    LogSource,
    MetricsPartial,
    ReplicaMetrics,
)
from llm_port_backend.services.inference.registry import registry

API = "/api/inference"

_SPEC: dict[str, Any] = {
    "api_version": "inference.llmport.ai/v1alpha1",
    "engine": {"name": "vllm", "config": {}},
    "scale": {"replicas": 1},
}


class _MuteDriver(InferenceDriver):
    """A driver with no observability surface.

    Explicitly subclasses the protocol so it inherits the default bodies,
    which raise :class:`ObservabilityUnsupported` -- the behaviour the 501
    mapping depends on.
    """

    key: str = "mute-test-driver"


async def _seed_roles(dbsession: AsyncSession) -> tuple[User, User]:
    """Return (viewer, outsider): one with the built-in viewer role, one with none."""
    rbac = RbacDAO(dbsession)
    await rbac.seed_defaults()

    viewer = User(
        email=f"viewer-{uuid.uuid4().hex}@test.local",
        hashed_password="x",
        is_verified=True,
        is_active=True,
        is_superuser=False,
    )
    outsider = User(
        email=f"outsider-{uuid.uuid4().hex}@test.local",
        hashed_password="x",
        is_verified=True,
        is_active=True,
        is_superuser=False,
    )
    dbsession.add(viewer)
    dbsession.add(outsider)
    await dbsession.flush()
    await rbac.assign_role(viewer.id, (await rbac.get_role_by_name("viewer")).id)
    return viewer, outsider


async def _seed_roles_with_admin(dbsession: AsyncSession) -> tuple[User, User]:
    """Return (viewer, admin).

    Membership changes sit behind ``inference.environments:update``, which the
    built-in *operator* role does not carry -- only *admin* does.  The tests
    use admin because that is what the routes actually require; whether an
    operator should be able to add or remove a node is a policy question
    recorded against the walkthrough, not something a test should paper over.
    """
    viewer, _ = await _seed_roles(dbsession)
    rbac = RbacDAO(dbsession)
    admin = User(
        email=f"admin-{uuid.uuid4().hex}@test.local",
        hashed_password="x",
        is_verified=True,
        is_active=True,
        is_superuser=False,
    )
    dbsession.add(admin)
    await dbsession.flush()
    await rbac.assign_role(admin.id, (await rbac.get_role_by_name("admin")).id)
    return viewer, admin


def _set_user(fastapi_app: FastAPI, user: User) -> None:
    fastapi_app.dependency_overrides[current_active_user] = lambda: user


async def _seed_deployment(
    dbsession: AsyncSession, *, driver: str = "ray"
) -> tuple[InferenceEnvironment, InferenceDeployment]:
    """Seed a control plane, node, environment and deployment."""
    control_plane = InferenceControlPlane(
        name=f"cp-{uuid.uuid4().hex[:8]}", driver=driver
    )
    dbsession.add(control_plane)
    await dbsession.flush()

    node = InfraNode(
        agent_id=f"node-{uuid.uuid4().hex[:8]}",
        host="10.0.0.1",
        status="healthy",
        scheduler_eligible=True,
    )
    dbsession.add(node)
    await dbsession.flush()

    environment = InferenceEnvironment(
        control_plane_id=control_plane.id,
        name=f"env-{uuid.uuid4().hex[:8]}",
        head_node_id=node.id,
    )
    dbsession.add(environment)
    await dbsession.flush()
    dbsession.add(
        InferenceEnvironmentNode(
            environment_id=environment.id, node_id=node.id, role="head"
        )
    )

    model = LLMModel(
        display_name="org/test-model",
        source=ModelSource.HUGGINGFACE,
        status=ModelStatus.AVAILABLE,
        hf_repo_id="org/test-model",
        hf_revision="main",
    )
    dbsession.add(model)
    await dbsession.flush()

    deployment = InferenceDeployment(
        environment_id=environment.id,
        model_id=model.id,
        name=f"dep-{uuid.uuid4().hex[:8]}",
        spec_json=_SPEC,
    )
    dbsession.add(deployment)
    await dbsession.commit()
    return environment, deployment


@pytest.mark.anyio
async def test_logs_happy_path_is_backend_neutral(
    client: AsyncClient,
    fastapi_app: FastAPI,
    dbsession: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A viewer reads a page of normalized logs; no Ray-shaped key appears."""
    viewer, _ = await _seed_roles(dbsession)
    _set_user(fastapi_app, viewer)
    _, deployment = await _seed_deployment(dbsession)

    async def fake_logs(self, session, dep, **kwargs) -> LogPage:  # noqa: ANN001
        assert kwargs["tail"] == 50
        assert kwargs["source"] == LogSource.RUNTIME_CONTAINER
        return LogPage(
            source=LogSource.RUNTIME_CONTAINER,
            node_id="node-1",
            lines=[
                LogLine(
                    ts=datetime(2026, 9, 20, 12, 0, tzinfo=UTC),
                    level="INFO",
                    message="Started",
                )
            ],
        )

    monkeypatch.setattr(RayDriver, "logs", fake_logs, raising=False)

    response = await client.get(f"{API}/deployments/{deployment.id}/logs?tail=50")
    assert response.status_code == 200
    body = response.json()
    assert body["source"] == "runtime_container"
    assert body["lines"][0]["message"] == "Started"

    # The plan forbids Ray's own log shapes from reaching the API contract.
    assert set(body) == {
        "source",
        "node_id",
        "replica_id",
        "lines",
        "truncated",
        "next_cursor",
        "detail",
    }
    for forbidden in ("ray", "serve", "actor", "job_id", "task_id"):
        assert forbidden not in response.text.lower().replace("serve_replica", "")


@pytest.mark.anyio
async def test_logs_empty_page_states_why(
    client: AsyncClient,
    fastapi_app: FastAPI,
    dbsession: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty page carries a reason, so silence never reads as health."""
    viewer, _ = await _seed_roles(dbsession)
    _set_user(fastapi_app, viewer)
    _, deployment = await _seed_deployment(dbsession)

    async def fake_logs(self, session, dep, **kwargs) -> LogPage:  # noqa: ANN001
        return LogPage(
            source=LogSource.RUNTIME_CONTAINER,
            detail="could not reach the node: timeout",
        )

    monkeypatch.setattr(RayDriver, "logs", fake_logs, raising=False)

    response = await client.get(f"{API}/deployments/{deployment.id}/logs")
    assert response.status_code == 200
    body = response.json()
    assert body["lines"] == []
    assert "timeout" in body["detail"]


@pytest.mark.anyio
async def test_logs_rejects_absurd_tail(
    client: AsyncClient,
    fastapi_app: FastAPI,
    dbsession: AsyncSession,
) -> None:
    """``tail`` is bounded at the route, so no caller can ask for a whole disk."""
    viewer, _ = await _seed_roles(dbsession)
    _set_user(fastapi_app, viewer)
    _, deployment = await _seed_deployment(dbsession)

    too_many = await client.get(
        f"{API}/deployments/{deployment.id}/logs?tail=10000000"
    )
    assert too_many.status_code == 422

    too_few = await client.get(f"{API}/deployments/{deployment.id}/logs?tail=0")
    assert too_few.status_code == 422


@pytest.mark.anyio
async def test_logs_unknown_deployment_is_404(
    client: AsyncClient,
    fastapi_app: FastAPI,
    dbsession: AsyncSession,
) -> None:
    """An unknown deployment is a 404, not an empty page."""
    viewer, _ = await _seed_roles(dbsession)
    _set_user(fastapi_app, viewer)
    await dbsession.commit()

    response = await client.get(f"{API}/deployments/{uuid.uuid4()}/logs")
    assert response.status_code == 404


@pytest.mark.anyio
async def test_observability_routes_require_read_permission(
    client: AsyncClient,
    fastapi_app: FastAPI,
    dbsession: AsyncSession,
) -> None:
    """A user with no role is refused on every observability route."""
    _, outsider = await _seed_roles(dbsession)
    environment, deployment = await _seed_deployment(dbsession)
    _set_user(fastapi_app, outsider)

    for path in (
        f"{API}/deployments/{deployment.id}/logs",
        f"{API}/deployments/{deployment.id}/metrics",
        f"{API}/environments/{environment.id}/metrics",
        f"{API}/environments/{environment.id}/nodes",
    ):
        response = await client.get(path)
        assert response.status_code == 403, path


@pytest.mark.anyio
async def test_driver_without_logs_returns_501(
    client: AsyncClient,
    fastapi_app: FastAPI,
    dbsession: AsyncSession,
) -> None:
    """A driver with no log surface answers 501, never an empty log view."""
    viewer, _ = await _seed_roles(dbsession)
    _set_user(fastapi_app, viewer)
    registry.register(_MuteDriver.key, _MuteDriver)
    _, deployment = await _seed_deployment(dbsession, driver=_MuteDriver.key)

    logs = await client.get(f"{API}/deployments/{deployment.id}/logs")
    assert logs.status_code == 501
    assert "logs" in logs.json()["detail"]

    metrics = await client.get(f"{API}/deployments/{deployment.id}/metrics")
    assert metrics.status_code == 501


@pytest.mark.anyio
async def test_unregistered_driver_returns_501(
    client: AsyncClient,
    fastapi_app: FastAPI,
    dbsession: AsyncSession,
) -> None:
    """An environment on an unregistered driver is a capability gap, not a 500."""
    viewer, _ = await _seed_roles(dbsession)
    _set_user(fastapi_app, viewer)
    _, deployment = await _seed_deployment(dbsession, driver="no-such-driver")

    response = await client.get(f"{API}/deployments/{deployment.id}/logs")
    assert response.status_code == 501
    assert "no driver registered" in response.json()["detail"]


@pytest.mark.anyio
async def test_deployment_metrics_reports_all_three_tiers(
    client: AsyncClient,
    fastapi_app: FastAPI,
    dbsession: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One response carries the environment, Serve application and replica tiers."""
    viewer, _ = await _seed_roles(dbsession)
    _set_user(fastapi_app, viewer)
    _, deployment = await _seed_deployment(dbsession)

    async def fake_metrics(self, session, dep, **kwargs) -> DeploymentMetrics:  # noqa: ANN001
        return DeploymentMetrics(
            deployment_id=str(dep.id),
            app_name="llm-port-dep",
            app_status="RUNNING",
            replicas_ready=1,
            replicas_total=2,
            deployments=[
                ReplicaMetrics(
                    deployment_name="LLMDeployment:model",
                    status="HEALTHY",
                    replicas_ready=1,
                    replicas_pending=1,
                )
            ],
            partials=[
                MetricsPartial(
                    tier="node_metrics",
                    reason="1 of 2 live nodes export no metrics port",
                )
            ],
            observed_at=datetime.now(tz=UTC),
        )

    monkeypatch.setattr(RayDriver, "deployment_metrics", fake_metrics, raising=False)

    response = await client.get(f"{API}/deployments/{deployment.id}/metrics")
    assert response.status_code == 200
    body = response.json()
    assert body["app_status"] == "RUNNING"
    assert body["deployments"][0]["replicas_pending"] == 1
    # A tier that could not report says so instead of reporting zero.
    assert body["partials"][0]["tier"] == "node_metrics"


@pytest.mark.anyio
async def test_environment_metrics_partial_is_not_a_zero(
    client: AsyncClient,
    fastapi_app: FastAPI,
    dbsession: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unreported tiers arrive as labelled partials alongside the live values."""
    viewer, _ = await _seed_roles(dbsession)
    _set_user(fastapi_app, viewer)
    environment, _ = await _seed_deployment(dbsession)

    async def fake_env_metrics(self, session, env, **kwargs) -> EnvironmentMetrics:  # noqa: ANN001
        return EnvironmentMetrics(
            environment_id=str(env.id),
            alive=True,
            nodes_total=2,
            nodes_alive=2,
            gpus_total=2.0,
            partials=[
                MetricsPartial(tier="node_metrics", reason="worker exports no port")
            ],
        )

    monkeypatch.setattr(RayDriver, "environment_metrics", fake_env_metrics, raising=False)

    response = await client.get(f"{API}/environments/{environment.id}/metrics")
    assert response.status_code == 200
    body = response.json()
    assert body["nodes_alive"] == 2
    assert body["partials"] == [
        {
            "tier": "node_metrics",
            "reason": "worker exports no port",
            # A partial is a warning unless it says otherwise: this one is a
            # real gap, so the default is what it should carry.
            "severity": "warning",
        }
    ]


@pytest.mark.anyio
async def test_environment_nodes_lists_members(
    client: AsyncClient,
    fastapi_app: FastAPI,
    dbsession: AsyncSession,
) -> None:
    """Membership is readable: the UI's "participating nodes" needs a source."""
    viewer, _ = await _seed_roles(dbsession)
    _set_user(fastapi_app, viewer)
    environment, _ = await _seed_deployment(dbsession)

    response = await client.get(f"{API}/environments/{environment.id}/nodes")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["role"] == "head"
    assert body[0]["node_id"]


@pytest.mark.anyio
async def test_environment_nodes_unknown_environment_is_404(
    client: AsyncClient,
    fastapi_app: FastAPI,
    dbsession: AsyncSession,
) -> None:
    """An unknown environment is a 404, not an empty member list."""
    viewer, _ = await _seed_roles(dbsession)
    _set_user(fastapi_app, viewer)
    await dbsession.commit()

    response = await client.get(f"{API}/environments/{uuid.uuid4()}/nodes")
    assert response.status_code == 404


@pytest.mark.anyio
async def test_environment_node_can_be_removed(
    client: AsyncClient,
    fastapi_app: FastAPI,
    dbsession: AsyncSession,
) -> None:
    """The counterpart to POST .../nodes: a mis-added node is undoable in-product."""
    _, admin = await _seed_roles_with_admin(dbsession)
    _set_user(fastapi_app, admin)
    environment, _ = await _seed_deployment(dbsession)

    worker = InfraNode(
        agent_id=f"node-{uuid.uuid4().hex[:8]}",
        host="10.0.0.2",
        status="healthy",
        scheduler_eligible=True,
    )
    dbsession.add(worker)
    await dbsession.flush()
    dbsession.add(
        InferenceEnvironmentNode(
            environment_id=environment.id, node_id=worker.id, role="worker"
        )
    )
    await dbsession.commit()

    before = await client.get(f"{API}/environments/{environment.id}/nodes")
    assert len(before.json()) == 2

    removed = await client.delete(
        f"{API}/environments/{environment.id}/nodes/{worker.id}"
    )
    assert removed.status_code == 204

    after = await client.get(f"{API}/environments/{environment.id}/nodes")
    assert [row["role"] for row in after.json()] == ["head"]


@pytest.mark.anyio
async def test_removing_the_head_of_a_running_environment_is_refused(
    client: AsyncClient,
    fastapi_app: FastAPI,
    dbsession: AsyncSession,
) -> None:
    """Otherwise ``head_node_id`` would dangle at a node that is no longer a member."""
    _, admin = await _seed_roles_with_admin(dbsession)
    _set_user(fastapi_app, admin)
    environment, _ = await _seed_deployment(dbsession)

    response = await client.delete(
        f"{API}/environments/{environment.id}/nodes/{environment.head_node_id}"
    )
    assert response.status_code == 409
    assert "head node" in response.json()["detail"]

    # Still a member: a refused call must not half-apply.
    listing = await client.get(f"{API}/environments/{environment.id}/nodes")
    assert len(listing.json()) == 1


@pytest.mark.anyio
async def test_head_can_be_removed_once_the_environment_is_stopped(
    client: AsyncClient,
    fastapi_app: FastAPI,
    dbsession: AsyncSession,
) -> None:
    """The guard is about a running cluster, not an immovable binding."""
    _, admin = await _seed_roles_with_admin(dbsession)
    _set_user(fastapi_app, admin)
    environment, _ = await _seed_deployment(dbsession)

    stopped = await client.patch(
        f"{API}/environments/{environment.id}", json={"desired_state": "stopped"}
    )
    assert stopped.status_code == 200

    response = await client.delete(
        f"{API}/environments/{environment.id}/nodes/{environment.head_node_id}"
    )
    assert response.status_code == 204


@pytest.mark.anyio
async def test_removing_a_node_needs_more_than_read(
    client: AsyncClient,
    fastapi_app: FastAPI,
    dbsession: AsyncSession,
) -> None:
    """A viewer can list members but cannot change membership."""
    viewer, _ = await _seed_roles(dbsession)
    _set_user(fastapi_app, viewer)
    environment, _ = await _seed_deployment(dbsession)

    response = await client.delete(
        f"{API}/environments/{environment.id}/nodes/{environment.head_node_id}"
    )
    assert response.status_code == 403


@pytest.mark.anyio
async def test_drivers_route_lists_registered_keys(
    client: AsyncClient,
    fastapi_app: FastAPI,
    dbsession: AsyncSession,
) -> None:
    """The UI offers registered drivers only, so a control plane cannot be
    created against a driver that would answer 501 to every call."""
    viewer, _ = await _seed_roles(dbsession)
    _set_user(fastapi_app, viewer)
    await dbsession.commit()

    response = await client.get(f"{API}/drivers")
    assert response.status_code == 200
    assert "ray" in response.json()
