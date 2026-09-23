"""Neutral inference domain models (Ray-first, vendor-agnostic).

These models support the migration from the legacy single-node
``LLMRuntime`` model to a managed multi-replica inference model:

* ``InferenceControlPlane`` — a logical backend system (e.g. a Ray cluster
  control plane) that can host inference environments.
* ``InferenceEnvironment`` — a managed compute environment (e.g. a Ray
  cluster formed from enrolled infra nodes).
* ``InferenceEnvironmentBinding`` — the backend-specific binding that
  implements the environment's roles (orchestration, replica routing, ...).
* ``InferenceEnvironmentNode`` — desired membership of an infra node in an
  environment (head or worker).
* ``InferenceDeployment`` — desired deployment of an ``LLMModel`` into an
  environment, expressed through a versioned spec document.
* ``InferenceEndpoint`` — a logical endpoint exposed for a ready
  deployment; published to the gateway as a single logical upstream.
* ``ModelAvailability`` — per-node readiness of a model's artifacts.

The legacy ``llm_runtimes`` / ``infra_node_workload_assignment`` tables are
untouched; both models coexist until the native path is retired.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from llm_port_backend.db.base import Base

# ---------------------------------------------------------------------------
# Shared vocabulary
# ---------------------------------------------------------------------------


class ControlPlaneStatus(enum.StrEnum):
    """High-level health of a backend control plane."""

    PENDING = "pending"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    DEGRADED = "degraded"
    FAILED = "failed"
    DISABLED = "disabled"


class EnvironmentStatus(enum.StrEnum):
    """Observed state of a managed inference environment."""

    PENDING = "pending"
    PREPARING = "preparing"
    RUNNING = "running"
    READY = "ready"
    DEGRADED = "degraded"
    FAILED = "failed"
    STOPPED = "stopped"


class EnvironmentDesiredState(enum.StrEnum):
    """Desired state of a managed environment."""

    RUNNING = "running"
    STOPPED = "stopped"
    DELETED = "deleted"


class EnvironmentNodeRole(enum.StrEnum):
    """Role of a node within a managed environment."""

    HEAD = "head"
    WORKER = "worker"


class InferenceEngine(enum.StrEnum):
    """Known local inference engines deployable through the new domain."""

    VLLM = "vllm"
    SGLANG = "sglang"
    TGI = "tgi"
    TRTLLM = "trtllm"
    LLAMA_CPP = "llama_cpp"
    OTHER = "other"


class DeploymentPhase(enum.StrEnum):
    """Observed lifecycle phase of a deployment."""

    PENDING = "pending"
    PREPARING = "preparing"
    APPLYING = "applying"
    RUNNING = "running"
    DEGRADED = "degraded"
    STOPPED = "stopped"
    FAILED = "failed"
    DELETED = "deleted"


class DeploymentDesiredState(enum.StrEnum):
    """Desired lifecycle state of a deployment."""

    ACTIVE = "active"
    STOPPED = "stopped"
    DELETED = "deleted"


class EndpointStatus(enum.StrEnum):
    """Lifecycle of a logical inference endpoint."""

    PENDING = "pending"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    UNPUBLISHING = "unpublishing"
    DEGRADED = "degraded"
    FAILED = "failed"
    RETIRED = "retired"


class ModelAvailabilityStatus(enum.StrEnum):
    """Artifact readiness of a model on a node."""

    UNKNOWN = "unknown"
    PENDING = "pending"
    SYNCING = "syncing"
    READY = "ready"
    STALE = "stale"
    MISSING = "missing"
    FAILED = "failed"


class ModelSourceKind(enum.StrEnum):
    """How model artifacts are expected to be present on a node."""

    SYNCED = "synced"
    PRE_EXISTING = "pre_existing"
    REMOTE = "remote"


def _sa_enum(enum_cls: type[enum.StrEnum], name: str) -> Any:
    """Build an SAEnum from a StrEnum using lowercase values."""
    from sqlalchemy import Enum as SAEnum  # noqa: PLC0415

    return SAEnum(
        enum_cls,
        name=name,
        create_type=False,
        values_callable=lambda e: [m.value for m in e],
    )


# ---------------------------------------------------------------------------
# Control plane
# ---------------------------------------------------------------------------


class InferenceControlPlane(Base):
    """A backend system (e.g. Ray) that hosts inference environments.

    ``credential_ref`` is an opaque reference to encrypted credential
    material managed outside these tables.  Raw credentials (for example a
    Ray auth token) must never be stored in plain columns or node-command
    payloads.
    """

    __tablename__ = "inference_control_planes"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    driver: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        _sa_enum(ControlPlaneStatus, "inference_control_plane_status"),
        nullable=False,
        default=ControlPlaneStatus.PENDING.value,
        index=True,
    )
    config_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    observed_status_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    credential_ref: Mapped[str | None] = mapped_column(String(256), nullable=True)
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    observed_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------


class InferenceEnvironment(Base):
    """A managed inference environment (e.g. a Ray cluster)."""

    __tablename__ = "inference_environments"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    control_plane_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("inference_control_planes.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        _sa_enum(EnvironmentStatus, "inference_environment_status"),
        nullable=False,
        default=EnvironmentStatus.PENDING.value,
        index=True,
    )
    desired_state: Mapped[str] = mapped_column(
        _sa_enum(EnvironmentDesiredState, "inference_environment_desired_state"),
        nullable=False,
        default=EnvironmentDesiredState.RUNNING.value,
    )
    # The backend's own version string, written by whichever driver owns
    # the environment.  Not `ray_version`: a Dynamo or exo environment has
    # no Ray, and this column is part of the neutral domain.
    runtime_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    head_node_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("infra_node.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    config_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    capabilities_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    observed_status_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    observed_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class InferenceEnvironmentBinding(Base):
    """Backend binding that implements an environment's roles.

    Ray v1 typically uses a single binding owning
    ``orchestration``, ``replica_routing``, ``environment_management`` and
    ``observability``.  The table keeps the core model from assuming that
    one backend must own every inference role.
    """

    __tablename__ = "inference_environment_bindings"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    environment_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("inference_environments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    control_plane_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("inference_control_planes.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    driver: Mapped[str] = mapped_column(String(64), nullable=False)
    roles_json: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    config_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (UniqueConstraint("environment_id", "driver", name="uq_env_binding_env_driver"),)


class InferenceComputePool(Base):
    """A scheduling-compatible group of nodes inside an environment.

    A pool is not new information: it is the *persisted equivalence class* of
    nodes under the same compatibility rules a runtime bundle is matched with
    (vendor, CPU architecture, accelerator family).  Persisting it is what
    lets a mixed-vendor cluster -- NVIDIA here, ROCm there, an Apple/exo group
    tomorrow -- say which machines are interchangeable, instead of the cluster
    being the only grouping and therefore implicitly homogeneous.

    Derived on membership so nothing has to be configured; ``managed`` marks a
    pool an operator has taken over, which derivation then leaves alone.
    """

    __tablename__ = "inference_compute_pools"
    __table_args__ = (
        UniqueConstraint("environment_id", "name", name="uq_compute_pool_env_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    environment_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("inference_environments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    #: Stable identity of the compatibility class, e.g. "nvidia/aarch64/gb10".
    #: Derivation matches on this, never on the display name.
    signature: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    accelerator_vendor: Mapped[str] = mapped_column(String(64), nullable=False)
    accelerator_family: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cpu_architecture: Mapped[str] = mapped_column(String(32), nullable=False)
    #: Free-form scheduling hints an operator adds; never written by derivation.
    labels_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    #: True once an operator edits the pool, which stops derivation renaming it.
    managed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class InferenceEnvironmentNode(Base):
    """Desired membership of an infra node in an environment."""

    __tablename__ = "inference_environment_nodes"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    environment_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("inference_environments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    node_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("infra_node.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(
        _sa_enum(EnvironmentNodeRole, "inference_environment_node_role"),
        nullable=False,
        default=EnvironmentNodeRole.WORKER.value,
    )
    # What the backend reports about this member (Ray: alive/dead).  Named
    # for the domain, not for one backend's vocabulary.
    member_status: Mapped[str | None] = mapped_column(String(64), nullable=True)
    compute_pool_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("inference_compute_pools.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    observed_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    joined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (UniqueConstraint("environment_id", "node_id", name="uq_env_node_env_node"),)


# ---------------------------------------------------------------------------
# Deployment
# ---------------------------------------------------------------------------


class InferenceDeployment(Base):
    """Desired deployment of a model into an environment.

    The portable deployment contract lives in ``spec_json`` and is
    validated with the versioned Pydantic schemas in
    ``llm_port_backend.services.inference.schemas``.  Identity fields
    (``model_id``, ``environment_id``, ``name``, ``desired_state``) are
    kept in indexed columns so reconciliation queries stay efficient.

    ``generation`` increments on every desired-state change; the
    reconciler persists ``observed_generation`` once an observation is
    accepted.
    """

    __tablename__ = "inference_deployments"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    environment_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("inference_environments.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    model_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("llm_models.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False, unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    spec_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    desired_state: Mapped[str] = mapped_column(
        _sa_enum(DeploymentDesiredState, "inference_deployment_desired_state"),
        nullable=False,
        default=DeploymentDesiredState.ACTIVE.value,
        index=True,
    )
    phase: Mapped[str] = mapped_column(
        _sa_enum(DeploymentPhase, "inference_deployment_phase"),
        nullable=False,
        default=DeploymentPhase.PENDING.value,
        index=True,
    )
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    observed_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    observed_status_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    phase_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    ready_replicas: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_replicas: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


class InferenceEndpoint(Base):
    """Logical endpoint published for a ready deployment.

    Ray replicas are never published individually; the gateway sees a
    single candidate per endpoint.
    """

    __tablename__ = "inference_endpoints"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    deployment_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("inference_deployments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    path: Mapped[str] = mapped_column(String(256), nullable=False, default="/v1")
    address: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        _sa_enum(EndpointStatus, "inference_endpoint_status"),
        nullable=False,
        default=EndpointStatus.PENDING.value,
        index=True,
    )
    status_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (UniqueConstraint("deployment_id", "name", name="uq_endpoint_deploy_name"),)


# ---------------------------------------------------------------------------
# Taking over vLLM a machine already runs (Phase 8)
# ---------------------------------------------------------------------------


class AdoptionState(enum.StrEnum):
    """Where taking over a found vLLM container stands.

    ``routed`` and ``released`` are routing it as it is (8.2). The rest are
    moving it into a cluster (8.3): a deployment is started beside it, checked
    with a real request, and the name moves over, with a way back.
    """

    ROUTED = "routed"
    RELEASED = "released"
    DEPLOYING = "deploying"
    VERIFIED = "verified"
    VERIFY_FAILED = "verify_failed"
    SWITCHED = "switched"
    SWITCHED_BACK = "switched_back"
    FINISHED = "finished"
    ABANDONED = "abandoned"


#: States in which LLM.Port routes, or is moving, the container: one per container.
OPEN_ADOPTION_STATES = frozenset({
    AdoptionState.ROUTED.value,
    AdoptionState.DEPLOYING.value,
    AdoptionState.VERIFIED.value,
    AdoptionState.VERIFY_FAILED.value,
    AdoptionState.SWITCHED.value,
    AdoptionState.SWITCHED_BACK.value,
})


class InferenceAdoption(Base):
    """A vLLM container LLM.Port found on a machine and took over.

    Found by the agent (``vllm_containers`` in the inventory), not started by
    LLM.Port. Its own table rather than columns on a provider or deployment:
    the reconciler rewrites a deployment's observed state every pass, and a
    second writer there would lose updates. Foreign keys are ``SET NULL`` so
    the record outlives the machine, the provider and the deployment.
    """

    __tablename__ = "inference_adoptions"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    node_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("infra_node.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    container_name: Mapped[str] = mapped_column(String(256), nullable=False)
    #: The name clients call it by at the gateway.
    alias: Mapped[str] = mapped_column(String(256), nullable=False, index=True)
    #: The model name the container itself answers to.
    served_model_name: Mapped[str] = mapped_column(String(512), nullable=False)
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    task: Mapped[str | None] = mapped_column(String(32), nullable=True)
    provider_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("llm_providers.id", ondelete="SET NULL"),
        nullable=True,
    )
    deployment_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("inference_deployments.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, default=AdoptionState.ROUTED.value, index=True
    )
    detail_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_by: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True)
    routed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    switched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    switched_back_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )


# ---------------------------------------------------------------------------
# Model availability
# ---------------------------------------------------------------------------


class ModelAvailability(Base):
    """Per-node artifact readiness for a logical model.

    Uniqueness is ``(model_id, node_id)``.  If a future product semantic
    allows multiple revisions of one logical model to coexist on a node,
    ``revision``/manifest identity must be folded into the key before the
    constraint is trusted for reconciliation.
    """

    __tablename__ = "model_availability"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    model_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("llm_models.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    node_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("infra_node.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        _sa_enum(ModelAvailabilityStatus, "model_availability_status"),
        nullable=False,
        default=ModelAvailabilityStatus.UNKNOWN.value,
        index=True,
    )
    source_kind: Mapped[str] = mapped_column(
        _sa_enum(ModelSourceKind, "model_availability_source_kind"),
        nullable=False,
        default=ModelSourceKind.SYNCED.value,
    )
    revision: Mapped[str | None] = mapped_column(String(256), nullable=True)
    manifest_sha256: Mapped[str | None] = mapped_column(String(128), nullable=True)
    root_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    progress: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    status_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (UniqueConstraint("model_id", "node_id", name="uq_model_availability_model_node"),)
