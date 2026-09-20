"""Inference deployment CRUD endpoints.

Phase 1: desired-state only.  Create/update validate the versioned spec
(``inference.llmport.ai/v1alpha1``) and persist it.  The reconcile endpoint is
an honest stub; no replica is scheduled or contacted.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query
from starlette import status

from llm_port_backend.db.models.inference import InferenceEnvironment
from llm_port_backend.db.models.users import User
from llm_port_backend.services.inference.observability import (
    DeploymentMetrics,
    LogPage,
    LogSource,
    ObservabilityUnsupported,
)
from llm_port_backend.services.inference.service import (
    DeploymentService,
    InferenceError,
)
from llm_port_backend.services.nodes.service import NodeControlService
from llm_port_backend.web.api.inference.observability import (
    get_node_control_service,
    resolve_driver_for_environment,
    unsupported,
)
from llm_port_backend.web.api.inference.control_planes import _map_inference_error
from llm_port_backend.web.api.inference.schema import (
    DeploymentCreate,
    DeploymentDTO,
    DeploymentUpdate,
    EndpointDTO,
)
from llm_port_backend.web.api.rbac import require_permission

router = APIRouter()
_DEP = "inference.deployments"


@router.get("", response_model=list[DeploymentDTO])
@router.get("/", response_model=list[DeploymentDTO], include_in_schema=False)
async def list_deployments(
    environment_id: uuid.UUID | None = None,
    model_id: uuid.UUID | None = None,
    _user: User = Depends(require_permission(_DEP, "read")),
    service: DeploymentService = Depends(),
) -> list[DeploymentDTO]:
    """List inference deployments, optionally filtered by environment or model."""
    return [_dto_from_dep(d) for d in await service.list(environment_id, model_id)]


@router.post("", response_model=DeploymentDTO, status_code=status.HTTP_201_CREATED)
@router.post("/", response_model=DeploymentDTO, status_code=status.HTTP_201_CREATED, include_in_schema=False)
async def create_deployment(
    body: DeploymentCreate,
    _user: User = Depends(require_permission(_DEP, "create")),
    service: DeploymentService = Depends(),
) -> DeploymentDTO:
    """Create an inference deployment from a validated versioned spec."""
    try:
        dep = await service.create(
            environment_id=body.environment_id,
            model_id=body.model_id,
            name=body.name,
            spec=body.spec,
            description=body.description,
        )
    except InferenceError as exc:
        raise _map_inference_error(exc)
    return _dto_from_dep(dep)


@router.get("/{deployment_id}", response_model=DeploymentDTO)
async def get_deployment(
    deployment_id: uuid.UUID,
    _user: User = Depends(require_permission(_DEP, "read")),
    service: DeploymentService = Depends(),
) -> DeploymentDTO:
    """Get a single inference deployment by ID."""
    try:
        dep = await service.get(deployment_id)
    except InferenceError as exc:
        raise _map_inference_error(exc)
    return _dto_from_dep(dep)


@router.patch("/{deployment_id}", response_model=DeploymentDTO)
async def update_deployment(
    deployment_id: uuid.UUID,
    body: DeploymentUpdate,
    _user: User = Depends(require_permission(_DEP, "update")),
    service: DeploymentService = Depends(),
) -> DeploymentDTO:
    """Patch a deployment. Omitted fields are left unchanged; a new ``spec`` is a full replacement."""
    kwargs = body.model_dump(exclude_unset=True)
    try:
        dep = await service.update(deployment_id, **kwargs)
    except InferenceError as exc:
        raise _map_inference_error(exc)
    return _dto_from_dep(dep)


@router.delete("/{deployment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_deployment(
    deployment_id: uuid.UUID,
    _user: User = Depends(require_permission(_DEP, "delete")),
    service: DeploymentService = Depends(),
) -> None:
    """Delete a deployment (endpoints are cascade-deleted by the DB)."""
    try:
        await service.delete(deployment_id)
    except InferenceError as exc:
        raise _map_inference_error(exc)


@router.post("/{deployment_id}/reconcile", response_model=DeploymentDTO)
async def reconcile_deployment(
    deployment_id: uuid.UUID,
    _user: User = Depends(require_permission(_DEP, "operate")),
    service: DeploymentService = Depends(),
) -> DeploymentDTO:
    """Request a reconcile: queue the deployment for the background reconciler.

    Returns immediately with the current deployment; the next reconciler pass
    re-applies a ``failed`` deployment or re-observes a running one.
    """
    try:
        dep = await service.request_reconcile(deployment_id)
    except InferenceError as exc:
        raise _map_inference_error(exc)
    return _dto_from_dep(dep)


@router.get("/{deployment_id}/endpoints", response_model=list[EndpointDTO])
async def list_deployment_endpoints(
    deployment_id: uuid.UUID,
    _user: User = Depends(require_permission(_DEP, "read")),
    service: DeploymentService = Depends(),
) -> list[EndpointDTO]:
    """Return the active published endpoints for a deployment."""
    try:
        endpoints = await service.endpoints(deployment_id)
    except InferenceError as exc:
        raise _map_inference_error(exc)
    return [EndpointDTO.model_validate(e) for e in endpoints]


def _dto_from_dep(dep) -> DeploymentDTO:
    return DeploymentDTO.model_validate(dep)


# ---------------------------------------------------------------------------
# Observability (Phase 6)
# ---------------------------------------------------------------------------


@router.get("/{deployment_id}/logs", response_model=LogPage)
async def get_deployment_logs(
    deployment_id: uuid.UUID,
    source: LogSource = Query(
        LogSource.RUNTIME_CONTAINER,
        description="Which process to read: the runtime container or a Serve replica.",
    ),
    node_id: uuid.UUID | None = Query(None, description="Read from this member node instead of the head."),
    replica_id: str | None = Query(None, description="Narrow a serve_replica read to one replica."),
    tail: int = Query(200, ge=1, le=5000, description="How many trailing lines to return."),
    since: str | None = Query(None, description="Only lines at or after this timestamp."),
    _user: User = Depends(require_permission(_DEP, "read")),
    service: DeploymentService = Depends(),
    node_control: NodeControlService = Depends(get_node_control_service),
) -> LogPage:
    """Read a page of normalized logs for a deployment.

    The shape is driver-neutral by contract: nothing backend-specific reaches
    this response (Phase 6, "Logs").
    """
    try:
        deployment = await service.get(deployment_id)
    except InferenceError as exc:
        raise _map_inference_error(exc)

    environment = await service.session.get(InferenceEnvironment, deployment.environment_id)
    if environment is None:
        raise _map_inference_error(InferenceError("environment not found"))
    driver = await resolve_driver_for_environment(service.session, environment)

    try:
        return await driver.logs(
            service.session,
            deployment,
            source=source,
            node_id=str(node_id) if node_id else None,
            replica_id=replica_id,
            tail=tail,
            since=since,
            node_control=node_control,
        )
    except ObservabilityUnsupported as exc:
        raise unsupported(exc)


@router.get("/{deployment_id}/metrics", response_model=DeploymentMetrics)
async def get_deployment_metrics(
    deployment_id: uuid.UUID,
    _user: User = Depends(require_permission(_DEP, "read")),
    service: DeploymentService = Depends(),
    node_control: NodeControlService = Depends(get_node_control_service),
) -> DeploymentMetrics:
    """Aggregated Serve-application and replica metrics for a deployment.

    A tier that cannot be reported comes back in ``partials`` with a reason,
    not as a zero.
    """
    try:
        deployment = await service.get(deployment_id)
    except InferenceError as exc:
        raise _map_inference_error(exc)

    environment = await service.session.get(InferenceEnvironment, deployment.environment_id)
    if environment is None:
        raise _map_inference_error(InferenceError("environment not found"))
    driver = await resolve_driver_for_environment(service.session, environment)

    try:
        return await driver.deployment_metrics(
            service.session, deployment, node_control=node_control
        )
    except ObservabilityUnsupported as exc:
        raise unsupported(exc)
