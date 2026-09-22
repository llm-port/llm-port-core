"""Pydantic schemas for the /inference API.

These are request/response DTOs only.  The versioned deployment spec
(``InferenceDeploymentSpecV1Alpha1``) lives in
``llm_port_backend.services.inference.schemas`` and is exposed through the
``spec`` field of the deployment create/update bodies.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from llm_port_backend.services.inference.planner import InferenceEnvironmentPlan

# ---------------------------------------------------------------------------
# Control planes
# ---------------------------------------------------------------------------


class ControlPlaneCreate(BaseModel):
    """Request body for creating an inference control plane."""

    name: str = Field(..., min_length=1, max_length=128)
    driver: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Driver key (e.g. 'ray', 'vllm'). The driver must be "
        "registered before a control plane using it can be reconciled.",
    )
    description: str | None = Field(None, max_length=1024)
    config: dict[str, Any] | None = Field(
        None, description="Driver-specific config (opaque JSON)."
    )
    credential_ref: str | None = Field(
        None,
        max_length=256,
        description="Opaque reference to credential material managed outside the DB.",
    )
    enabled: bool = Field(
        True, description="Whether the control plane is usable for new work."
    )


class ControlPlaneUpdate(BaseModel):
    """Request body for patching an inference control plane.

    Fields omitted from the request body are left unchanged.  To explicitly
    clear a nullable field, send ``null``.
    """

    name: str | None = Field(None, min_length=1, max_length=128)
    description: str | None = Field(None, max_length=1024)
    config: dict[str, Any] | None = None
    credential_ref: str | None = Field(None, max_length=256)
    enabled: bool | None = None


class ControlPlaneDTO(BaseModel):
    """Control plane representation returned by the API."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID
    name: str
    driver: str
    description: str | None
    status: str
    config: dict[str, Any] = Field(validation_alias="config_json")
    observed_status: dict[str, Any] = Field(
        validation_alias="observed_status_json"
    )
    credential_ref: str | None
    generation: int
    observed_generation: int
    status_message: str | None
    enabled: bool
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Environments
# ---------------------------------------------------------------------------


class EnvironmentCreate(BaseModel):
    """Request body for creating an inference environment."""

    control_plane_id: uuid.UUID
    name: str = Field(..., min_length=1, max_length=128)
    description: str | None = Field(None, max_length=1024)
    runtime_version: str | None = Field(
        None,
        max_length=64,
        description="Optional Ray version pin (used by the Ray driver).",
    )
    head_node_id: uuid.UUID | None = Field(
        None, description="Optional preferred head node."
    )
    address: str | None = Field(
        None, max_length=1024, description="Optional head address."
    )
    config: dict[str, Any] | None = Field(
        None, description="Backend-specific environment config (opaque JSON)."
    )


class EnvironmentUpdate(BaseModel):
    """Request body for patching an inference environment.

    Fields omitted from the request body are left unchanged.  To explicitly
    clear a nullable field, send ``null``.
    """

    description: str | None = Field(None, max_length=1024)
    desired_state: str | None = Field(
        None,
        description="Desired state: 'running' | 'stopped'. Omit to keep current.",
    )
    runtime_version: str | None = Field(None, max_length=64)
    head_node_id: uuid.UUID | None = None
    address: str | None = Field(None, max_length=1024)
    config: dict[str, Any] | None = None


class EnvironmentNodeAdd(BaseModel):
    """Request body for adding a node to an environment (Phase 1: desired state only)."""

    node_id: uuid.UUID
    role: str = Field(
        "worker",
        description="Node role: 'head' | 'worker'.",
    )

class RuntimeBundleDTO(BaseModel):
    """A certified runtime image, and the machines it will be used on.

    Not a choice an operator makes.  The image is decided by the machine: it
    is built for one CPU architecture and one accelerator generation, so on a
    cluster spanning two platforms there is no single answer, and the answer
    that used to be pinned on the cluster was pushed to every node including
    the ones it could not run on.

    This is therefore a readout -- "these machines will run this image" --
    shown so an operator can see what they are about to start, and see when a
    machine they picked has nothing to start it with.
    """

    model_config = ConfigDict(extra="forbid")

    bundle_id: str
    display_name: str
    description: str = ""
    image: str
    cpu_architecture: str
    accelerator_vendor: str
    runtime_version: str
    vllm_version: str
    certification_status: str
    #: Node ids that *resolve* to this bundle -- the ones it will actually
    #: be used on -- and, for the rest, why it will not be.
    compatible_node_ids: list[uuid.UUID] = Field(default_factory=list)
    incompatible: dict[str, str] = Field(default_factory=dict)


