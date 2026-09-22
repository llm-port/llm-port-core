"""A provider that belongs to the deployment serving it.

The providers screen is where an operator manages where models come from,
local or remote. A model served by one of our own clusters was not on it at
all — the page showed a stale local runtime whose container had been deleted,
and nothing about the cluster that was actually answering requests. So the
only thing under "LLM Providers" was a red error, and the thing that worked
was invisible.

The rule this encodes: the deployment owns the row. It creates it when it
starts serving, keeps it while stopped, and takes it away when it goes. And
because it owns the row, nothing else may edit it — a change made on the
providers screen would be overwritten by the next reconcile, and until then
the screen would show something untrue.

Ownership is marked with ``source_kind``/``source_id``, the same names and
values the gateway already stamps on its routing records. Two layers
describing ownership in two vocabularies is how they come apart.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.models.inference import (
    DeploymentDesiredState,
    DeploymentPhase,
    EndpointStatus,
    InferenceControlPlane,
    InferenceDeployment,
    InferenceEnvironment,
)
from llm_port_backend.db.models.llm import (
    LLMModel,
    LLMProvider,
    ModelSource,
    ModelStatus,
    ProviderTarget,
    ProviderType,
)
from llm_port_backend.db.dao.inference_dao import EndpointDAO
from llm_port_backend.services.inference.publication import (
    DERIVED_SOURCE_KIND,
    InferencePublicationCoordinator,
)

pytestmark = pytest.mark.anyio()


async def _deployment(
    session: AsyncSession,
    *,
    phase: str = DeploymentPhase.RUNNING.value,
    desired: str = DeploymentDesiredState.ACTIVE.value,
    ready: int = 2,
) -> InferenceDeployment:
    control_plane = InferenceControlPlane(
        name=f"cp-{uuid.uuid4().hex[:8]}", driver="ray"
    )
    session.add(control_plane)
    await session.flush()
    env = InferenceEnvironment(
        control_plane_id=control_plane.id,
        name=f"env-{uuid.uuid4().hex[:8]}",
        desired_state="running",
    )
    session.add(env)
    await session.flush()
    model = LLMModel(
        display_name="Qwen/Qwen2.5-0.5B-Instruct",
        source=ModelSource.HUGGINGFACE,
        status=ModelStatus.AVAILABLE,
        hf_repo_id="Qwen/Qwen2.5-0.5B-Instruct",
        hf_revision="main",
    )
    session.add(model)
    await session.flush()
    deployment = InferenceDeployment(
        environment_id=env.id,
        model_id=model.id,
        name=f"dep-{uuid.uuid4().hex[:8]}",
        spec_json={},
        phase=phase,
        desired_state=desired,
        ready_replicas=ready,
        total_replicas=ready,
    )
    session.add(deployment)
    await session.flush()
    return deployment


async def _publish(
    session: AsyncSession, deployment: InferenceDeployment
) -> None:
    """Give the deployment a published endpoint, as a serving one has."""
    dao = EndpointDAO(session)
    await dao.create(
        deployment.id,
        name="openai",
        address="http://10.88.10.71:8000/llmport-app",
        path="/v1",
        status=EndpointStatus.PUBLISHED,
    )
    await session.flush()


async def _provider_for(
    session: AsyncSession, deployment_id: uuid.UUID
) -> LLMProvider | None:
    rows = await session.execute(
        select(LLMProvider).where(
            LLMProvider.source_kind == DERIVED_SOURCE_KIND,
            LLMProvider.source_id == str(deployment_id),
        )
    )
    return rows.scalars().first()


def _coordinator(session: AsyncSession) -> InferencePublicationCoordinator:
    # No gateway: the provider row is the backend's own and must not depend on
    # one being configured. Inside that guard, a stack without a gateway
    # listed no provider for a cluster that was serving.
    return InferencePublicationCoordinator(session)


# ── created at the end of the process ────────────────────────────────────


async def test_a_serving_deployment_gets_a_provider(dbsession: AsyncSession) -> None:
    deployment = await _deployment(dbsession)
    await _publish(dbsession, deployment)

    await _coordinator(dbsession).reconcile_derived_provider(deployment)

    provider = await _provider_for(dbsession, deployment.id)
    assert provider is not None
    assert provider.name == deployment.name
    assert provider.target == ProviderTarget.INFERENCE_CLUSTER
    assert provider.type == ProviderType.VLLM
    assert provider.endpoint_url and provider.endpoint_url.endswith("/v1")


async def test_it_is_marked_as_owned_the_way_the_gateway_marks_its_own(
    dbsession: AsyncSession,
) -> None:
    """One vocabulary for ownership across both layers."""
    deployment = await _deployment(dbsession)
    await _publish(dbsession, deployment)
    await _coordinator(dbsession).reconcile_derived_provider(deployment)

    provider = await _provider_for(dbsession, deployment.id)
    assert provider.source_kind == "inference_deployment"
    assert provider.source_id == str(deployment.id)
    assert provider.is_derived is True


async def test_a_deployment_that_has_never_served_gets_no_row(
    dbsession: AsyncSession,
) -> None:
    """Nothing published yet, so there is nothing to manage.

    Creating one here would put a provider on the screen for something that
    has never answered a request.
    """
    deployment = await _deployment(dbsession, phase=DeploymentPhase.PENDING.value)

    await _coordinator(dbsession).reconcile_derived_provider(deployment)

    assert await _provider_for(dbsession, deployment.id) is None


async def test_reconciling_twice_does_not_make_two(dbsession: AsyncSession) -> None:
    """Reconcile passes repeat; the row is keyed by owner, not by name."""
    deployment = await _deployment(dbsession)
    await _publish(dbsession, deployment)
    coordinator = _coordinator(dbsession)

    await coordinator.reconcile_derived_provider(deployment)
    await coordinator.reconcile_derived_provider(deployment)

    rows = await dbsession.execute(
        select(LLMProvider).where(LLMProvider.source_id == str(deployment.id))
    )
    assert len(rows.scalars().all()) == 1


async def test_renaming_a_deployment_moves_its_provider(
    dbsession: AsyncSession,
) -> None:
    """Keyed on the owner, so a rename does not strand the old row."""
    deployment = await _deployment(dbsession)
    await _publish(dbsession, deployment)
    coordinator = _coordinator(dbsession)
    await coordinator.reconcile_derived_provider(deployment)

    deployment.name = "renamed-deployment"
    await coordinator.reconcile_derived_provider(deployment)

    rows = await dbsession.execute(
        select(LLMProvider).where(LLMProvider.source_id == str(deployment.id))
    )
    providers = rows.scalars().all()
    assert len(providers) == 1
    assert providers[0].name == "renamed-deployment"


# ── removed when the deployment is ───────────────────────────────────────


async def test_a_deleted_deployment_takes_its_provider_with_it(
    dbsession: AsyncSession,
) -> None:
    """The point of the whole change: no stale providers."""
    deployment = await _deployment(dbsession)
    await _publish(dbsession, deployment)
    coordinator = _coordinator(dbsession)
    await coordinator.reconcile_derived_provider(deployment)
    assert await _provider_for(dbsession, deployment.id) is not None

    deployment.phase = DeploymentPhase.DELETED.value
    await coordinator.reconcile_derived_provider(deployment)

    assert await _provider_for(dbsession, deployment.id) is None


async def test_removal_is_idempotent(dbsession: AsyncSession) -> None:
    deployment = await _deployment(dbsession, phase=DeploymentPhase.DELETED.value)
    coordinator = _coordinator(dbsession)

    assert await coordinator.remove_derived_provider(deployment.id) is False
    await coordinator.reconcile_derived_provider(deployment)


async def test_a_stopped_deployment_keeps_its_provider(
    dbsession: AsyncSession,
) -> None:
    """Stopped is not gone.

    The moment a deployment stops serving is the moment an operator goes
    looking for it, and a row that vanishes then is a row that vanishes
    exactly when it is wanted. A stopped local runtime keeps its row for the
    same reason.
    """
    deployment = await _deployment(dbsession)
    await _publish(dbsession, deployment)
    coordinator = _coordinator(dbsession)
    await coordinator.reconcile_derived_provider(deployment)

    deployment.phase = DeploymentPhase.STOPPED.value
    await coordinator.reconcile_derived_provider(deployment)

    assert await _provider_for(dbsession, deployment.id) is not None


async def test_losing_every_replica_keeps_the_provider(
    dbsession: AsyncSession,
) -> None:
    deployment = await _deployment(dbsession)
    await _publish(dbsession, deployment)
    coordinator = _coordinator(dbsession)
    await coordinator.reconcile_derived_provider(deployment)

    deployment.ready_replicas = 0
    await coordinator.reconcile_derived_provider(deployment)

    assert await _provider_for(dbsession, deployment.id) is not None


# ── a person's provider is never touched ─────────────────────────────────


async def test_a_hand_made_provider_is_left_alone(dbsession: AsyncSession) -> None:
    """Nothing here may delete a row a person created.

    The removal is keyed on ownership, so a provider with no owner is out of
    reach of it however similar it looks.
    """
    mine = LLMProvider(
        name="qwen-mini",
        type=ProviderType.VLLM,
        target=ProviderTarget.LOCAL_DOCKER,
    )
    dbsession.add(mine)
    await dbsession.flush()

    deployment = await _deployment(dbsession, phase=DeploymentPhase.DELETED.value)
    await _coordinator(dbsession).reconcile_derived_provider(deployment)

    assert await dbsession.get(LLMProvider, mine.id) is not None
    assert mine.is_derived is False


async def test_a_reconcile_failure_never_fails_the_deployment(
    dbsession: AsyncSession,
) -> None:
    """A display record is not worth failing a deployment pass over."""

    class _Broken:
        id = uuid.uuid4()
        name = "broken"
        desired_state = DeploymentDesiredState.ACTIVE.value
        phase = DeploymentPhase.RUNNING.value

        @property
        def ready_replicas(self) -> int:
            raise RuntimeError("something went wrong reading this deployment")

    # Must not raise.
    await _coordinator(dbsession).reconcile_derived_provider(_Broken())


async def _admin(dbsession: AsyncSession, fastapi_app) -> None:
    """Sign in as somebody allowed to manage providers.

    The guard under test refuses an *authorised* caller, so the test has to
    get past authorisation first or a 401 would masquerade as a pass.
    """
    from llm_port_backend.db.dao.rbac_dao import RbacDAO
    from llm_port_backend.db.models.users import User, current_active_user

    rbac = RbacDAO(dbsession)
    await rbac.seed_defaults()
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
    fastapi_app.dependency_overrides[current_active_user] = lambda: admin

    # The delete route depends on the LLM service, and FastAPI resolves every
    # dependency before the handler runs -- so without one the request fails
    # on the dependency rather than reaching the guard under test. It is never
    # called: the guard refuses first.
    from llm_port_backend.web.api.llm.dependencies import get_llm_service

    fastapi_app.dependency_overrides[get_llm_service] = lambda: object()


# ── the providers API refuses to manage what it does not own ─────────────


async def test_the_api_refuses_to_edit_a_derived_provider(
    client, fastapi_app, dbsession: AsyncSession
) -> None:
    """A change here would not stick.

    The next reconcile overwrites it, and until then the screen shows
    something that is not true. Refusing, naming the owner, is more useful
    than a control that appears to work.
    """
    await _admin(dbsession, fastapi_app)
    deployment = await _deployment(dbsession)
    await _publish(dbsession, deployment)
    await _coordinator(dbsession).reconcile_derived_provider(deployment)
    await dbsession.commit()
    provider = await _provider_for(dbsession, deployment.id)

    response = await client.patch(
        f"/api/llm/providers/{provider.id}", json={"name": "hijacked"}
    )

    assert response.status_code == 409
    assert str(deployment.id) in response.json()["detail"]


async def test_the_api_refuses_to_delete_a_derived_provider(
    client, fastapi_app, dbsession: AsyncSession
) -> None:
    await _admin(dbsession, fastapi_app)
    deployment = await _deployment(dbsession)
    await _publish(dbsession, deployment)
    await _coordinator(dbsession).reconcile_derived_provider(deployment)
    await dbsession.commit()
    provider = await _provider_for(dbsession, deployment.id)

    response = await client.delete(f"/api/llm/providers/{provider.id}")

    assert response.status_code == 409
    assert (await _provider_for(dbsession, deployment.id)) is not None


async def test_a_hand_made_provider_can_still_be_edited(
    client, fastapi_app, dbsession: AsyncSession
) -> None:
    """The guard must not lock everybody out of their own providers."""
    await _admin(dbsession, fastapi_app)
    mine = LLMProvider(
        name="hand-made",
        type=ProviderType.VLLM,
        target=ProviderTarget.REMOTE_ENDPOINT,
        endpoint_url="http://10.88.10.80:8000/v1",
    )
    dbsession.add(mine)
    await dbsession.commit()

    response = await client.patch(
        f"/api/llm/providers/{mine.id}", json={"name": "renamed"}
    )
    assert response.status_code == 200


async def test_the_list_names_the_owner_so_the_screen_can_link_to_it(
    client, fastapi_app, dbsession: AsyncSession
) -> None:
    """Resolved by the backend, not by the screen joining deployments itself.

    Two lookups are two answers that can disagree, and this page is exactly
    where they would be seen side by side.
    """
    await _admin(dbsession, fastapi_app)
    deployment = await _deployment(dbsession)
    await _publish(dbsession, deployment)
    await _coordinator(dbsession).reconcile_derived_provider(deployment)
    await dbsession.commit()

    response = await client.get("/api/llm/providers/")
    assert response.status_code == 200
    derived = [p for p in response.json() if p["source_kind"]]
    assert len(derived) >= 1
    owner = next(p for p in derived if p["source_id"] == str(deployment.id))
    assert owner["managed_by"]["kind"] == "inference_deployment"
    assert owner["managed_by"]["name"] == deployment.name
    assert owner["managed_by"]["state"] == "running"
    # The model column has no runtime row to join through, so the owner
    # carries it; without this the column read "no runtime" for a deployment
    # that was serving one.
    assert owner["managed_by"]["model_name"] == "Qwen/Qwen2.5-0.5B-Instruct"
    assert owner["target"] == "inference_cluster"


# ── the stat cards, for either kind of provider ──────────────────────────


async def test_the_cards_answer_for_a_cluster_backed_provider(
    client, fastapi_app, dbsession: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The reason this endpoint exists at all.

    A cluster-backed provider has no runtime and no container, so the
    runtime-keyed route could not answer for it and the screen offered a link
    to the deployment instead. That is a dead end for somebody whose role
    reaches the providers page and not the deployments page, and it turns
    "is the hardware working" into a two-screen question.

    The figures are labelled with the *environment's* name, because that is
    what ``sync_ray_targets`` writes onto the scrape targets and what the
    cluster dashboard selects on.
    """
    await _admin(dbsession, fastapi_app)
    deployment = await _deployment(dbsession)
    await _publish(dbsession, deployment)
    await _coordinator(dbsession).reconcile_derived_provider(deployment)
    await dbsession.commit()
    provider = await _provider_for(dbsession, deployment.id)

    environment = await dbsession.get(InferenceEnvironment, deployment.environment_id)
    asked: dict[str, object] = {}

    async def fake_stats(self, runtime_id, runtime_name):  # noqa: ANN001
        asked["id"] = runtime_id
        asked["name"] = runtime_name
        return {
            "enabled": True,
            "stale": False,
            "dashboard_url": "http://grafana/d/cluster",
            "stats": {"generation_tokens_per_sec": 32.0},
        }

    from llm_port_backend.services.llm.monitoring import MonitoringProvisioner

    monkeypatch.setattr(MonitoringProvisioner, "stats", fake_stats, raising=False)

    response = await client.get(f"/api/llm/providers/{provider.id}/monitoring-stats")

    assert response.status_code == 200
    body = response.json()
    assert body["enabled"] is True
    assert body["stats"]["generation_tokens_per_sec"] == 32.0
    # Asked under the environment's name, not the deployment's or provider's.
    assert asked["name"] == environment.name
    assert asked["id"] == environment.id


async def test_a_provider_with_nothing_to_report_says_so_rather_than_failing(
    client, fastapi_app, dbsession: AsyncSession
) -> None:
    """The card row renders a muted state from this, not an error.

    A 500 here would put an error on the providers page for a provider that
    is simply not monitored.
    """
    await _admin(dbsession, fastapi_app)
    mine = LLMProvider(
        name="remote-only",
        type=ProviderType.CLOUD,
        target=ProviderTarget.REMOTE_ENDPOINT,
    )
    dbsession.add(mine)
    await dbsession.commit()

    response = await client.get(f"/api/llm/providers/{mine.id}/monitoring-stats")

    assert response.status_code == 200
    assert response.json()["enabled"] is False


async def test_an_unknown_provider_is_a_404(
    client, fastapi_app, dbsession: AsyncSession
) -> None:
    await _admin(dbsession, fastapi_app)
    response = await client.get(
        f"/api/llm/providers/{uuid.uuid4()}/monitoring-stats"
    )
    assert response.status_code == 404
