"""Unit tests for generic InferencePublicationCoordinator (Phase 5)."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock
import pytest

from llm_port_backend.db.models.inference import (
    DeploymentDesiredState,
    DeploymentPhase,
    EndpointStatus,
    InferenceDeployment,
    InferenceEndpoint,
)
from llm_port_backend.services.inference.publication import InferencePublicationCoordinator


@pytest.fixture
def mock_session():
    return AsyncMock()


@pytest.fixture
def mock_gateway_sync():
    sync = MagicMock()
    sync.enabled = True
    sync.publish_inference_endpoint = AsyncMock()
    sync.deactivate_source = AsyncMock()
    sync.reactivate_source = AsyncMock()
    sync.retire_source = AsyncMock()
    sync.set_source_health = AsyncMock()
    return sync


@pytest.mark.anyio
async def test_reconcile_publication_published_endpoint(mock_session, mock_gateway_sync):
    """A RUNNING deployment with a PUBLISHED endpoint is published to the gateway as healthy."""
    dep_id = uuid.uuid4()
    deployment = InferenceDeployment(
        id=dep_id,
        name="meta-llama-3-8b",
        desired_state=DeploymentDesiredState.ACTIVE,
        phase=DeploymentPhase.RUNNING,
        ready_replicas=2,
        total_replicas=2,
        spec_json={"service": {"alias": "llama3", "path": "/v1"}},
    )
    endpoint = InferenceEndpoint(
        id=uuid.uuid4(),
        deployment_id=dep_id,
        name="openai",
        address="http://10.88.10.49:8000/llmport-dep1",
        path="/v1",
        status=EndpointStatus.PUBLISHED,
        published_json={
            "model": "meta-llama/Meta-Llama-3-8B-Instruct",
            "base_url": "http://10.88.10.49:8000/llmport-dep1/v1",
        },
    )

    coordinator = InferencePublicationCoordinator(mock_session, gateway_sync=mock_gateway_sync)
    coordinator.endpoint_dao.list_for_deployment = AsyncMock(return_value=[endpoint])

    await coordinator.reconcile_deployment_publication(deployment)

    mock_gateway_sync.publish_inference_endpoint.assert_awaited_once_with(
        deployment_id=dep_id,
        endpoint_id=endpoint.id,
        base_url="http://10.88.10.49:8000/llmport-dep1/v1",
        alias="llama3",
        served_model_name="meta-llama/Meta-Llama-3-8B-Instruct",
        backend_provider_type="vllm",
        health_status="healthy",
        is_routable=True,
    )


@pytest.mark.anyio
async def test_reconcile_publication_degraded_deployment_with_ready_replica(mock_session, mock_gateway_sync):
    """A DEGRADED deployment with ready_replicas > 0 remains routable on the gateway."""
    dep_id = uuid.uuid4()
    deployment = InferenceDeployment(
        id=dep_id,
        name="qwen-coder",
        desired_state=DeploymentDesiredState.ACTIVE,
        phase=DeploymentPhase.DEGRADED,
        ready_replicas=1,  # 1 of 2 replicas ready
        total_replicas=2,
        spec_json={},
    )
    endpoint = InferenceEndpoint(
        id=uuid.uuid4(),
        deployment_id=dep_id,
        name="openai",
        address="http://10.88.10.49:8000/llmport-dep2",
        path="/v1",
        status=EndpointStatus.PUBLISHED,
        published_json={"model": "Qwen/Qwen2.5-Coder-1.5B-Instruct"},
    )

    coordinator = InferencePublicationCoordinator(mock_session, gateway_sync=mock_gateway_sync)
    coordinator.endpoint_dao.list_for_deployment = AsyncMock(return_value=[endpoint])

    await coordinator.reconcile_deployment_publication(deployment)

    # Must be published as healthy and routable
    mock_gateway_sync.publish_inference_endpoint.assert_awaited_once_with(
        deployment_id=dep_id,
        endpoint_id=endpoint.id,
        base_url="http://10.88.10.49:8000/llmport-dep2/v1",
        alias="qwen-coder",
        served_model_name="Qwen/Qwen2.5-Coder-1.5B-Instruct",
        backend_provider_type="vllm",
        health_status="healthy",
        is_routable=True,
    )


@pytest.mark.anyio
async def test_reconcile_publication_stopped_deployment(mock_session, mock_gateway_sync):
    """Stopping a deployment soft-deactivates the source without destroying alias/membership."""
    dep_id = uuid.uuid4()
    deployment = InferenceDeployment(
        id=dep_id,
        name="meta-llama-3-8b",
        desired_state=DeploymentDesiredState.STOPPED,
        phase=DeploymentPhase.STOPPED,
        ready_replicas=0,
    )

    coordinator = InferencePublicationCoordinator(mock_session, gateway_sync=mock_gateway_sync)
    await coordinator.reconcile_deployment_publication(deployment)

    mock_gateway_sync.deactivate_source.assert_awaited_once_with(
        source_kind="inference_deployment",
        source_id=dep_id,
    )
    mock_gateway_sync.publish_inference_endpoint.assert_not_awaited()


@pytest.mark.anyio
async def test_reconcile_publication_deleted_deployment(mock_session, mock_gateway_sync):
    """Deleting a deployment marks the source retired."""
    dep_id = uuid.uuid4()
    deployment = InferenceDeployment(
        id=dep_id,
        name="meta-llama-3-8b",
        desired_state=DeploymentDesiredState.DELETED,
        phase=DeploymentPhase.DELETED,
        ready_replicas=0,
    )

    coordinator = InferencePublicationCoordinator(mock_session, gateway_sync=mock_gateway_sync)
    await coordinator.reconcile_deployment_publication(deployment)

    mock_gateway_sync.retire_source.assert_awaited_once_with(
        source_kind="inference_deployment",
        source_id=dep_id,
    )


@pytest.mark.anyio
async def test_reconcile_publication_unhealthy_endpoint_zero_replicas(mock_session, mock_gateway_sync):
    """Endpoint with 0 ready replicas is marked unhealthy and disabled."""
    dep_id = uuid.uuid4()
    deployment = InferenceDeployment(
        id=dep_id,
        name="meta-llama-3-8b",
        desired_state=DeploymentDesiredState.ACTIVE,
        phase=DeploymentPhase.FAILED,
        ready_replicas=0,
        total_replicas=1,
    )
    endpoint = InferenceEndpoint(
        id=uuid.uuid4(),
        deployment_id=dep_id,
        name="openai",
        address="http://10.88.10.49:8000/app",
        status=EndpointStatus.FAILED,
    )

    coordinator = InferencePublicationCoordinator(mock_session, gateway_sync=mock_gateway_sync)
    coordinator.endpoint_dao.list_for_deployment = AsyncMock(return_value=[endpoint])

    await coordinator.reconcile_deployment_publication(deployment)

    mock_gateway_sync.set_source_health.assert_awaited_once_with(
        source_kind="inference_deployment",
        source_id=dep_id,
        health_status="unhealthy",
        enabled=False,
    )

