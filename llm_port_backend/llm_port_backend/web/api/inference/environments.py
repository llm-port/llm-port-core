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
from llm_port_backend.services.inference.artifacts import ArtifactReadiness
from llm_port_backend.services.inference.planner import InferenceEnvironmentPlan
from llm_port_backend.services.inference.service import (
    EnvironmentService,
    InferenceError,
)
from llm_port_backend.web.api.inference.control_planes import _map_inference_error
from llm_port_backend.web.api.inference.schema import (
    ApplyPlanRequest,
    ComputePoolDTO,
    EnvironmentCreate,
    EnvironmentDTO,
    EnvironmentNodeAdd,
    EnvironmentNodeDTO,
    EnvironmentUpdate,
)
from llm_port_backend.services.inference.observability import (
    EnvironmentMetrics,
    ObservabilityUnsupported,
)
from llm_port_backend.services.nodes.service import NodeControlService
from llm_port_backend.web.api.inference.observability import (
    get_node_control_service,
    resolve_driver_for_environment,
    unsupported,
)
from llm_port_backend.web.api.rbac import require_permission

router = APIRouter()
_ENV = "inference.environments"

#: Statuses in which a member machine may be reporting progress worth showing.
_COMING_UP = frozenset({"pending", "preparing"})


@router.get("", response_model=list[EnvironmentDTO])
@router.get("/", response_model=list[EnvironmentDTO], include_in_schema=False)
async def list_environments(
    control_plane_id: uuid.UUID | None = None,
    _user: User = Depends(require_permission(_ENV, "read")),
    service: EnvironmentService = Depends(),
) -> list[EnvironmentDTO]:
    """List inference environments, optionally filtered by control plane."""
    dtos = []
    for env in await service.list(control_plane_id=control_plane_id):
        dto = _dto_from_env(env)
        if env.status in _COMING_UP:
            dto.progress = await service.lifecycle_progress(env.id)
        dtos.append(dto)
    return dtos


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
            runtime_version=body.runtime_version,
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
    dto = _dto_from_env(env)
    if env.status in _COMING_UP:
        dto.progress = await service.lifecycle_progress(env.id)
    return dto


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
    force: bool = Query(False, description="Delete a running cluster whose machines are all offline."),
    _user: User = Depends(require_permission(_ENV, "delete")),
    service: EnvironmentService = Depends(),
) -> None:
    """Delete an environment. Refused while deployments exist, or while it runs."""
    try:
        await service.delete(environment_id, force=force)
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


@router.delete(
    "/{environment_id}/nodes/{node_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_environment_node(
    environment_id: uuid.UUID,
    node_id: uuid.UUID,
    _user: User = Depends(require_permission(_ENV, "update")),
    service: EnvironmentService = Depends(),
) -> None:
    """Drop a node from an environment's desired membership.

    The counterpart to ``POST .../nodes``.  Without it an operator who added
    the wrong node could only undo it in the database.  Desired state only:
    no node is contacted, and the reconciler converges on the next pass.
    """
    try:
        await service.remove_node(environment_id, node_id)
    except InferenceError as exc:
        raise _map_inference_error(exc)


@router.get("/{environment_id}/pools", response_model=list[ComputePoolDTO])
async def list_environment_pools(
    environment_id: uuid.UUID,
    _user: User = Depends(require_permission(_ENV, "read")),
    service: EnvironmentService = Depends(),
) -> list[ComputePoolDTO]:
    """List the compute pools derived for this cluster.

    Read-only.  A single-vendor cluster has exactly one, which is why nothing
    has to be configured; a mixed cluster has one per compatibility class, and
    that is what placement and runtime-bundle matching both key off.
    """
    try:
        await service.get(environment_id)
    except InferenceError as exc:
        raise _map_inference_error(exc)

    from llm_port_backend.services.inference.pools import ComputePoolCoordinator

    coordinator = ComputePoolCoordinator(service.session)
    pools = await coordinator.list_for_environment(environment_id)
    counts = await coordinator.member_counts(environment_id)
    result = []
    for pool in pools:
        dto = ComputePoolDTO.model_validate(pool)
        dto.member_count = counts.get(pool.id, 0)
        result.append(dto)
    return result


@router.get("/{environment_id}/nodes", response_model=list[EnvironmentNodeDTO])
async def list_environment_nodes(
    environment_id: uuid.UUID,
    _user: User = Depends(require_permission(_ENV, "read")),
    service: EnvironmentService = Depends(),
) -> list[EnvironmentNodeDTO]:
    """List the nodes registered as members of an environment.

    Read-only: it reports desired membership and whatever the last reconcile
    observed per node, and contacts nothing.
    """
    try:
        await service.get(environment_id)
    except InferenceError as exc:
        raise _map_inference_error(exc)
    memberships = await service.node_dao.list_for_environment(environment_id)
    return [EnvironmentNodeDTO.model_validate(m) for m in memberships]


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


@router.post(
    "/{environment_id}/artifacts/{model_id}/sync",
    response_model=ArtifactReadiness,
)
async def sync_environment_artifact(
    environment_id: uuid.UUID,
    model_id: uuid.UUID,
    request: Request,
    _user: User = Depends(require_permission(_ENV, "operate")),
    service: EnvironmentService = Depends(),
) -> ArtifactReadiness:
    """Evaluate readiness and trigger artifact synchronization for model on environment nodes."""
    gateway = None
    factory = getattr(request.app.state, "db_session_factory", None)
    if factory is not None:
        from llm_port_backend.services.inference.drivers.ray.commands import (
            NodeCommandGateway,
        )

        gateway = NodeCommandGateway(factory)
    try:
        return await service.sync_artifact(environment_id, model_id, gateway=gateway)
    except InferenceError as exc:
        raise _map_inference_error(exc)


@router.get(
    "/{environment_id}/artifacts/{model_id}",
    response_model=ArtifactReadiness,
)
async def get_environment_artifact_readiness(
    environment_id: uuid.UUID,
    model_id: uuid.UUID,
    _user: User = Depends(require_permission(_ENV, "read")),
    service: EnvironmentService = Depends(),
) -> ArtifactReadiness:
    """Report artifact readiness for a model across environment nodes.

    Read-only: unlike the sync route this never reconciles rows, so a caller
    holding only ``read`` cannot mutate availability state by polling.
    """
    try:
        return await service.evaluate_artifact(environment_id, model_id)
    except InferenceError as exc:
        raise _map_inference_error(exc)


def _dto_from_env(env) -> EnvironmentDTO:
    return EnvironmentDTO.model_validate(env)


@router.get("/{environment_id}/metrics", response_model=EnvironmentMetrics)
async def get_environment_metrics(
    environment_id: uuid.UUID,
    _user: User = Depends(require_permission(_ENV, "read")),
    service: EnvironmentService = Depends(),
    node_control: NodeControlService = Depends(get_node_control_service),
) -> EnvironmentMetrics:
    """Aggregated cluster metrics for an environment.

    Read-only, and honest about gaps: nodes that export no metrics port are
    reported in ``partials`` rather than counted as zero.
    """
    try:
        environment = await service.get(environment_id)
    except InferenceError as exc:
        raise _map_inference_error(exc)

    driver = await resolve_driver_for_environment(service.session, environment)
    try:
        return await driver.environment_metrics(
            service.session, environment, node_control=node_control
        )
    except ObservabilityUnsupported as exc:
        raise unsupported(exc)
