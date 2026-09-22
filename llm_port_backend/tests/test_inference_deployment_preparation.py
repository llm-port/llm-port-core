"""Tests for deployment artifact preparation gate, phase progression, and path translation (WI-5)."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.dao.inference_dao import ModelAvailabilityDAO
from llm_port_backend.db.models.inference import (
    DeploymentDesiredState,
    DeploymentPhase,
    EnvironmentStatus,
    InferenceControlPlane,
    InferenceDeployment,
    InferenceEnvironment,
    InferenceEnvironmentNode,
    ModelAvailability,
    ModelAvailabilityStatus,
)
from llm_port_backend.db.models.llm import LLMModel, ModelSource, ModelStatus
from llm_port_backend.db.models.node_control import (
    InfraNode,
    InfraNodeCommand,
    NodeCommandStatus,
    NodeCommandType,
)
from llm_port_backend.services.inference.drivers.ray.deployment import RayDeploymentManager
from llm_port_backend.services.inference.drivers.ray.client import RayCommandError
from tests.platform_fixtures import DGX_SPARK_PLATFORM


class _FakeNodeControlService:
    def __init__(self, *, serve_status: dict[str, Any] | None = None) -> None:
        self.issued: list[dict[str, Any]] = []
        self.commands: dict[uuid.UUID, InfraNodeCommand] = {}
        self._serve_status = serve_status

    async def issue_command(
        self,
        *,
        node_id: uuid.UUID,
        command_type: str,
        payload: dict[str, Any] | None = None,
        idempotency_key: str,
        correlation_id: str | None = None,
        timeout_sec: int | None = None,
        issued_by: uuid.UUID | None = None,
    ) -> InfraNodeCommand:
        cid = uuid.uuid4()
        cmd = InfraNodeCommand(
            id=cid,
            node_id=node_id,
            command_type=command_type,
            payload_json=payload or {},
            idempotency_key=idempotency_key,
            correlation_id=correlation_id,
            timeout_sec=timeout_sec,
            status=NodeCommandStatus.SUCCEEDED.value,
        )
        self.issued.append({
            "command_id": cid,
            "node_id": node_id,
            "command_type": command_type,
            "payload": payload,
            "idempotency_key": idempotency_key,
        })
        self.commands[cid] = cmd
        return cmd

    async def get_command(self, *, command_id: uuid.UUID) -> InfraNodeCommand | None:
        return self.commands.get(command_id)


def _spec() -> dict[str, Any]:
    return {
        "api_version": "inference.llmport.ai/v1alpha1",
        "engine": {"name": "vllm", "config": {}},
        "scale": {"replicas": 1},
        "resources": {"replica": {"gpus": 1.0}},
        "service": {"path": "/v1"},
    }


def _make_serve_status(app_name: str, *, status: str = "RUNNING", ready: int = 1) -> dict[str, Any]:
    return {
        "alive": True,
        "serve": {
            "available": True,
            "active": status == "RUNNING",
            "apps": {
                app_name: {
                    "name": app_name,
                    "status": status,
                    "deployments": {
                        "OpenAiIngress": {
                            "name": "OpenAiIngress",
                            "status": "HEALTHY",
                            "num_replicas_ready": 1,
                            "num_replicas_pending": 0,
                        },
                        "LLMServer": {
                            "name": "LLMServer",
                            "status": "HEALTHY",
                            "num_replicas_ready": ready,
                            "num_replicas_pending": 0,
                        },
                    },
                }
            },
        },
    }


@pytest.mark.anyio
async def test_deployment_phase_progression_pending_preparing_applying_running(
    dbsession: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify full phase progression: PENDING -> PREPARING -> APPLYING -> RUNNING."""
    cp = InferenceControlPlane(name=f"cp-{uuid.uuid4().hex[:8]}", driver="ray")
    dbsession.add(cp)
    await dbsession.flush()

    node = InfraNode(
        agent_id=f"head-{uuid.uuid4().hex[:8]}", host="10.0.0.1", status="healthy",
        capabilities_json=dict(DGX_SPARK_PLATFORM),
    )
    dbsession.add(node)
    await dbsession.flush()

    env = InferenceEnvironment(
        control_plane_id=cp.id,
        name=f"env-{uuid.uuid4().hex[:8]}",
        head_node_id=node.id,
        status=EnvironmentStatus.READY.value,
        config_json={
            "runtime_bundle_id": "bundle-dgx-spark-gb10-v1",
            "artifacts": {"offline_only": False},
        },
    )
    dbsession.add(env)
    await dbsession.flush()
    dbsession.add(InferenceEnvironmentNode(environment_id=env.id, node_id=node.id, role="head"))

    model = LLMModel(
        display_name="org/test-model",
        source=ModelSource.HUGGINGFACE,
        status=ModelStatus.AVAILABLE,
        hf_repo_id="org/test-model",
        hf_revision="main",
    )
    dbsession.add(model)
    await dbsession.flush()

    dep = InferenceDeployment(
        environment_id=env.id,
        model_id=model.id,
        name=f"dep-{uuid.uuid4().hex[:8]}",
        spec_json=_spec(),
        phase=DeploymentPhase.PENDING.value,
    )
    dbsession.add(dep)
    await dbsession.commit()

    manager = RayDeploymentManager()
    fake_control = _FakeNodeControlService()

    # Pass 1: Artifacts not ready yet (no row).
    # Reconciler should discover unready artifacts, issue ensure(), and enter PREPARING.
    await manager.reconcile_deployment(dbsession, dep, node_control=fake_control)
    assert dep.phase == DeploymentPhase.PREPARING.value
    assert dep.observed_generation == 0  # mark_observed=False
    obs = dep.observed_status_json.get("observation", {})
    assert obs.get("reason") == "preparing_artifacts"
    assert obs.get("remote_fallback_available") is True

    # Pass 2: Mark artifacts as READY with valid bundle mount path
    dao = ModelAvailabilityDAO(dbsession)
    await dao.mark(
        model_id=model.id,
        node_id=node.id,
        status=ModelAvailabilityStatus.READY,
        root_path="/srv/llm-port/models/models--org--test-model/snapshots/commit123",
        revision="commit123",
        manifest_sha256="sha256:abc",
        size_bytes=1000,
    )
    await dbsession.commit()

    # In Pass 2, Serve app is run, but probe returns empty/waiting -> transitions to APPLYING
    async def _mock_probe_empty(*args: Any, **kwargs: Any) -> Any:
        return None

    monkeypatch.setattr(manager, "_probe_serve", _mock_probe_empty)
    monkeypatch.setattr(
        "llm_port_backend.services.inference.drivers.ray.deployment._READINESS_PASS_BUDGET_SEC",
        0.05,
    )
    monkeypatch.setattr(
        "llm_port_backend.services.inference.drivers.ray.deployment._READINESS_POLL_SEC",
        0.01,
    )

    await manager.reconcile_deployment(dbsession, dep, node_control=fake_control)
    assert dep.phase == DeploymentPhase.APPLYING.value
    assert dep.observed_generation == 0  # mark_observed=False

    # Verify that the RUN_SERVE_APP command received the translated container path!
    run_serve_cmds = [c for c in fake_control.issued if c["command_type"] == NodeCommandType.RUN_SERVE_APP.value]
    assert len(run_serve_cmds) == 1
    llm_args = run_serve_cmds[0]["payload"]["llm_serving_args"]
    # Host /srv/llm-port/models maps to container /models
    assert (
        llm_args["llm_configs"][0]["model_loading_config"]["model_source"]
        == "/models/models--org--test-model/snapshots/commit123"
    )

    # Pass 3: Serve status probe returns RUNNING with ready replica -> transitions to RUNNING
    app_name = f"llmport-{dep.id}"

    class _MockServeResponse:
        alive = True

    async def _mock_probe_running(*args: Any, **kwargs: Any) -> Any:
        from llm_port_backend.services.inference.drivers.ray.client import _parse_serve_status
        data = _make_serve_status(app_name, status="RUNNING", ready=1)
        return _parse_serve_status(data)

    monkeypatch.setattr(manager, "_probe_serve", _mock_probe_running)

    await manager.reconcile_deployment(dbsession, dep, node_control=fake_control)
    assert dep.phase == DeploymentPhase.RUNNING.value
    assert dep.ready_replicas == 1
    assert dep.observed_generation == dep.generation


