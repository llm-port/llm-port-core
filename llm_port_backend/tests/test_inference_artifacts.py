"""Unit tests for services.inference.artifacts (WI-3)."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llm_port_backend.db.models.inference import (
    InferenceEnvironment,
    ModelAvailability,
    ModelAvailabilityStatus,
)
from llm_port_backend.db.models.node_control import InfraNode
from llm_port_backend.services.inference.artifacts import (
    ArtifactReadiness,
    ModelArtifactCoordinator,
    is_ready_for,
)


def test_is_ready_for_predicate() -> None:
    """Test readiness predicate validation."""
    assert not is_ready_for(None, "digest1")

    row_not_ready = SimpleNamespace(
        status=ModelAvailabilityStatus.SYNCING.value,
        root_path="/models/snap",
        manifest_sha256="digest1",
    )
    assert not is_ready_for(row_not_ready, "digest1")

    row_no_path = SimpleNamespace(
        status=ModelAvailabilityStatus.READY.value,
        root_path="",
        manifest_sha256="digest1",
    )
    assert not is_ready_for(row_no_path, "digest1")

    row_digest_mismatch = SimpleNamespace(
        status=ModelAvailabilityStatus.READY.value,
        root_path="/models/snap",
        manifest_sha256="digest_old",
    )
    assert not is_ready_for(row_digest_mismatch, "digest_new")

    row_ok = SimpleNamespace(
        status=ModelAvailabilityStatus.READY.value,
        root_path="/models/snap",
        manifest_sha256="digest1",
    )
    assert is_ready_for(row_ok, "digest1")
    assert is_ready_for(row_ok, None)  # Any digest accepted if desired is None


@pytest.mark.anyio
async def test_coordinator_eligible_nodes_filters() -> None:
    """eligible_nodes excludes draining, maintenance, and ineligible nodes."""
    session = AsyncMock()
    n1 = InfraNode(id=uuid.uuid4(), scheduler_eligible=True, maintenance_mode=False, draining=False, status="healthy")
    n2 = InfraNode(id=uuid.uuid4(), scheduler_eligible=False, maintenance_mode=False, draining=False, status="healthy")
    n3 = InfraNode(id=uuid.uuid4(), scheduler_eligible=True, maintenance_mode=True, draining=False, status="healthy")
    n4 = InfraNode(id=uuid.uuid4(), scheduler_eligible=True, maintenance_mode=False, draining=True, status="healthy")
    n5 = InfraNode(id=uuid.uuid4(), scheduler_eligible=True, maintenance_mode=False, draining=False, status="offline")

    fake_res = MagicMock()
    fake_res.scalars.return_value.all.return_value = [n1, n2, n3, n4, n5]
    session.execute.return_value = fake_res

    coordinator = ModelArtifactCoordinator(session)
    env = InferenceEnvironment(id=uuid.uuid4())
    eligible = await coordinator.eligible_nodes(env)

    assert eligible == [n1]


@pytest.mark.anyio
async def test_coordinator_evaluate_all_ready() -> None:
    """evaluate returns all_ready=True when all eligible nodes have matching READY rows."""
    session = AsyncMock()
    n1 = InfraNode(id=uuid.uuid4(), scheduler_eligible=True, maintenance_mode=False, draining=False, status="healthy")
    env = InferenceEnvironment(id=uuid.uuid4())
    model = SimpleNamespace(id=uuid.uuid4(), hf_repo_id="org/model", hf_revision="rev1")

    coordinator = ModelArtifactCoordinator(session)
    coordinator.eligible_nodes = AsyncMock(return_value=[n1])

    row = ModelAvailability(
        id=uuid.uuid4(),
        model_id=model.id,
        node_id=n1.id,
        status=ModelAvailabilityStatus.READY.value,
        root_path="/models/snapshots/rev1",
        manifest_sha256="digest123",
    )
    coordinator._dao.list_for_nodes = AsyncMock(return_value=[row])

    with patch("llm_port_backend.services.inference.artifacts.model_cache_dir", return_value=None):
        readiness = await coordinator.evaluate(model=model, environment=env)

    assert readiness.all_ready
    assert readiness.ready_node_ids == [str(n1.id)]
    assert readiness.pending_node_ids == []
    assert readiness.root_paths[str(n1.id)] == "/models/snapshots/rev1"


@pytest.mark.anyio
async def test_coordinator_evaluate_detects_missing_and_stale() -> None:
    """evaluate marks missing rows MISSING and outdated digests STALE."""
    session = AsyncMock()
    n1 = InfraNode(id=uuid.uuid4(), scheduler_eligible=True, maintenance_mode=False, draining=False, status="healthy")
    n2 = InfraNode(id=uuid.uuid4(), scheduler_eligible=True, maintenance_mode=False, draining=False, status="healthy")
    env = InferenceEnvironment(id=uuid.uuid4())
    model = SimpleNamespace(id=uuid.uuid4(), hf_repo_id="org/model", hf_revision="rev1")

    coordinator = ModelArtifactCoordinator(session)
    coordinator.eligible_nodes = AsyncMock(return_value=[n1, n2])

    # n1 has stale digest, n2 has no row
    row_stale = ModelAvailability(
        id=uuid.uuid4(),
        model_id=model.id,
        node_id=n1.id,
        status=ModelAvailabilityStatus.READY.value,
        root_path="/models/snapshots/old",
        manifest_sha256="digest_old",
    )
    coordinator._dao.list_for_nodes = AsyncMock(return_value=[row_stale])
    coordinator._dao.mark = AsyncMock()

    fake_manifest = {"manifest_sha256": "digest_new", "blobs": [{"hash": "b1"}]}
    with patch("llm_port_backend.services.inference.artifacts.model_cache_dir", return_value="/tmp/cache"):
        with patch("llm_port_backend.services.inference.artifacts.build_cache_manifest", return_value=fake_manifest):
            readiness = await coordinator.evaluate(model=model, environment=env)

    assert not readiness.all_ready
    assert str(n1.id) in readiness.pending_node_ids
    assert str(n2.id) in readiness.pending_node_ids

    # n2 marked MISSING, n1 marked STALE
    mark_calls = coordinator._dao.mark.call_args_list
    assert any(c[0][1] == n2.id and c[0][2] == ModelAvailabilityStatus.MISSING for c in mark_calls)
    assert any(c[0][1] == n1.id and c[0][2] == ModelAvailabilityStatus.STALE for c in mark_calls)


@pytest.mark.anyio
async def test_coordinator_ensure_issues_commands() -> None:
    """ensure dispatches SYNC_MODEL commands with deterministic idempotency keys."""
    session = AsyncMock()
    n1 = InfraNode(id=uuid.uuid4(), scheduler_eligible=True, maintenance_mode=False, draining=False, status="healthy")
    env = InferenceEnvironment(id=uuid.uuid4())
    model = SimpleNamespace(id=uuid.uuid4(), hf_repo_id="org/model", hf_revision="rev1")

    gateway_mock = AsyncMock()

    coordinator = ModelArtifactCoordinator(session)
    coordinator.evaluate = AsyncMock(
        return_value=ArtifactReadiness(
            model_id=str(model.id),
            manifest_sha256="digest123",
            desired_revision="rev1",
            ready_node_ids=[],
            pending_node_ids=[str(n1.id)],
            failed_node_ids=[],
            blockers=[],
            all_ready=False,
        )
    )
    coordinator._dao.mark = AsyncMock()
    # ensure() re-reads the rows so an in-flight sync can be left alone; with
    # no rows, every target node is dispatched exactly as before.
    coordinator._dao.list_for_nodes = AsyncMock(return_value=[])

    fake_payload = {"model_id": str(model.id), "blobs": [{"hash": "b1"}]}
    with patch("llm_port_backend.services.inference.artifacts.build_model_sync_payload", return_value=fake_payload):
        res = await coordinator.ensure(model=model, environment=env, gateway=gateway_mock)

    assert not res.all_ready
    gateway_mock.issue_command.assert_called_once()
    call_kwargs = gateway_mock.issue_command.call_args[1]
    assert call_kwargs["node_id"] == n1.id
    assert call_kwargs["command_type"] == "sync_model"
    assert call_kwargs["idempotency_key"] == f"artifact:{model.id}:digest123:{n1.id}"
    assert call_kwargs["payload"] == {"model_sync": fake_payload}

