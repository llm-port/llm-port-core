"""Inference environment CRUD endpoints.

Phase 1: desired-state only.  The reconcile endpoint is an honest stub; the
``add-node``/``remove-node`` sub-routes register desired membership only — no
node is contacted.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request
from starlette import status

from llm_port_backend.db.models.users import User
from llm_port_backend.services.inference.planner import InferenceEnvironmentPlan
from llm_port_backend.services.inference.service import (
    EnvironmentService,
    InferenceError,
)
from llm_port_backend.web.api.inference.control_planes import _map_inference_error
from llm_port_backend.web.api.inference.schema import (
    ApplyPlanRequest,
    EnvironmentCreate,
    EnvironmentDTO,
    EnvironmentNodeAdd,
    EnvironmentUpdate,
)
from llm_port_backend.web.api.rbac import require_permission

router = APIRouter()
_ENV = "inference.environments"


@router.get("", response_model=list[EnvironmentDTO])
@router.get("/", response_model=list[EnvironmentDTO], include_in_schema=False)
async def list_environments(
    control_plane_id: uuid.UUID | None = None,
    _user: User = Depends(require_permission(_ENV, "read")),
    service: EnvironmentService = Depends(),
) -> list[EnvironmentDTO]:
    """List inference environments, optionally filtered by control plane."""
    return [_dto_from_env(e) for e in await service.list(control_plane_id=control_plane_id)]


@router.post("", response_model=EnvironmentDTO, status_code=status.HTTP_201_CREATED)
@router.post("/", response_model=EnvironmentDTO, status_code=status.HTTP_201_CREATED, include_in_schema=False)
async def create_environment(
    body: EnvironmentCreate,
    _user: User = Depends(require_permission(_ENV, "create")),
    service: EnvironmentService = Depends(),
) -> EnvironmentDTO:
    """Create an inference environment under an existing control plane."""
    try:
        env = await service.create(
            control_plane_id=body.control_plane_id,
            name=body.name,
            description=body.description,
            ray_version=body.ray_version,
            head_node_id=body.head_node_id,
            address=body.address,
            config=body.config,
        )
    except InferenceError as exc:
        raise _map_inference_error(exc)
    return _dto_from_env(env)


@router.get("/{environment_id}", response_model=EnvironmentDTO)
async def get_environment(
    environment_id: uuid.UUID,
    _user: User = Depends(require_permission(_ENV, "read")),
    service: EnvironmentService = Depends(),
) -> EnvironmentDTO:
    """Get a single inference environment by ID."""
    try:
        env = await service.get(environment_id)
    except InferenceError as exc:
        raise _map_inference_error(exc)
    return _dto_from_env(env)


@router.patch("/{environment_id}", response_model=EnvironmentDTO)
async def update_environment(
    environment_id: uuid.UUID,
    body: EnvironmentUpdate,
    _user: User = Depends(require_permission(_ENV, "update")),
    service: EnvironmentService = Depends(),
) -> EnvironmentDTO:
    """Patch environment fields. Omitted fields are left unchanged."""
    kwargs = body.model_dump(exclude_unset=True)
    try:
        env = await service.update(environment_id, **kwargs)
    except InferenceError as exc:
        raise _map_inference_error(exc)
    return _dto_from_env(env)


@router.delete("/{environment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_environment(
    environment_id: uuid.UUID,
    _user: User = Depends(require_permission(_ENV, "delete")),
    service: EnvironmentService = Depends(),
) -> None:
    """Delete an environment. Refused while deployments exist."""
    try:
        await service.delete(environment_id)
    except InferenceError as exc:
        raise _map_inference_error(exc)


@router.post("/{environment_id}/reconcile", response_model=EnvironmentDTO)
async def reconcile_environment(
    environment_id: uuid.UUID,
    _user: User = Depends(require_permission(_ENV, "operate")),
    service: EnvironmentService = Depends(),
) -> EnvironmentDTO:
    """Request a reconcile: queue the environment for the background reconciler.

    Returns immediately with the current environment; observed state is
    updated by the next reconciler pass.
    """
    try:
        env = await service.request_reconcile(environment_id)
    except InferenceError as exc:
        raise _map_inference_error(exc)
    return _dto_from_env(env)


@router.post(
    "/{environment_id}/nodes",
    response_model=EnvironmentDTO,
    status_code=status.HTTP_201_CREATED,
)
async def add_environment_node(
    environment_id: uuid.UUID,
    body: EnvironmentNodeAdd,
    _user: User = Depends(require_permission(_ENV, "update")),
    service: EnvironmentService = Depends(),
) -> EnvironmentDTO:
    """Register an infra node as a desired environment member (Phase 1)."""
    try:
        await service.add_node(environment_id, body.node_id, body.role)
    except InferenceError as exc:
        raise _map_inference_error(exc)
    env = await service.get(environment_id)
    return _dto_from_env(env)


@router.post(
    "/{environment_id}/plan",
    response_model=InferenceEnvironmentPlan,
)
async def plan_environment(
    environment_id: uuid.UUID,
    request: Request,
    validate: bool | None = Query(
        None,
        description=(
            "Run the cheap agent-to-agent TCP reachability challenge on the recommended "
            "candidate. Defaults to running it when the nodes are reachable; pass false to "
            "plan from passive facts only."
        ),
    ),
    _user: User = Depends(require_permission(_ENV, "operate")),
    service: EnvironmentService = Depends(),
) -> InferenceEnvironmentPlan:
    """Generate ephemeral interconnect fabric plan with recommendation scores.

    The probe needs its own short transactions (the command has to be committed
    before the websocket dispatcher can hand it to an agent), so the request's
    session factory is passed through rather than this request's session.
    """
    gateway = None
    if validate is not False:
        factory = getattr(request.app.state, "db_session_factory", None)
        if factory is not None:
            from llm_port_backend.services.inference.drivers.ray.commands import (
                NodeCommandGateway,
            )

            gateway = NodeCommandGateway(factory)
    try:
        return await service.plan_fabric(environment_id, gateway=gateway, validate=validate)
    except InferenceError as exc:
        raise _map_inference_error(exc)


@router.post(
    "/{environment_id}/apply-plan",
    response_model=EnvironmentDTO,
)
async def apply_environment_plan(
    environment_id: uuid.UUID,
    body: ApplyPlanRequest,
    _user: User = Depends(require_permission(_ENV, "operate")),
    service: EnvironmentService = Depends(),
) -> EnvironmentDTO:
    """Apply an approved interconnect fabric plan to the environment.

    The plan is re-derived server-side from the live inventory; the submitted
    document is an approval receipt, checked against the re-derived inventory
    digests to reject a plan the operator approved against a topology that has
    since moved.
    """
    try:
        env = await service.apply_fabric_plan(
            environment_id,
            body.plan,
            selected_candidate_id=body.selected_candidate_id,
        )
    except InferenceError as exc:
        raise _map_inference_error(exc)
    return _dto_from_env(env)


def _dto_from_env(env) -> EnvironmentDTO:
    return EnvironmentDTO.model_validate(env)