@pytest.mark.anyio
async def test_a_failed_sync_blocks_the_deploy_without_offline_only(
    dbsession: AsyncSession,
) -> None:
    """The DGX regression: readiness knew, and we deployed anyway.

    ``offline_only`` defaulted to False, which the gate read as "a remote
    fetch is available" and used to skip straight to ``serve.run``.  It is not
    available: the Phase 4B runtime bundle is air-gapped by construction and
    the certified image carries ``HF_HUB_OFFLINE=1``.

    So the deploy went ahead with nothing to load, and four minutes later Ray
    reported

        Failed to create vLLM engine config: Cannot find an appropriate
        cached snapshot folder for the specified revision

    while artifact readiness had been holding the real reason the whole time.
    """
    cp = InferenceControlPlane(name=f"cp-{uuid.uuid4().hex[:8]}", driver="ray")
    dbsession.add(cp)
    await dbsession.flush()

    node = InfraNode(
        agent_id=f"head-{uuid.uuid4().hex[:8]}", host="10.0.0.2", status="healthy",
        capabilities_json=dict(DGX_SPARK_PLATFORM),
    )
    dbsession.add(node)
    await dbsession.flush()

    # Note: no "artifacts" config at all -- this is the default environment,
    # which is exactly the one that used to slip through.
    env = InferenceEnvironment(
        control_plane_id=cp.id,
        name=f"env-{uuid.uuid4().hex[:8]}",
        head_node_id=node.id,
        status=EnvironmentStatus.READY.value,
        config_json={"runtime_bundle_id": "bundle-dgx-spark-gb10-v1"},
    )
    dbsession.add(env)
    await dbsession.flush()
    dbsession.add(InferenceEnvironmentNode(environment_id=env.id, node_id=node.id, role="head"))

    model = LLMModel(
        display_name="org/never-synced",
        source=ModelSource.HUGGINGFACE,
        status=ModelStatus.AVAILABLE,
        hf_repo_id="org/never-synced",
        hf_revision="main",
    )
    dbsession.add(model)
    await dbsession.flush()

    dep = InferenceDeployment(
        environment_id=env.id,
        model_id=model.id,
        name=f"dep-{uuid.uuid4().hex[:8]}",
        spec_json=_spec(),
        phase=DeploymentPhase.PENDING.value,
    )
    dbsession.add(dep)
    await dbsession.commit()

    # A node whose sync was attempted and failed -- the real condition, as
    # opposed to a model nobody has tried to place yet.
    dbsession.add(
        ModelAvailability(
            model_id=model.id,
            node_id=node.id,
            status=ModelAvailabilityStatus.FAILED.value,
            status_message="model_sync payload with files is required.",
        )
    )
    await dbsession.commit()

    manager = RayDeploymentManager()
    fake_control = _FakeNodeControlService()
    await manager.reconcile_deployment(dbsession, dep, node_control=fake_control)

    assert dep.phase == DeploymentPhase.FAILED.value
    # Nothing was handed to Ray, so nothing can fail obscurely inside it.
    assert not any(
        c["command_type"] == NodeCommandType.RUN_SERVE_APP.value for c in fake_control.issued
    )
    # And the operator is told what is actually wrong.
    message = dep.phase_message or ""
    assert "cannot download it" in message
    assert "model_sync payload with files is required" in message


