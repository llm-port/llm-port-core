"""High-level orchestration service for the neutral inference domain (Phase 1).

This is the **desired-state** entry point used by the ``/api/inference``
routes.  It is deliberately transaction-scoped: every public method operates
on an already-bound ``AsyncSession`` (injected as a DAO) and only *flushes*.
The web layer owns the commit/rollback boundary (``get_db_session``).

Live backend actions (talking to Ray, scheduling replicas, etc.) are NOT
performed here.  The :mod:`reconciliation` seams and the driver
:mod:`registry` exist so that Phase 2's ``RayDriver`` / ``DeploymentOrchestrator``
implementations can be plugged in without reshaping these call sites.

Boundary note: each service's public methods treat **absent arguments as
"unspecified"** (pass the ``...`` sentinel through to the DAO).  ``None`` has a
meaning (clear a nullable field / keep current).  The API layer maps an absent
request field to ``...`` so the two never clash.
"""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from llm_port_backend.services.inference.planner import InferenceEnvironmentPlan

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.dao.inference_dao import (
    ControlPlaneDAO,
    DeploymentDAO,
    EndpointDAO,
    EnvironmentDAO,
    EnvironmentNodeDAO,
)
from llm_port_backend.db.dao.llm_dao import ModelDAO
from llm_port_backend.db.dependencies import get_db_session
from llm_port_backend.db.models.inference import (
    DeploymentDesiredState,
    EnvironmentDesiredState,
    EnvironmentNodeRole,
    InferenceControlPlane,
    InferenceDeployment,
    InferenceEndpoint,
    InferenceEnvironment,
)
from llm_port_backend.services.inference.reconciliation import (
    control_plane_observation,
    deployment_observation,
    environment_observation,
)
from llm_port_backend.services.inference.schemas import (
    InferenceDeploymentSpecV1Alpha1,
    parse_inference_deployment_spec,
)
from llm_port_backend.services.inference.wakeup import wake_reconciler_after_commit

log = logging.getLogger(__name__)


class InferenceError(Exception):
    """Base for inference orchestration errors mapped to HTTP by the API layer."""


class NotFoundError(InferenceError):
    """A referenced domain object does not exist (HTTP 404)."""

    def __init__(self, what: str, identifier: Any) -> None:
        super().__init__(f"{what} not found: {identifier}")
        self.what = what
        self.identifier = identifier


class ConflictError(InferenceError):
    """A write violated a uniqueness/consistency constraint (HTTP 409)."""

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


def _enum(value: str, enum_cls: type, *, what: str) -> Any:
    """Coerce a string into ``enum_cls`` or raise :class:`ConflictError`."""
    try:
        return enum_cls(value)
    except ValueError as exc:
        raise ConflictError(f"invalid {what}: {value!r}") from exc


# ---------------------------------------------------------------------------
# Control planes
# ---------------------------------------------------------------------------


