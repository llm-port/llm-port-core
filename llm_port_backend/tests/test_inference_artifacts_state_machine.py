"""Artifact state-machine behaviour against a real session (4C-01, 4C-03, 4C-06, 4C-07).

These drive ``ModelArtifactCoordinator`` through a real database rather than a
mocked DAO.  The defects they pin were all invisible to the mock-based suite:
a mocked DAO records calls but cannot show that a row's state was destroyed.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.dao.inference_dao import ModelAvailabilityDAO
from llm_port_backend.db.models.inference import (
    InferenceControlPlane,
    InferenceEnvironment,
    InferenceEnvironmentNode,
    ModelAvailabilityStatus,
)
from llm_port_backend.db.models.llm import LLMModel, ModelSource, ModelStatus
from llm_port_backend.db.models.node_control import InfraNode, InfraNodeCommand
from llm_port_backend.services.inference.artifacts import (
    ModelArtifactCoordinator,
    _default_revision,
)
from llm_port_backend.services.inference.drivers.ray.commands import NodeCommandGateway


async def _environment(dbsession: AsyncSession, *, nodes: int = 1):
    cp = InferenceControlPlane(name=f"cp-{uuid.uuid4().hex[:6]}", driver="ray")
    dbsession.add(cp)
    await dbsession.flush()
    env = InferenceEnvironment(
        control_plane_id=cp.id, name=f"env-{uuid.uuid4().hex[:6]}", desired_state="running",
    )
    dbsession.add(env)
    model = LLMModel(
        display_name=f"m-{uuid.uuid4().hex[:6]}",
        source=ModelSource.HUGGINGFACE,
        hf_repo_id=f"org/m-{uuid.uuid4().hex[:6]}",
        status=ModelStatus.AVAILABLE,
    )
    dbsession.add(model)
    made: list[InfraNode] = []
    for i in range(nodes):
        n = InfraNode(agent_id=f"n-{uuid.uuid4().hex[:6]}", host=f"10.0.0.{i + 1}", status="healthy")
        dbsession.add(n)
        made.append(n)
    await dbsession.flush()
    for n in made:
        dbsession.add(
            InferenceEnvironmentNode(environment_id=env.id, node_id=n.id, role="head")
        )
    await dbsession.flush()
    return env, model, made


async def _commands_for(dbsession: AsyncSession, node_id: uuid.UUID) -> list[InfraNodeCommand]:
    res = await dbsession.execute(
        select(InfraNodeCommand).where(InfraNodeCommand.node_id == node_id)
    )
    return list(res.scalars().all())


@pytest.mark.anyio()
async def test_in_flight_sync_is_not_reset_to_pending(dbsession: AsyncSession) -> None:
    """A reconcile pass must not destroy the progress of a running sync.

    ``evaluate`` files a SYNCING row under "pending", and ``ensure`` used to
    re-mark every pending node PENDING with "Sync command queued" - so the row
    flapped PENDING/SYNCING for the whole transfer and the reported percentage
    reset on every pass.
    """
    env, model, nodes = await _environment(dbsession)
    node = nodes[0]
    dao = ModelAvailabilityDAO(dbsession)
    await dao.mark(
        model.id, node.id, ModelAvailabilityStatus.SYNCING,
        progress=42.0, status_message="Model sync blob abc123 (42%)",
    )
    await dbsession.flush()

    coordinator = ModelArtifactCoordinator(dbsession, gateway=NodeCommandGateway(dbsession))
    await coordinator.ensure(model=model, environment=env)
    await dbsession.flush()

    row = await dao.get(model.id, node.id)
    assert row.status == ModelAvailabilityStatus.SYNCING.value
    assert row.progress == 42.0
    assert "42%" in (row.status_message or "")


@pytest.mark.anyio()
async def test_failed_sync_is_retried_after_backoff(dbsession: AsyncSession) -> None:
    """A transient failure must not disable local artifacts forever."""
    env, model, nodes = await _environment(dbsession)
    node = nodes[0]
    dao = ModelAvailabilityDAO(dbsession)
    gateway = NodeCommandGateway(dbsession)

    # Just failed: within the backoff window, so no new command.
    await dao.mark(
        model.id, node.id, ModelAvailabilityStatus.FAILED, status_message="connection reset",
    )
    await dbsession.flush()
    await ModelArtifactCoordinator(dbsession, gateway=gateway).ensure(
        model=model, environment=env
    )
    await dbsession.flush()
    assert len(await _commands_for(dbsession, node.id)) == 0

    # Failure is now old: the node is retried.
    row = await dao.get(model.id, node.id)
    row.updated_at = datetime.now(tz=UTC) - timedelta(hours=1)
    await dbsession.flush()
    await ModelArtifactCoordinator(dbsession, gateway=gateway).ensure(
        model=model, environment=env
    )
    await dbsession.flush()
    assert len(await _commands_for(dbsession, node.id)) == 1


@pytest.mark.anyio()
async def test_read_only_evaluate_writes_nothing(dbsession: AsyncSession) -> None:
    """The readiness route must not reconcile rows just by being polled."""
    env, model, nodes = await _environment(dbsession)
    node = nodes[0]
    dao = ModelAvailabilityDAO(dbsession)

    coordinator = ModelArtifactCoordinator(dbsession)
    readiness = await coordinator.evaluate(model=model, environment=env, persist=False)
    await dbsession.flush()

    assert readiness.all_ready is False
    assert str(node.id) in readiness.pending_node_ids
    assert await dao.get(model.id, node.id) is None, "read-only evaluate created a row"

    # The reconciling form still records what it found.
    await coordinator.evaluate(model=model, environment=env)
    await dbsession.flush()
    row = await dao.get(model.id, node.id)
    assert row is not None
    assert row.status == ModelAvailabilityStatus.MISSING.value


def test_default_revision_prefers_main_over_alphabetical_first() -> None:
    """The backend must record the revision the agent will actually land on.

    ``build_cache_manifest`` sorts refs by filename, so taking ``refs[0]``
    recorded ``dev`` while the agent - which prefers ``main`` - synced ``main``.
    """
    refs = [{"name": "dev", "commit": "deadbeef"}, {"name": "main", "commit": "cafebabe"}]
    assert _default_revision(refs) == "cafebabe"
    assert _default_revision([{"name": "master", "commit": "abc"}]) == "abc"
    assert _default_revision([{"name": "only", "commit": "xyz"}]) == "xyz"
    assert _default_revision([]) is None