@pytest.mark.anyio
async def test_deployment_offline_only_blocks_on_missing_artifact(
    dbsession: AsyncSession,
) -> None:
    """In offline_only mode, missing artifact/manifest must transition to FAILED with reason."""
    cp = InferenceControlPlane(name=f"cp-{uuid.uuid4().hex[:8]}", driver="ray")
    dbsession.add(cp)
    await dbsession.flush()

    node = InfraNode(
        agent_id=f"head-{uuid.uuid4().hex[:8]}", host="10.0.0.2", status="healthy",
        capabilities_json=dict(DGX_SPARK_PLATFORM),
    )
    dbsession.add(node)
    await dbsession.flush()

    # Environment configured as offline_only
    env = InferenceEnvironment(
        control_plane_id=cp.id,
        name=f"env-{uuid.uuid4().hex[:8]}",
        head_node_id=node.id,
        status=EnvironmentStatus.READY.value,
        config_json={
            "runtime_bundle_id": "bundle-dgx-spark-gb10-v1",
            "artifacts": {"offline_only": True},
        },
    )
    dbsession.add(env)
    await dbsession.flush()
    dbsession.add(InferenceEnvironmentNode(environment_id=env.id, node_id=node.id, role="head"))

    # Model has repo_id that does not exist in backend local cache
    model = LLMModel(
        display_name="org/non-existent-local-model",
        source=ModelSource.HUGGINGFACE,
        status=ModelStatus.AVAILABLE,
        hf_repo_id="org/non-existent-local-model",
        hf_revision="main",
    )
    dbsession.add(model)
    await dbsession.flush()

    dep = InferenceDeployment(
        environment_id=env.id,
        model_id=model.id,
        name=f"dep-{uuid.uuid4().hex[:8]}",
        spec_json=_spec(),
        phase=DeploymentPhase.PENDING.value,
    )
    dbsession.add(dep)
    await dbsession.commit()

    manager = RayDeploymentManager()
    fake_control = _FakeNodeControlService()

    await manager.reconcile_deployment(dbsession, dep, node_control=fake_control)

    # Must be marked FAILED immediately with blocker explanation
    assert dep.phase == DeploymentPhase.FAILED.value
    assert dep.observed_generation == dep.generation  # terminal failure is observed
    # The message is written for the operator, not from the config flag that
    # produced it: "offline-only mode" told them nothing they could act on.
    message = dep.phase_message or ""
    assert "cannot download it" in message
    # ...and it still names the underlying blocker.
    assert "org/non-existent-local-model" in message
    # No RUN_SERVE_APP must have been issued
    assert not any(c["command_type"] == NodeCommandType.RUN_SERVE_APP.value for c in fake_control.issued)