class ControlPlaneService:
    """Control-plane lifecycle (Phase 1: desired state only, no live probes)."""

    def __init__(self, session: AsyncSession = Depends(get_db_session)) -> None:
        self.session = session
        self.dao = ControlPlaneDAO(session)
        self.environment_dao = EnvironmentDAO(session)

    async def create(
        self,
        *,
        name: str,
        driver: str,
        description: str | None = None,
        config: dict[str, Any] | None = None,
        credential_ref: str | None = None,
        enabled: bool = True,
    ) -> InferenceControlPlane:
        """Create a control plane, guarding the unique ``name``."""
        try:
            return await self.dao.create(
                name=name,
                driver=driver,
                description=description,
                config=config,
                credential_ref=credential_ref,
                enabled=enabled,
            )
        except IntegrityError as exc:
            raise ConflictError(f"control plane name already exists: {name}") from exc

    async def get(self, control_plane_id: uuid.UUID) -> InferenceControlPlane:
        """Fetch a control plane or raise :class:`NotFoundError`."""
        control_plane = await self.dao.get(control_plane_id)
        if control_plane is None:
            raise NotFoundError("control plane", control_plane_id)
        return control_plane

    async def list(self) -> list[InferenceControlPlane]:
        """List control planes."""
        return await self.dao.list_all()

    async def update(
        self,
        control_plane_id: uuid.UUID,
        *,
        name: str | None = None,
        description: str | None = ...,
        config: dict[str, Any] | None = ...,
        credential_ref: str | None = ...,
        enabled: bool | None = ...,
    ) -> InferenceControlPlane:
        """Patch a control plane; any config change bumps ``generation``.

        ``driver`` is an immutable identity field in Phase 1 and is not
        writable through this endpoint.
        """
        try:
            updated = await self.dao.update(
                control_plane_id,
                name=name,
                description=description,
                config=config,
                credential_ref=credential_ref,
                enabled=enabled,
            )
        except IntegrityError as exc:
            raise ConflictError("control plane update violated a constraint") from exc
        if updated is None:
            raise NotFoundError("control plane", control_plane_id)
        return updated

    async def delete(self, control_plane_id: uuid.UUID) -> None:
        """Hard-delete a control plane, refused while environments exist."""
        environment = await self.environment_dao.list_all(control_plane_id=control_plane_id)
        if environment:
            raise ConflictError("control plane has environments; delete them first")
        if not await self.dao.delete(control_plane_id):
            raise NotFoundError("control plane", control_plane_id)

    # -- Reconciliation seam (no live actions in Phase 1). ----------------
    async def reconcile(self, control_plane_id: uuid.UUID) -> InferenceControlPlane:
        """Drive a control plane toward its desired state.

        Phase 1: no driver is registered, so the driver is resolved through the
        (currently empty) registry and an honest no-op observation is recorded
        at the current generation.  No backend dependency is required.
        """
        control_plane = await self.get(control_plane_id)
        control_plane.observed_generation = control_plane.generation
        control_plane.observed_status_json = control_plane_observation(control_plane.driver)
        await self.session.flush()
        await self.session.refresh(control_plane)
        return control_plane

    async def request_reconcile(self, control_plane_id: uuid.UUID) -> InferenceControlPlane:
        """Queue every environment of the control plane for the reconciler.

        A control plane has no reconcile pass of its own; its live state is
        its environments'.  Observed state is left untouched.
        """
        control_plane = await self.get(control_plane_id)
        for environment in await self.environment_dao.list_all(control_plane_id=control_plane_id):
            _queue_for_reconcile(environment)
        await self.session.flush()
        return control_plane



async def _forget_cluster_metrics(environment_id: uuid.UUID) -> None:
    """Drop a deleted cluster's scrape targets and dashboard (best-effort)."""
    try:
        from llm_port_backend.services.llm.monitoring import (  # noqa: PLC0415
            get_monitoring_provisioner,
        )

        provisioner = get_monitoring_provisioner()
        if provisioner is not None:
            await provisioner.remove_ray_targets(environment_id, drop_dashboard=True)
    except Exception:  # noqa: BLE001 - monitoring never blocks a delete
        log.warning("Could not remove metrics for deleted cluster %s", environment_id, exc_info=True)


def _queue_for_reconcile(row: Any) -> None:
    """Put *row* back in the reconciler's queue without touching its observed state.

    The loop selects rows whose ``observed_generation`` lags ``generation``;
    the next pass re-stamps it.  Unlike :meth:`reconcile` (the no-driver
    fallback), this never overwrites ``observed_status_json`` — the driver's
    bookkeeping there (e.g. the applied config hash) must survive, or the next
    pass would redeploy and restart the model.
    """
    if row.observed_generation >= row.generation:
        row.observed_generation = row.generation - 1


# ---------------------------------------------------------------------------
# Environments
# ---------------------------------------------------------------------------