class ComputePoolDTO(BaseModel):
    """A scheduling-compatible group of machines inside a cluster.

    Derived from what the nodes report, so nothing has to be configured for a
    single-vendor cluster; it becomes load-bearing the moment a cluster mixes
    accelerators, which is what keeps the architecture open to AMD, Intel or
    an Apple/exo group later.
    """

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID
    environment_id: uuid.UUID
    name: str
    #: Identity of the compatibility class; matching is on this, not the name.
    signature: str
    accelerator_vendor: str
    accelerator_family: str | None = None
    cpu_architecture: str
    labels: dict[str, Any] = Field(default_factory=dict, validation_alias="labels_json")
    #: True once an operator edits it, which stops derivation renaming it.
    managed: bool = False
    #: How many of the cluster's machines sit in this pool.
    member_count: int = 0


class EnvironmentNodeDTO(BaseModel):
    """One node's membership in an environment.

    Phase 6 requires the deployment detail view to name the participating
    nodes; the membership rows were only writable before this, so a caller
    could add a node and never see it again.
    """

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    node_id: uuid.UUID
    role: str
    member_status: str | None = None
    compute_pool_id: uuid.UUID | None = None
    observed: dict[str, Any] = Field(
        default_factory=dict, validation_alias="observed_json"
    )
    joined_at: datetime | None = None


class ApplyPlanRequest(BaseModel):
    """Request body for applying an approved environment interconnect plan.

    The plan document is an **approval receipt**, not an instruction: the
    server re-derives the plan from the live node inventory and applies the
    candidate it computed itself.  The receipt is used only to assert the
    approval was for this environment and to detect inventory that moved since
    the plan was shown to the operator.
    """

    plan: InferenceEnvironmentPlan | None = Field(
        None,
        description=(
            "The plan document that was approved. Used for stale-plan detection and "
            "environment binding only; node bindings are always re-derived server-side. "
            "Omitting it applies the current server-side recommendation without staleness "
            "protection."
        ),
    )
    selected_candidate_id: str | None = Field(
        None,
        description="Optional explicit candidate ID to apply. If omitted, the recommended candidate is applied.",
    )


class EnvironmentDTO(BaseModel):
    """Environment representation returned by the API."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID
    control_plane_id: uuid.UUID
    name: str
    description: str | None
    status: str
    desired_state: str
    runtime_version: str | None
    head_node_id: uuid.UUID | None
    address: str | None
    config: dict[str, Any] = Field(validation_alias="config_json")
    capabilities: dict[str, Any] = Field(
        validation_alias="capabilities_json"
    )
    observed_status: dict[str, Any] = Field(
        validation_alias="observed_status_json"
    )
    generation: int
    observed_generation: int
    status_message: str | None
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Deployments
# ---------------------------------------------------------------------------


class DeploymentCreate(BaseModel):
    """Request body for creating an inference deployment."""

    environment_id: uuid.UUID
    model_id: uuid.UUID
    name: str = Field(..., min_length=1, max_length=128)
    spec: dict[str, Any] = Field(
        ..., description="Versioned deployment spec document (v1alpha1)."
    )
    description: str | None = Field(None, max_length=1024)


class DeploymentUpdate(BaseModel):
    """Request body for patching an inference deployment.

    Fields omitted from the request body are left unchanged.  To change the
    spec, send a full replacement (the v1alpha1 spec is not partially patchable
    in Phase 1).
    """

    description: str | None = Field(None, max_length=1024)
    spec: dict[str, Any] | None = Field(
        None,
        description="Full replacement versioned spec document. Omit to keep current.",
    )
    desired_state: str | None = Field(
        None,
        description="Desired state: 'active' | 'stopped' | 'deleted'. Omit to keep current.",
    )


class DeploymentDTO(BaseModel):
    """Deployment representation returned by the API."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID
    environment_id: uuid.UUID
    model_id: uuid.UUID
    name: str
    description: str | None
    spec: dict[str, Any] = Field(validation_alias="spec_json")
    desired_state: str
    phase: str
    generation: int
    observed_generation: int
    observed_status: dict[str, Any] = Field(
        validation_alias="observed_status_json"
    )
    phase_message: str | None
    ready_replicas: int
    total_replicas: int
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Reconcile
# ---------------------------------------------------------------------------


class ReconcileReport(BaseModel):
    """Report from a reconcile endpoint.

    Phase 1 reconcile endpoints are honest stubs: ``reconciled`` is
    :data:`False` and ``reason`` explains why.  Once a driver is registered
    (Phase 2+) ``reconciled`` becomes :data:`True` and the payload reflects
    the backend's actual state change.
    """

    reconciled: bool
    reason: str | None = None
    extra: dict[str, Any] | None = None


class EndpointDTO(BaseModel):
    """Logical endpoint representation returned by the API."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    id: uuid.UUID
    deployment_id: uuid.UUID
    name: str
    path: str
    address: str
    status: str
    status_message: str | None = None
    published: dict[str, Any] = Field(default_factory=dict, validation_alias="published_json")
    created_at: datetime
    updated_at: datetime