@pytest.mark.anyio
async def test_deployment_offline_only_blocks_on_unmapped_mount(
    dbsession: AsyncSession,
) -> None:
    """Host root path that does not fall under any bundle container mount must fail in offline_only."""
    cp = InferenceControlPlane(name=f"cp-{uuid.uuid4().hex[:8]}", driver="ray")
    dbsession.add(cp)
    await dbsession.flush()

    node = InfraNode(
        agent_id=f"head-{uuid.uuid4().hex[:8]}", host="10.0.0.3", status="healthy",
        capabilities_json=dict(DGX_SPARK_PLATFORM),
    )
    dbsession.add(node)
    await dbsession.flush()

    env = InferenceEnvironment(
        control_plane_id=cp.id,
        name=f"env-{uuid.uuid4().hex[:8]}",
        head_node_id=node.id,
        status=EnvironmentStatus.READY.value,
        config_json={
            "runtime_bundle_id": "bundle-dgx-spark-gb10-v1",
            "artifacts": {"offline_only": True},
        },
    )
    dbsession.add(env)
    await dbsession.flush()
    dbsession.add(InferenceEnvironmentNode(environment_id=env.id, node_id=node.id, role="head"))

    model = LLMModel(
        display_name="org/unmapped-model",
        source=ModelSource.HUGGINGFACE,
        status=ModelStatus.AVAILABLE,
        hf_repo_id="org/unmapped-model",
        hf_revision="main",
    )
    dbsession.add(model)
    await dbsession.flush()

    # Seed availability with path not under /srv/llm-port/models
    dao = ModelAvailabilityDAO(dbsession)
    await dao.mark(
        model_id=model.id,
        node_id=node.id,
        status=ModelAvailabilityStatus.READY,
        root_path="/unmapped/storage/path/commit",
        revision="commit",
        manifest_sha256="sha256:123",
        size_bytes=500,
    )

    dep = InferenceDeployment(
        environment_id=env.id,
        model_id=model.id,
        name=f"dep-{uuid.uuid4().hex[:8]}",
        spec_json=_spec(),
        phase=DeploymentPhase.PENDING.value,
    )
    dbsession.add(dep)
    await dbsession.commit()

    manager = RayDeploymentManager()
    fake_control = _FakeNodeControlService()

    await manager.reconcile_deployment(dbsession, dep, node_control=fake_control)

    assert dep.phase == DeploymentPhase.FAILED.value
    assert "mount" in (dep.phase_message or "").lower()