#: Statuses in which a cluster may have Ray running on its machines.
_RUNNING_STATUSES = frozenset({"ready", "running", "degraded", "preparing"})


class EnvironmentService:
    """Environment lifecycle (Phase 1: desired state only, no live actions)."""

    def __init__(self, session: AsyncSession = Depends(get_db_session)) -> None:
        self.session = session
        self.dao = EnvironmentDAO(session)
        self.control_plane_dao = ControlPlaneDAO(session)
        self.deployment_dao = DeploymentDAO(session)
        self.node_dao = EnvironmentNodeDAO(session)
        self.model_dao = ModelDAO(session)

    async def create(
        self,
        *,
        control_plane_id: uuid.UUID,
        name: str,
        description: str | None = None,
        runtime_version: str | None = None,
        head_node_id: uuid.UUID | None = None,
        address: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> InferenceEnvironment:
        """Create an environment under an existing control plane."""
        wake_reconciler_after_commit(self.session)  # act now, not at the next tick
        if not await self.control_plane_dao.get(control_plane_id):
            raise NotFoundError("control plane", control_plane_id)
        try:
            return await self.dao.create(
                control_plane_id=control_plane_id,
                name=name,
                description=description,
                runtime_version=runtime_version,
                head_node_id=head_node_id,
                address=address,
                config=config,
            )
        except IntegrityError as exc:
            raise ConflictError(f"environment name already exists: {name}") from exc

    async def get(self, environment_id: uuid.UUID) -> InferenceEnvironment:
        """Fetch an environment or raise :class:`NotFoundError`."""
        environment = await self.dao.get(environment_id)
        if environment is None:
            raise NotFoundError("environment", environment_id)
        return environment

    async def list(self, control_plane_id: uuid.UUID | None = None) -> list[InferenceEnvironment]:
        """List environments, optionally filtered by control plane."""
        return await self.dao.list_all(control_plane_id=control_plane_id)

    async def list_pending_reconciliation(self) -> list[InferenceEnvironment]:
        """List environments requiring reconciliation."""
        return await self.dao.list_pending_observation()

    async def update(
        self,
        environment_id: uuid.UUID,
        *,
        description: str | None = ...,
        desired_state: str | None = ...,
        runtime_version: str | None = ...,
        head_node_id: uuid.UUID | None = ...,
        address: str | None = ...,
        config: dict[str, Any] | None = ...,
    ) -> InferenceEnvironment:
        """Patch an environment; config/desired-state changes bump ``generation``."""
        wake_reconciler_after_commit(self.session)  # act now, not at the next tick
        ds: EnvironmentDesiredState | None = ...
        if desired_state is not ...:
            if desired_state is None:
                ds = None
            else:
                ds = _enum(desired_state, EnvironmentDesiredState, what="desired_state")
        try:
            updated = await self.dao.update(
                environment_id,
                description=description,
                desired_state=ds,
                runtime_version=runtime_version,
                head_node_id=head_node_id,
                address=address,
                config=config,
            )
        except IntegrityError as exc:
            raise ConflictError("environment update violated a constraint") from exc
        if updated is None:
            raise NotFoundError("environment", environment_id)
        return updated

    async def add_node(
        self, environment_id: uuid.UUID, node_id: uuid.UUID, role: str = "worker"
    ) -> None:
        """Register an infra node as a desired environment member.

        The node is also placed in the compute pool matching its hardware.
        Derived rather than asked for: the pool is the equivalence class the
        node already belongs to, so joining a cluster is still one decision
        even when the cluster is mixed-vendor.
        """
        await self.get(environment_id)
        node_role = _enum(role, EnvironmentNodeRole, what="role")
        try:
            member = await self.node_dao.add_node(environment_id, node_id, node_role)
        except IntegrityError as exc:
            raise ConflictError(
                "node is already a member of this environment or unknown"
            ) from exc

        from llm_port_backend.db.dao.node_control_dao import NodeControlDAO
        from llm_port_backend.services.inference.pools import ComputePoolCoordinator

        node = await NodeControlDAO(self.session).get_node_by_id(node_id)
        if node is not None:
            await ComputePoolCoordinator(self.session).assign(
                environment_id=environment_id, node=node, member=member
            )

    async def remove_node(self, environment_id: uuid.UUID, node_id: uuid.UUID) -> None:
        """Remove an infra node from an environment (no-op if absent).

        Refuses to unbind the head of an environment that is still meant to
        run: ``head_node_id`` would keep pointing at a node that is no longer
        a member, and the next reconcile pass would address a cluster whose
        head it does not manage.  Stop the environment first, or bind another
        head.
        """
        environment = await self.get(environment_id)
        if (
            environment.head_node_id == node_id
            and environment.desired_state != EnvironmentDesiredState.STOPPED.value
        ):
            raise ConflictError(
                "cannot remove the head node while the environment is running; "
                "stop it or bind a different head first"
            )
        await self.node_dao.remove_node(environment_id, node_id)

    async def lifecycle_progress(self, environment_id: uuid.UUID) -> dict[str, Any] | None:
        """What each member machine last reported while this cluster comes up.

        Keyed on the command idempotency keys the Ray driver issues
        (``inference-env:<id>:...``), so it covers every member and every
        lifecycle step without the driver having to publish anything.

        Per machine, not just the newest event overall: a two-machine cluster
        receives its runtime image on both at once, and a single "latest"
        line would flick between them every few seconds.
        """
        from llm_port_backend.db.models.node_control import (  # noqa: PLC0415
            InfraNodeCommand,
            InfraNodeCommandEvent,
            NodeCommandStatus,
        )

        in_flight = [
            NodeCommandStatus.DISPATCHED.value,
            NodeCommandStatus.ACKED.value,
            NodeCommandStatus.RUNNING.value,
        ]
        rows = (
            await self.session.execute(
                select(
                    InfraNodeCommandEvent.message,
                    InfraNodeCommandEvent.payload_json,
                    InfraNodeCommandEvent.created_at,
                    InfraNodeCommand.command_type,
                    InfraNodeCommand.node_id,
                )
                .join(InfraNodeCommand, InfraNodeCommand.id == InfraNodeCommandEvent.command_id)
                .where(
                    InfraNodeCommand.idempotency_key.like(f"inference-env:{environment_id}:%"),
                    InfraNodeCommand.status.in_(in_flight),
                    InfraNodeCommandEvent.phase == "progress",
                )
                # ``seq`` breaks ties: events written in one transaction share
                # a timestamp, and "newest" must not be a coin toss.
                .order_by(
                    InfraNodeCommandEvent.created_at.desc(),
                    InfraNodeCommandEvent.seq.desc(),
                )
                .limit(200)
            )
        ).all()
        if not rows:
            return None

        machines: dict[str, dict[str, Any]] = {}
        for row in rows:  # newest first: keep the first seen per machine
            node = str(row.node_id)
            if node in machines:
                continue
            payload = row.payload_json if isinstance(row.payload_json, dict) else {}
            pct = payload.get("progress_pct")
            machines[node] = {
                "node_id": node,
                "message": row.message,
                "progress_pct": pct if isinstance(pct, (int, float)) else None,
                "step": row.command_type,
                "at": row.created_at.isoformat() if row.created_at else None,
            }

        newest = next(iter(machines.values()))
        return {**newest, "machines": sorted(machines.values(), key=lambda m: m["node_id"])}

    async def request_reconcile(self, environment_id: uuid.UUID) -> InferenceEnvironment:
        """Queue the environment for the reconciler, now.

        Observed state is left alone except for the failure backoff and a
        recovery that gave up: a person pressing Try again has usually just
        fixed whatever it was waiting on, and making them wait out an
        hour-long recheck -- or a recovery that has stopped trying -- would be
        the reconciler overruling the one party who knows something changed.
        """
        wake_reconciler_after_commit(self.session)  # act now, not at the next tick
        environment = await self.get(environment_id)
        observed = dict(environment.observed_status_json or {})
        retry = observed.pop("retry", None)
        recovery = observed.pop("recovery", None)
        if retry is not None or recovery is not None:
            environment.observed_status_json = observed
        _queue_for_reconcile(environment)
        await self.session.flush()
        await self.session.refresh(environment)
        return environment

    async def reconcile(self, environment_id: uuid.UUID) -> InferenceEnvironment:
        """Record an honest no-op observation (no driver can act).

        Used by the reconciliation seam when the environment's driver is not
        registered; marks the environment observed at its current generation.
        API callers use :meth:`request_reconcile`.
        """
        environment = await self.get(environment_id)
        environment.observed_generation = environment.generation
        environment.observed_status_json = environment_observation()
        await self.session.flush()
        await self.session.refresh(environment)
        return environment

    async def delete(self, environment_id: uuid.UUID, *, force: bool = False) -> None:
        """Delete an environment, refused while deployments exist or it runs.

        ``force`` deletes a running cluster anyway, but only when none of its
        machines is reachable -- the one case where stopping it is impossible
        rather than merely skipped. What was left on them is replaced the
        next time that machine starts a cluster.

        Deleting the row does not reach the machines. A cluster deleted while
        running would leave its Ray head and workers going on the hardware
        with nothing in LLM.Port left to stop them -- so it has to be stopped
        first, which the console does for the operator before deleting.
        """
        environment = await self.get(environment_id)
        deployments = await self.deployment_dao.list_all(environment_id=environment_id)
        if deployments:
            raise ConflictError("environment has deployments; delete them first")
        running = environment.desired_state == "running" and environment.status in _RUNNING_STATUSES
        stopping = environment.desired_state == "stopped" and environment.status in _RUNNING_STATUSES
        if (running or stopping) and not (force and await self._no_member_reachable(environment_id)):
            raise ConflictError(
                "the cluster is running on its machines; stop it first so they are "
                "left clean, then delete it"
            )
        if not await self.dao.delete(environment_id):
            raise NotFoundError("environment", environment_id)
        await _forget_cluster_metrics(environment_id)

    async def _no_member_reachable(self, environment_id: uuid.UUID) -> bool:
        from llm_port_backend.db.models.node_control import InfraNode, NodeHealthStatus  # noqa: PLC0415

        members = await self.node_dao.list_for_environment(environment_id)
        if not members:
            return True
        rows = (
            await self.session.execute(
                select(InfraNode.status).where(InfraNode.id.in_([m.node_id for m in members]))
            )
        ).scalars().all()
        return all(status == NodeHealthStatus.OFFLINE.value for status in rows)

    async def plan_fabric(
        self,
        environment_id: uuid.UUID,
        *,
        gateway: Any = None,
        validate: bool | None = None,
    ) -> InferenceEnvironmentPlan:
        """Generate an ephemeral interconnect plan across participating nodes.

        ``gateway`` enables the cheap agent-to-agent reachability challenge; a
        plan produced without one says so in its ``warnings``.
        """
        from llm_port_backend.services.inference.planner import MultiNodeFabricPlanner

        planner = MultiNodeFabricPlanner(self.session, gateway=gateway)
        return await planner.plan_environment(environment_id, validate=validate)

    async def apply_fabric_plan(
        self,
        environment_id: uuid.UUID,
        plan: "InferenceEnvironmentPlan | None" = None,
        *,
        selected_candidate_id: str | None = None,
    ) -> InferenceEnvironment:
        """Apply an approved fabric plan to the environment.

        The plan is re-derived server-side; *plan* is the approval receipt used
        for stale detection only.
        """
        from llm_port_backend.services.inference.planner import MultiNodeFabricPlanner

        planner = MultiNodeFabricPlanner(self.session)
        return await planner.apply_plan(
            environment_id,
            plan,
            selected_candidate_id=selected_candidate_id,
        )

    async def evaluate_artifact(
        self,
        environment_id: uuid.UUID,
        model_id: uuid.UUID,
    ) -> Any:
        """Report artifact readiness for a model across environment nodes.

        Read-only: ``persist=False`` so a caller holding only ``read`` cannot
        reconcile availability rows just by polling this route.
        """
        environment = await self.get(environment_id)
        model = await self.model_dao.get(model_id)
        if model is None:
            raise NotFoundError("model", model_id)

        from llm_port_backend.services.inference.artifacts import ModelArtifactCoordinator

        coordinator = ModelArtifactCoordinator(self.session)
        nodes = await coordinator.eligible_nodes(environment)
        if not nodes:
            raise ConflictError("Environment has no eligible nodes for artifact synchronization")

        return await coordinator.evaluate(
            model=model, environment=environment, persist=False
        )

    async def sync_artifact(
        self,
        environment_id: uuid.UUID,
        model_id: uuid.UUID,
        *,
        gateway: Any = None,
    ) -> Any:
        """Evaluate and trigger artifact synchronization for model on environment nodes."""
        environment = await self.get(environment_id)
        model = await self.model_dao.get(model_id)
        if model is None:
            raise NotFoundError("model", model_id)

        from llm_port_backend.services.inference.artifacts import ModelArtifactCoordinator

        coordinator = ModelArtifactCoordinator(self.session, gateway=gateway)
        nodes = await coordinator.eligible_nodes(environment)
        if not nodes:
            raise ConflictError("Environment has no eligible nodes for artifact synchronization")

        return await coordinator.ensure(model=model, environment=environment, gateway=gateway)



# ---------------------------------------------------------------------------
# Deployments
# ---------------------------------------------------------------------------


class DeploymentService:
    """Deployment lifecycle (Phase 1: spec validation + desired state)."""

    def __init__(self, session: AsyncSession = Depends(get_db_session)) -> None:
        self.session = session
        self.dao = DeploymentDAO(session)
        self.environment_dao = EnvironmentDAO(session)
        self.endpoint_dao = EndpointDAO(session)
        self.model_dao = ModelDAO(session)

    async def create(
        self,
        *,
        environment_id: uuid.UUID,
        model_id: uuid.UUID,
        name: str,
        spec: dict[str, Any],
        description: str | None = None,
    ) -> InferenceDeployment:
        """Create a deployment from a validated versioned spec."""
        wake_reconciler_after_commit(self.session)  # act now, not at the next tick
        if not await self.environment_dao.get(environment_id):
            raise NotFoundError("environment", environment_id)
        if not await self.model_dao.get(model_id):
            raise NotFoundError("model", model_id)
        # Re-validate so the DB only ever holds well-formed documents; any
        # malformed document surfaces as a 409, not a 500.
        self.validate_spec(spec)
        try:
            return await self.dao.create(
                environment_id=environment_id,
                model_id=model_id,
                name=name,
                spec=spec,
                description=description,
                desired_state=DeploymentDesiredState.ACTIVE,
            )
        except IntegrityError as exc:
            raise ConflictError(f"deployment name already exists: {name}") from exc

    def validate_spec(self, spec: dict[str, Any]) -> InferenceDeploymentSpecV1Alpha1:
        """Validate a spec without persisting.

        :raises ConflictError: if the document is not a known, valid spec.
        """
        try:
            return parse_inference_deployment_spec(spec)
        except Exception as exc:  # noqa: BLE001 - map any validation failure
            raise ConflictError(f"invalid deployment spec: {exc}") from exc

    async def get(self, deployment_id: uuid.UUID) -> InferenceDeployment:
        """Fetch a deployment or raise :class:`NotFoundError`."""
        deployment = await self.dao.get(deployment_id)
        if deployment is None:
            raise NotFoundError("deployment", deployment_id)
        return deployment

    async def list(
        self,
        environment_id: uuid.UUID | None = None,
        model_id: uuid.UUID | None = None,
    ) -> list[InferenceDeployment]:
        """List deployments, optionally filtered by environment or model."""
        return await self.dao.list_all(environment_id=environment_id, model_id=model_id)

    async def update(
        self,
        deployment_id: uuid.UUID,
        *,
        spec: dict[str, Any] | None = ...,
        desired_state: str | None = ...,
        description: str | None = ...,
    ) -> InferenceDeployment:
        """Patch a deployment; spec/desired-state changes bump ``generation``."""
        wake_reconciler_after_commit(self.session)  # act now, not at the next tick
        dds: DeploymentDesiredState | None = ...
        if desired_state is not ...:
            if desired_state is None:
                dds = None
            else:
                dds = _enum(desired_state, DeploymentDesiredState, what="desired_state")
        # ``...`` here means "keep the current spec"; a real ``None`` is rejected
        # by the DAO and would be an error, so we never translate absence to None.
        provided_spec: dict[str, Any] | None = ...
        if spec is not ...:
            if spec is None:
                raise ConflictError("spec cannot be cleared; omit it to keep the current spec")
            self.validate_spec(spec)
            provided_spec = spec
        try:
            updated = await self.dao.update(
                deployment_id,
                spec=provided_spec,
                desired_state=dds,
                description=description,
            )
        except IntegrityError as exc:
            raise ConflictError("deployment update violated a constraint") from exc
        if updated is None:
            raise NotFoundError("deployment", deployment_id)
        return updated

    async def request_reconcile(self, deployment_id: uuid.UUID) -> InferenceDeployment:
        """Queue the deployment for the reconciler (observed state untouched).

        A ``failed`` deployment is re-applied on that pass; a ``running`` one
        is only re-observed (its applied config hash is preserved).
        """
        wake_reconciler_after_commit(self.session)  # act now, not at the next tick
        deployment = await self.get(deployment_id)
        _queue_for_reconcile(deployment)
        await self.session.flush()
        await self.session.refresh(deployment)
        return deployment

    async def reconcile(self, deployment_id: uuid.UUID) -> InferenceDeployment:
        """Record an honest no-op observation (no driver can act).

        Used by the reconciliation seam when the deployment's driver is not
        registered.  We intentionally do NOT move the phase to ``applying``
        (nothing is actually applied).  API callers use
        :meth:`request_reconcile`.
        """
        deployment = await self.get(deployment_id)
        await self.dao.set_observed(
            deployment_id,
            observed_generation=deployment.generation,
            observed_status=deployment_observation(),
        )
        return await self.get(deployment_id)

    async def delete(self, deployment_id: uuid.UUID) -> None:
        """Delete a deployment (endpoints are cascade-deleted by the DB).

        The provider row the deployment owned goes with it. Not a cascade:
        ``llm_providers`` is in a different subsystem and deliberately has no
        foreign key to a deployment, so the ownership is carried by
        ``source_kind``/``source_id`` and has to be honoured in code.

        Done before the row is gone, because after it there is nothing left
        to identify the provider by.
        """
        from llm_port_backend.services.inference.publication import (  # noqa: PLC0415
            InferencePublicationCoordinator,
        )

        coordinator = InferencePublicationCoordinator(self.session)
        await coordinator.remove_derived_provider(deployment_id)

        if not await self.dao.delete(deployment_id):
            raise NotFoundError("deployment", deployment_id)

    async def endpoints(self, deployment_id: uuid.UUID) -> list[InferenceEndpoint]:
        """Return the endpoints declared for a deployment."""
        await self.get(deployment_id)
        return await self.endpoint_dao.list_for_deployment(deployment_id)
