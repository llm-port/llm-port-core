"""Versioned Pydantic schemas for the neutral inference domain.

``InferenceDeploymentSpecV1Alpha1`` is the portable, vendor-agnostic
deployment contract stored in ``inference_deployments.spec_json``.  It is
intentionally Ray-agnostic: the Ray compiler (a later phase) maps these
fields onto the Ray Serve declarative document.  Identity fields
(``model_id``, ``environment_id``, ``name``, ``desired_state``) are
persisted as indexed ORM columns, not inside the spec.

Every consumer of ``spec_json`` MUST validate it through
:func:`parse_inference_deployment_spec`; do not treat the stored JSON as
an arbitrary, unchecked dictionary.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

API_VERSION_V1ALPHA1 = "inference.llmport.ai/v1alpha1"


class EngineSpec(BaseModel):
    """Which inference engine to run and its engine-specific options."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(default="vllm", min_length=1, description="Engine name, e.g. 'vllm'.")
    config: dict[str, Any] = Field(
        default_factory=dict,
        description="Engine-specific kwargs (maps to engine_kwargs / LLMConfig.engine).",
    )


class AutoscaleSpec(BaseModel):
    """Optional autoscaling parameters for a deployment."""

    model_config = ConfigDict(extra="forbid")

    min_replicas: int = Field(..., ge=0)
    max_replicas: int = Field(..., ge=1)
    target_utilization: float | None = Field(
        None, ge=0.0, le=1.0, description="Target engine utilization (e.g. 0.9).",
    )
    metrics: str | None = Field(None, description="Optional engine metric to autoscale on.")
    scale_up_timeout: float | None = Field(None, ge=0, description="Seconds before scale-up.")
    scale_down_timeout: float | None = Field(None, ge=0, description="Seconds before scale-down.")


class ScaleSpec(BaseModel):
    """Replica scaling model: fixed replicas and/or autoscaling bounds."""

    model_config = ConfigDict(extra="forbid")

    replicas: int | None = Field(None, ge=1, description="Fixed replica count.")
    autoscale: AutoscaleSpec | None = None

    @model_validator(mode="after")
    def _require_a_scaling_mode(self) -> AutoscaleSpec | None:
        if self.replicas is None and self.autoscale is None:
            msg = "Exactly one of 'replicas' or 'autoscale' is required."
            raise ValueError(msg)
        if self.replicas is not None and self.autoscale is not None:
            msg = "'replicas' and 'autoscale' are mutually exclusive."
            raise ValueError(msg)
        return self


class ReplicaResources(BaseModel):
    """Per-replica resource requests."""

    model_config = ConfigDict(extra="forbid")

    gpus: float | None = Field(None, ge=0, description="GPUs per replica (fractional allowed).")
    cpu: float | None = Field(None, gt=0, description="CPU cores per replica.")
    memory: str | None = Field(None, description="Memory per replica, e.g. '32Gi'.")
    accelerator: str | None = Field(None, description="Accelerator type, e.g. 'A100'.")


class ResourceSpec(BaseModel):
    """Resource requirements for the deployment."""

    model_config = ConfigDict(extra="forbid")

    replica: ReplicaResources = Field(default_factory=ReplicaResources)
    placement: str | None = Field(None, description="Optional placement group strategy name.")


class TopologySpec(BaseModel):
    """Intra- and inter-node tensor/pipeline parallelism layout."""

    model_config = ConfigDict(extra="forbid")

    tensor_parallel_size: int | None = Field(None, ge=1, description="TP size per replica.")
    pipeline_parallel_size: int | None = Field(None, ge=1, description="PP size per replica.")
    data_parallel_size: int | None = Field(None, ge=1)
    nodes: int | None = Field(None, ge=1, description="Desired number of nodes in the topology.")


