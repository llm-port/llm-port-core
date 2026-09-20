"""DAOs for the neutral inference domain (Ray-first, vendor-agnostic).

Conventions follow the rest of the codebase:

* sessions are injected via FastAPI ``Depends(get_db_session)``;
* writes call ``flush()`` but never ``commit()`` — the request-scoped
  session dependency owns the commit/rollback boundary;
* ``update`` methods use the ``...`` sentinel to mean "unspecified" so
  that nullable fields can be explicitly cleared with ``None``.
"""

from datetime import UTC, datetime
import uuid
from typing import Any

from fastapi import Depends
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.dependencies import get_db_session
from llm_port_backend.db.models.inference import (
    DeploymentDesiredState,
    DeploymentPhase,
    EndpointStatus,
    EnvironmentDesiredState,
    EnvironmentNodeRole,
    EnvironmentStatus,
    InferenceControlPlane,
    InferenceDeployment,
    InferenceEndpoint,
    InferenceEnvironment,
    InferenceEnvironmentBinding,
    InferenceEnvironmentNode,
    ModelAvailability,
    ModelAvailabilityStatus,
)

# -----------------------------------------------------------------------
# Control plane DAO
# -----------------------------------------------------------------------


class ControlPlaneDAO:
    """CRUD operations for inference control planes."""

    def __init__(self, session: AsyncSession = Depends(get_db_session)) -> None:
        self.session = session

    async def create(
        self,
        name: str,
        driver: str,
        description: str | None = None,
        config: dict[str, Any] | None = None,
        credential_ref: str | None = None,
        enabled: bool = True,
    ) -> InferenceControlPlane:
        """Create a new control plane row."""
        control_plane = InferenceControlPlane(
            id=uuid.uuid4(),
            name=name,
            driver=driver,
            description=description,
            config_json=config or {},
            credential_ref=credential_ref,
            enabled=enabled,
        )
        self.session.add(control_plane)
        await self.session.flush()
        return control_plane

    async def get(self, control_plane_id: uuid.UUID) -> InferenceControlPlane | None:
        """Fetch a control plane by ID."""
        result = await self.session.execute(
            select(InferenceControlPlane).where(
                InferenceControlPlane.id == control_plane_id
            ),
        )
        return result.scalar_one_or_none()

    async def get_by_name(self, name: str) -> InferenceControlPlane | None:
        """Fetch a control plane by unique name."""
        result = await self.session.execute(
            select(InferenceControlPlane).where(InferenceControlPlane.name == name),
        )
        return result.scalar_one_or_none()

    async def list_all(
        self,
        *,
        driver: str | None = None,
        enabled_only: bool = False,
    ) -> list[InferenceControlPlane]:
        """Return control planes, optionally filtered."""
        stmt = select(InferenceControlPlane)
        if driver is not None:
            stmt = stmt.where(InferenceControlPlane.driver == driver)
        if enabled_only:
            stmt = stmt.where(InferenceControlPlane.enabled.is_(True))
        stmt = stmt.order_by(InferenceControlPlane.created_at.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def update(
        self,
        control_plane_id: uuid.UUID,
        *,
        name: str | None = None,
        description: str | None = ...,
        config: dict[str, Any] | None = ...,
        credential_ref: str | None = ...,
        enabled: bool | None = ...,
    ) -> InferenceControlPlane | None:
        """Patch writable fields on a control plane."""
        control_plane = await self.get(control_plane_id)
        if control_plane is None:
            return None
        changed = False
        if name is not None:
            control_plane.name = name
            changed = True
        if description is not ...:
            control_plane.description = description
            changed = True
        if config is not ...:
            control_plane.config_json = config
            changed = True
        if credential_ref is not ...:
            control_plane.credential_ref = credential_ref
            changed = True
        if enabled is not ...:
            control_plane.enabled = enabled
            changed = True
        if changed:
            control_plane.generation += 1
        await self.session.flush()
        await self.session.refresh(control_plane)
        return control_plane

    async def delete(self, control_plane_id: uuid.UUID) -> bool:
        """Delete a control plane. Returns False if not found.

        Fails with an integrity error while environments reference the
        plane (``ondelete=RESTRICT``).
        """
        control_plane = await self.get(control_plane_id)
        if control_plane is None:
            return False
        await self.session.delete(control_plane)
        return True


# -----------------------------------------------------------------------
# Environment DAO
# -----------------------------------------------------------------------


class EnvironmentDAO:
    """CRUD operations for managed inference environments."""

    def __init__(self, session: AsyncSession = Depends(get_db_session)) -> None:
        self.session = session

    async def create(
        self,
        control_plane_id: uuid.UUID,
        name: str,
        description: str | None = None,
        ray_version: str | None = None,
        head_node_id: uuid.UUID | None = None,
        address: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> InferenceEnvironment:
        """Create a new environment row."""
        environment = InferenceEnvironment(
            id=uuid.uuid4(),
            control_plane_id=control_plane_id,
            name=name,
            description=description,
            ray_version=ray_version,
            head_node_id=head_node_id,
            address=address,
            config_json=config or {},
        )
        self.session.add(environment)
        await self.session.flush()
        return environment

    async def get(self, environment_id: uuid.UUID) -> InferenceEnvironment | None:
        """Fetch an environment by ID."""
        result = await self.session.execute(
            select(InferenceEnvironment).where(
                InferenceEnvironment.id == environment_id
            ),
        )
        return result.scalar_one_or_none()

    async def get_by_name(self, name: str) -> InferenceEnvironment | None:
        """Fetch an environment by unique name."""
        result = await self.session.execute(
            select(InferenceEnvironment).where(InferenceEnvironment.name == name),
        )
        return result.scalar_one_or_none()

    async def list_all(
        self,
        *,
        control_plane_id: uuid.UUID | None = None,
    ) -> list[InferenceEnvironment]:
        """Return environments, optionally filtered by control plane."""
        stmt = select(InferenceEnvironment)
        if control_plane_id is not None:
            stmt = stmt.where(InferenceEnvironment.control_plane_id == control_plane_id)
        stmt = stmt.order_by(InferenceEnvironment.created_at.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_pending_observation(self) -> list[InferenceEnvironment]:
        """Return environments whose desired state is not yet observed.

        The reconciler must visit:
        * Rows where observed_generation lags generation (new desired state or config change);
        * Rows whose desired state is running but status is not ready (pending, preparing, degraded, failed)
          so failed or converging clusters are retried (F05).
        """
        result = await self.session.execute(
            select(InferenceEnvironment).where(
                or_(
                    InferenceEnvironment.observed_generation
                    < InferenceEnvironment.generation,
                    and_(
                        InferenceEnvironment.desired_state == "running",
                        InferenceEnvironment.status != EnvironmentStatus.READY.value,
                    ),
                )
            )
        )
        return list(result.scalars().all())

    async def update(
        self,
        environment_id: uuid.UUID,
        *,
        name: str | None = None,
        description: str | None = ...,
        desired_state: EnvironmentDesiredState | None = ...,
        ray_version: str | None = ...,
        head_node_id: uuid.UUID | None = ...,
        address: str | None = ...,
        config: dict[str, Any] | None = ...,
        capabilities: dict[str, Any] | None = ...,
    ) -> InferenceEnvironment | None:
        """Patch writable fields; desired-state changes bump generation."""
        environment = await self.get(environment_id)
        if environment is None:
            return None
        changed = False
        if name is not None:
            environment.name = name
            changed = True
        if description is not ...:
            environment.description = description
            changed = True
        if ray_version is not ...:
            environment.ray_version = ray_version
            changed = True
        if head_node_id is not ...:
            environment.head_node_id = head_node_id
            changed = True
        if address is not ...:
            environment.address = address
            changed = True
        if config is not ...:
            environment.config_json = config
            changed = True
        if capabilities is not ...:
            environment.capabilities_json = capabilities
            changed = True
        if desired_state is not ... and desired_state is not None:
            environment.desired_state = desired_state.value
            changed = True
        if changed:
            environment.generation += 1
        await self.session.flush()
        await self.session.refresh(environment)
        return environment

    async def delete(self, environment_id: uuid.UUID) -> bool:
        """Delete an environment. Returns False if not found.

        Fails with an integrity error while deployments reference the
        environment (``ondelete=RESTRICT``).
        """
        environment = await self.get(environment_id)
        if environment is None:
            return False
        await self.session.delete(environment)
        return True


# -----------------------------------------------------------------------
# Binding DAO
# -----------------------------------------------------------------------


class EnvironmentBindingDAO:
    """CRUD operations for environment/control-plane bindings."""

    def __init__(self, session: AsyncSession = Depends(get_db_session)) -> None:
        self.session = session

    async def create(
        self,
        environment_id: uuid.UUID,
        control_plane_id: uuid.UUID,
        driver: str,
        roles: list[str] | None = None,
        config: dict[str, Any] | None = None,
    ) -> InferenceEnvironmentBinding:
        """Create a binding. (environment_id, driver) must be unique."""
        binding = InferenceEnvironmentBinding(
            id=uuid.uuid4(),
            environment_id=environment_id,
            control_plane_id=control_plane_id,
            driver=driver,
            roles_json=roles or [],
            config_json=config or {},
        )
        self.session.add(binding)
        await self.session.flush()
        return binding

    async def list_for_environment(
        self, environment_id: uuid.UUID
    ) -> list[InferenceEnvironmentBinding]:
        """Return bindings for an environment."""
        result = await self.session.execute(
            select(InferenceEnvironmentBinding).where(
                InferenceEnvironmentBinding.environment_id == environment_id
            )
        )
        return list(result.scalars().all())

    async def delete(self, binding_id: uuid.UUID) -> bool:
        """Delete a binding. Returns False if not found."""
        result = await self.session.execute(
            select(InferenceEnvironmentBinding).where(
                InferenceEnvironmentBinding.id == binding_id
            ),
        )
        binding = result.scalar_one_or_none()
        if binding is None:
            return False
        await self.session.delete(binding)
        return True


# -----------------------------------------------------------------------
# Environment node DAO
# -----------------------------------------------------------------------


class EnvironmentNodeDAO:
    """CRUD operations for environment node membership."""

    def __init__(self, session: AsyncSession = Depends(get_db_session)) -> None:
        self.session = session

    async def add_node(
        self,
        environment_id: uuid.UUID,
        node_id: uuid.UUID,
        role: EnvironmentNodeRole = EnvironmentNodeRole.WORKER,
    ) -> InferenceEnvironmentNode:
        """Add a node to an environment. (environment_id, node_id) unique."""
        membership = InferenceEnvironmentNode(
            id=uuid.uuid4(),
            environment_id=environment_id,
            node_id=node_id,
            role=role.value,
        )
        self.session.add(membership)
        await self.session.flush()
        return membership

    async def list_for_environment(
        self, environment_id: uuid.UUID
    ) -> list[InferenceEnvironmentNode]:
        """Return memberships for an environment."""
        result = await self.session.execute(
            select(InferenceEnvironmentNode).where(
                InferenceEnvironmentNode.environment_id == environment_id
            )
        )
        return list(result.scalars().all())

    async def list_for_node(
        self, node_id: uuid.UUID
    ) -> list[InferenceEnvironmentNode]:
        """Return memberships for a node (every environment it joins)."""
        result = await self.session.execute(
            select(InferenceEnvironmentNode).where(
                InferenceEnvironmentNode.node_id == node_id
            )
        )
        return list(result.scalars().all())

    async def remove_node(
        self, environment_id: uuid.UUID, node_id: uuid.UUID
    ) -> bool:
        """Remove a node from an environment. Returns False if not found."""
        result = await self.session.execute(
            select(InferenceEnvironmentNode).where(
                InferenceEnvironmentNode.environment_id == environment_id,
                InferenceEnvironmentNode.node_id == node_id,
            ),
        )
        membership = result.scalar_one_or_none()
        if membership is None:
            return False
        await self.session.delete(membership)
        return True


# -----------------------------------------------------------------------
# Deployment DAO
# -----------------------------------------------------------------------


class DeploymentDAO:
    """CRUD operations for inference deployments."""

    def __init__(self, session: AsyncSession = Depends(get_db_session)) -> None:
        self.session = session

    async def create(
        self,
        environment_id: uuid.UUID,
        model_id: uuid.UUID,
        name: str,
        spec: dict[str, Any],
        description: str | None = None,
        desired_state: DeploymentDesiredState = DeploymentDesiredState.ACTIVE,
    ) -> InferenceDeployment:
        """Create a new deployment.

        The stored ``spec_json`` is the serialized versioned spec; callers
        are expected to validate it with
        ``llm_port_backend.services.inference.schemas`` first so the
        database only ever holds well-formed documents.
        """
        deployment = InferenceDeployment(
            id=uuid.uuid4(),
            environment_id=environment_id,
            model_id=model_id,
            name=name,
            description=description,
            spec_json=spec,
            desired_state=desired_state.value,
        )
        self.session.add(deployment)
        await self.session.flush()
        return deployment

    async def get(self, deployment_id: uuid.UUID) -> InferenceDeployment | None:
        """Fetch a deployment by ID."""
        result = await self.session.execute(
            select(InferenceDeployment).where(
                InferenceDeployment.id == deployment_id
            ),
        )
        return result.scalar_one_or_none()

    async def get_by_name(self, name: str) -> InferenceDeployment | None:
        """Fetch a deployment by unique name."""
        result = await self.session.execute(
            select(InferenceDeployment).where(InferenceDeployment.name == name),
        )
        return result.scalar_one_or_none()

    async def list_all(
        self,
        *,
        environment_id: uuid.UUID | None = None,
        model_id: uuid.UUID | None = None,
        phase: DeploymentPhase | None = None,
        include_deleted: bool = False,
    ) -> list[InferenceDeployment]:
        """Return deployments, optionally filtered."""
        stmt = select(InferenceDeployment)
        if environment_id is not None:
            stmt = stmt.where(InferenceDeployment.environment_id == environment_id)
        if model_id is not None:
            stmt = stmt.where(InferenceDeployment.model_id == model_id)
        if phase is not None:
            stmt = stmt.where(InferenceDeployment.phase == phase.value)
        if not include_deleted:
            stmt = stmt.where(
                InferenceDeployment.desired_state
                != DeploymentDesiredState.DELETED.value
            )
        stmt = stmt.order_by(InferenceDeployment.created_at.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_pending_observation(self) -> list[InferenceDeployment]:
        """Return deployments whose desired state is not yet observed.

        These are the rows the reconciler must visit:
        * Rows where observed_generation lags generation (new desired state or spec change);
        * Rows in an active transition phase (pending, preparing, applying) where
          convergence is still in progress.
        Terminal rows (running, stopped, failed, deleted) whose generation is
        observed stay out of the queue so the reconciler does not busy-spin.
        """
        result = await self.session.execute(
            select(InferenceDeployment).where(
                or_(
                    InferenceDeployment.observed_generation
                    < InferenceDeployment.generation,
                    and_(
                        InferenceDeployment.phase.in_([
                            DeploymentPhase.PENDING.value,
                            DeploymentPhase.PREPARING.value,
                            DeploymentPhase.APPLYING.value,
                            DeploymentPhase.DEGRADED.value,
                        ]),
                        InferenceDeployment.desired_state
                        != DeploymentDesiredState.DELETED.value,
                    ),
                )
            )
        )
        return list(result.scalars().all())

    async def update(
        self,
        deployment_id: uuid.UUID,
        *,
        name: str | None = None,
        description: str | None = ...,
        spec: dict[str, Any] | None = ...,
        desired_state: DeploymentDesiredState | None = ...,
    ) -> InferenceDeployment | None:
        """Patch deployment fields.

        Any change to the spec or the desired state is a desired-state
        change: ``generation`` is incremented so the reconciler picks the
        update up. Cosmetic changes (name/description) do not bump the
        generation — they are identity/metadata, not workload semantics.
        """
        deployment = await self.get(deployment_id)
        if deployment is None:
            return None
        desired_changed = False
        if name is not None:
            deployment.name = name
        if description is not ...:
            deployment.description = description
        if spec is not ...:
            if spec is None:
                raise ValueError("spec must be a dict (use ... to keep the current spec)")
            deployment.spec_json = spec
            desired_changed = True
        if desired_state is not ...:
            if desired_state is None:
                raise ValueError(
                    "desired_state must be provided (use ... to keep the current "
                    "desired state)"
                )
            deployment.desired_state = desired_state.value
            desired_changed = True
        if desired_changed:
            deployment.generation += 1
        await self.session.flush()
        await self.session.refresh(deployment)
        return deployment

    async def delete(self, deployment_id: uuid.UUID) -> bool:
        """Delete a deployment. Returns False if not found."""
        deployment = await self.get(deployment_id)
        if deployment is None:
            return False
        await self.session.delete(deployment)
        return True

    async def set_observed(
        self,
        deployment_id: uuid.UUID,
        observed_generation: int,
        *,
        phase: DeploymentPhase | None = None,
        phase_message: str | None = ...,
        observed_status: dict[str, Any] | None = None,
        ready_replicas: int | None = None,
        total_replicas: int | None = None,
    ) -> InferenceDeployment | None:
        """Persist reconciler observations for a deployment."""
        deployment = await self.get(deployment_id)
        if deployment is None:
            return None
        deployment.observed_generation = observed_generation
        if phase is not None:
            deployment.phase = phase.value
        if phase_message is not ...:
            deployment.phase_message = phase_message
        if observed_status is not None:
            deployment.observed_status_json = observed_status
        if ready_replicas is not None:
            deployment.ready_replicas = ready_replicas
        if total_replicas is not None:
            deployment.total_replicas = total_replicas
        await self.session.flush()
        await self.session.refresh(deployment)
        return deployment


# -----------------------------------------------------------------------
# Endpoint DAO
# -----------------------------------------------------------------------


class EndpointDAO:
    """CRUD operations for inference endpoints."""

    def __init__(self, session: AsyncSession = Depends(get_db_session)) -> None:
        self.session = session

    async def create(
        self,
        deployment_id: uuid.UUID,
        name: str,
        address: str,
        path: str = "/v1",
        status: EndpointStatus = EndpointStatus.PENDING,
    ) -> InferenceEndpoint:
        """Create an endpoint. (deployment_id, name) must be unique."""
        endpoint = InferenceEndpoint(
            id=uuid.uuid4(),
            deployment_id=deployment_id,
            name=name,
            path=path,
            address=address,
            status=status.value,
        )
        self.session.add(endpoint)
        await self.session.flush()
        return endpoint

    async def list_for_deployment(
        self, deployment_id: uuid.UUID
    ) -> list[InferenceEndpoint]:
        """Return endpoints for a deployment."""
        result = await self.session.execute(
            select(InferenceEndpoint).where(
                InferenceEndpoint.deployment_id == deployment_id
            )
        )
        return list(result.scalars().all())

    async def update(
        self,
        endpoint_id: uuid.UUID,
        *,
        address: str | None = None,
        status: EndpointStatus | None = None,
        status_message: str | None = ...,
        published: dict[str, Any] | None = ...,
    ) -> InferenceEndpoint | None:
        """Patch endpoint fields (used by the reconciler to publish)."""
        result = await self.session.execute(
            select(InferenceEndpoint).where(InferenceEndpoint.id == endpoint_id),
        )
        endpoint = result.scalar_one_or_none()
        if endpoint is None:
            return None
        if address is not None:
            endpoint.address = address
        if status is not None:
            endpoint.status = status.value
        if status_message is not ...:
            endpoint.status_message = status_message
        if published is not ...:
            endpoint.published_json = published
        await self.session.flush()
        await self.session.refresh(endpoint)
        return endpoint

    async def delete(self, endpoint_id: uuid.UUID) -> bool:
        """Delete an endpoint. Returns False if not found."""
        result = await self.session.execute(
            select(InferenceEndpoint).where(InferenceEndpoint.id == endpoint_id),
        )
        endpoint = result.scalar_one_or_none()
        if endpoint is None:
            return False
        await self.session.delete(endpoint)
        return True


# -----------------------------------------------------------------------
# Model availability DAO
# -----------------------------------------------------------------------


class ModelAvailabilityDAO:
    """CRUD operations for per-node model artifact availability."""

    def __init__(self, session: AsyncSession = Depends(get_db_session)) -> None:
        self.session = session

    async def upsert(
        self,
        model_id: uuid.UUID,
        node_id: uuid.UUID,
        **fields: Any,
    ) -> ModelAvailability:
        """Create or update the availability row for (model, node)."""
        result = await self.session.execute(
            select(ModelAvailability).where(
                ModelAvailability.model_id == model_id,
                ModelAvailability.node_id == node_id,
            ),
        )
        row = result.scalar_one_or_none()
        if row is None:
            row = ModelAvailability(
                id=uuid.uuid4(), model_id=model_id, node_id=node_id, **fields
            )
            self.session.add(row)
        else:
            for key, value in fields.items():
                setattr(row, key, value)
        await self.session.flush()
        await self.session.refresh(row)
        return row

    async def get(
        self, model_id: uuid.UUID, node_id: uuid.UUID
    ) -> ModelAvailability | None:
        """Fetch the availability row for (model, node)."""
        result = await self.session.execute(
            select(ModelAvailability).where(
                ModelAvailability.model_id == model_id,
                ModelAvailability.node_id == node_id,
            ),
        )
        return result.scalar_one_or_none()

    async def list_for_model(
        self, model_id: uuid.UUID
    ) -> list[ModelAvailability]:
        """Return availability rows for a model (all nodes)."""
        result = await self.session.execute(
            select(ModelAvailability).where(ModelAvailability.model_id == model_id)
        )
        return list(result.scalars().all())

    async def list_for_node(
        self, node_id: uuid.UUID
    ) -> list[ModelAvailability]:
        """Return availability rows for a node (all models)."""
        result = await self.session.execute(
            select(ModelAvailability).where(ModelAvailability.node_id == node_id)
        )
        return list(result.scalars().all())

    async def list_for_nodes(
        self, model_id: uuid.UUID, node_ids: list[uuid.UUID]
    ) -> list[ModelAvailability]:
        """Return availability rows for a model across the specified nodes."""
        if not node_ids:
            return []
        result = await self.session.execute(
            select(ModelAvailability).where(
                ModelAvailability.model_id == model_id,
                ModelAvailability.node_id.in_(node_ids),
            )
        )
        return list(result.scalars().all())

    async def mark(
        self,
        model_id: uuid.UUID,
        node_id: uuid.UUID,
        status: ModelAvailabilityStatus | str,
        **fields: Any,
    ) -> ModelAvailability:
        """Update availability status and metadata for (model, node)."""
        status_val = status.value if hasattr(status, "value") else str(status)
        fields["status"] = status_val
        fields["updated_at"] = datetime.now(tz=UTC)
        if status_val == ModelAvailabilityStatus.READY.value and "ready_at" not in fields:
            fields["ready_at"] = datetime.now(tz=UTC)
        return await self.upsert(model_id, node_id, **fields)
