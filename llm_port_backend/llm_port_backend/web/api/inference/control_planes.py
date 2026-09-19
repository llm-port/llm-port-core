"""Inference control-plane CRUD endpoints.

Desired-state CRUD.  The reconcile endpoint queues the control plane's
environments for the background reconciler; it contacts no node itself.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from starlette import status

from llm_port_backend.db.models.users import User
from llm_port_backend.services.inference.service import (
    ConflictError,
    ControlPlaneService,
    InferenceError,
    NotFoundError,
)
from llm_port_backend.web.api.inference.schema import (
    ControlPlaneCreate,
    ControlPlaneDTO,
    ControlPlaneUpdate,
)
from llm_port_backend.web.api.rbac import require_permission

router = APIRouter()
_CP = "inference.control_planes"


def _map_inference_error(exc: InferenceError) -> HTTPException:
    """Translate service-level domain errors to HTTP."""
    if isinstance(exc, NotFoundError):
        return HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, ConflictError):
        return HTTPException(status.HTTP_409_CONFLICT, detail=exc.detail)
    return HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.get("", response_model=list[ControlPlaneDTO])
@router.get("/", response_model=list[ControlPlaneDTO], include_in_schema=False)
async def list_control_planes(
    _user: User = Depends(require_permission(_CP, "read")),
    service: ControlPlaneService = Depends(),
) -> list[ControlPlaneDTO]:
    """List all inference control planes."""
    return [_dto_from_cp(cp) for cp in await service.list()]


@router.get("/{control_plane_id}", response_model=ControlPlaneDTO, summary="Get a control plane")
async def get_control_plane(
    control_plane_id: uuid.UUID,
    _user: User = Depends(require_permission(_CP, "read")),
    service: ControlPlaneService = Depends(),
) -> ControlPlaneDTO:
    """Fetch a single control plane."""
    try:
        cp = await service.get(control_plane_id)
    except InferenceError as exc:
        raise _map_inference_error(exc)
    return _dto_from_cp(cp)


@router.post("", response_model=ControlPlaneDTO, status_code=status.HTTP_201_CREATED)
@router.post("/", response_model=ControlPlaneDTO, status_code=status.HTTP_201_CREATED, include_in_schema=False)
async def create_control_plane(
    body: ControlPlaneCreate,
    _user: User = Depends(require_permission(_CP, "create")),
    service: ControlPlaneService = Depends(),
) -> ControlPlaneDTO:
    """Register an inference control plane (driver identity + config)."""
    try:
        cp = await service.create(
            name=body.name,
            driver=body.driver,
            description=body.description,
            config=body.config,
            credential_ref=body.credential_ref,
            enabled=body.enabled,
        )
    except InferenceError as exc:
        raise _map_inference_error(exc)
    return _dto_from_cp(cp)


@router.patch("/{control_plane_id}", response_model=ControlPlaneDTO)
async def update_control_plane(
    control_plane_id: uuid.UUID,
    body: ControlPlaneUpdate,
    _user: User = Depends(require_permission(_CP, "update")),
    service: ControlPlaneService = Depends(),
) -> ControlPlaneDTO:
    """Patch control-plane fields. Omitted fields are left unchanged."""
    kwargs = body.model_dump(exclude_unset=True)
    try:
        cp = await service.update(control_plane_id, **kwargs)
    except InferenceError as exc:
        raise _map_inference_error(exc)
    return _dto_from_cp(cp)


@router.delete("/{control_plane_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_control_plane(
    control_plane_id: uuid.UUID,
    _user: User = Depends(require_permission(_CP, "delete")),
    service: ControlPlaneService = Depends(),
) -> None:
    """Delete a control plane. Refused while environments exist."""
    try:
        await service.delete(control_plane_id)
    except InferenceError as exc:
        raise _map_inference_error(exc)


@router.post("/{control_plane_id}/reconcile", response_model=ControlPlaneDTO)
async def reconcile_control_plane(
    control_plane_id: uuid.UUID,
    _user: User = Depends(require_permission(_CP, "operate")),
    service: ControlPlaneService = Depends(),
) -> ControlPlaneDTO:
    """Request a reconcile of every environment bound to the control plane.

    Returns immediately with the control plane; its environments are
    reconciled by the next background reconciler pass.
    """
    try:
        cp = await service.request_reconcile(control_plane_id)
    except InferenceError as exc:
        raise _map_inference_error(exc)
    return _dto_from_cp(cp)


def _dto_from_cp(cp) -> ControlPlaneDTO:
    return ControlPlaneDTO.model_validate(cp)
