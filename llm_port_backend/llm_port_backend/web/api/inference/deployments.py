"""Inference deployment CRUD endpoints.

Phase 1: desired-state only.  Create/update validate the versioned spec
(``inference.llmport.ai/v1alpha1``) and persist it.  The reconcile endpoint is
an honest stub; no replica is scheduled or contacted.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from starlette import status

from llm_port_backend.db.models.users import User
from llm_port_backend.services.inference.service import (
    DeploymentService,
    InferenceError,
)
from llm_port_backend.web.api.inference.control_planes import _map_inference_error
from llm_port_backend.web.api.inference.schema import (
    DeploymentCreate,
    DeploymentDTO,
    DeploymentUpdate,
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
    """Drive a deployment toward its desired state.

    Phase 1: no live actions — records a no-op observation at the current
    generation and returns the deployment.
    """
    try:
        dep = await service.reconcile(deployment_id)
    except InferenceError as exc:
        raise _map_inference_error(exc)
    return _dto_from_dep(dep)


def _dto_from_dep(dep) -> DeploymentDTO:
    return DeploymentDTO.model_validate(dep)