class ObjectiveSpec(BaseModel):
    """Optimization objective hints (informational in v1alpha1)."""

    model_config = ConfigDict(extra="forbid")

    optimize_for: str = Field(
        default="throughput",
        description="Optimization preference: 'throughput' | 'latency' | custom.",
    )
    weights: dict[str, float] = Field(default_factory=dict)


class ReplicaRoutingSpec(BaseModel):
    """Replica / request routing preference inside the backend."""

    model_config = ConfigDict(extra="forbid")

    strategy: str = Field(
        default="default",
        description="Routing strategy: 'default', 'prefix_affinity', ...",
    )
    kv_aware: bool = Field(
        default=False,
        description="Opt-in KV-aware routing (capability-gated, advanced).",
    )


class ArtifactDeliverySpec(BaseModel):
    """How model artifacts should be made available to the environment."""

    model_config = ConfigDict(extra="forbid")

    source: str = Field(
        default="sync",
        description="'sync' (LLM.Port-managed), 'local_path' (pre-existing), or 'remote'.",
    )
    root_path: str | None = Field(None, description="Required when source='local_path'.")
    revision: str | None = Field(None, description="Optional model revision to sync.")


class ServiceSpec(BaseModel):
    """Service exposure options for a ready deployment."""

    model_config = ConfigDict(extra="forbid")

    port: int | None = Field(None, ge=1, le=65535)
    path: str = Field(default="/v1", description="OpenAI-compatible path prefix.")
    openai: bool = Field(default=True, description="Expose an OpenAI-compatible API.")
    #: The name this model is offered under in chat and at the gateway.
    #:
    #: Publication reads ``service.alias`` to decide what to register, and
    #: deliberately does not invent one from the deployment name.  The field
    #: was missing here while the schema forbids extras, so it could never be
    #: set: every deployment published a provider instance with no alias, and
    #: a successfully served model never appeared in the chat model list.
    alias: str | None = Field(
        default=None,
        max_length=128,
        description="Name to offer this model under in chat; omit to publish no alias.",
    )


class InferenceDeploymentSpecV1Alpha1(BaseModel):
    """Versioned, portable deployment spec (stored in ``spec_json``)."""

    model_config = ConfigDict(extra="forbid")

    api_version: Literal["inference.llmport.ai/v1alpha1"] = API_VERSION_V1ALPHA1
    engine: EngineSpec = Field(default_factory=EngineSpec)
    scale: ScaleSpec
    resources: ResourceSpec = Field(default_factory=ResourceSpec)
    topology: TopologySpec = Field(default_factory=TopologySpec)
    objective: ObjectiveSpec = Field(default_factory=ObjectiveSpec)
    replica_routing: ReplicaRoutingSpec = Field(default_factory=ReplicaRoutingSpec)
    artifacts: ArtifactDeliverySpec = Field(default_factory=ArtifactDeliverySpec)
    service: ServiceSpec = Field(default_factory=ServiceSpec)
    extensions: dict[str, Any] = Field(default_factory=dict)


KnownSpecVersions: dict[str, type[InferenceDeploymentSpecV1Alpha1]] = {
    API_VERSION_V1ALPHA1: InferenceDeploymentSpecV1Alpha1,
}


def parse_inference_deployment_spec(data: dict[str, Any]) -> InferenceDeploymentSpecV1Alpha1:
    """
    Validate an arbitrary spec document against a known spec version.

    :param data: raw spec document (e.g. ``spec_json``).
    :return: the validated, versioned spec model.
    :raises ValueError: if the document is empty or lacks ``api_version``.
    :raises pydantic.ValidationError: if the version is unknown or invalid.
    """
    if not data:
        msg = "spec must be a non-empty object"
        raise ValueError(msg)
    api_version = data.get("api_version")
    model_cls = KnownSpecVersions.get(api_version) if isinstance(api_version, str) else None
    if model_cls is None:
        msg = f"unsupported or missing api_version: {api_version!r}"
        raise ValueError(msg)
    return model_cls.model_validate(data)
