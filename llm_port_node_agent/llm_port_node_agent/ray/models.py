"""LLM.Port-owned DTOs for the node agent's Ray integration.

These models are the wire contract between the node agent and the backend
reconciler.  They are deliberately plain ``pydantic`` (no Ray types leak
across the boundary): the SDK layer (``core.py``/``serve.py``) normalizes raw
``ray.nodes()`` / ``serve.status()`` records into these DTOs.

Tier A (always available, Dashboard-independent):
    ``RayEnvironmentStatus`` — cluster tier only: alive, nodes, resources,
    capabilities, serve tier.

Tier B/C (optional, never gate Tier A health):
    ``serve`` sub-structure (Serve Python API), ``metrics`` scrape targets
    (Tier C), ``state`` diagnostics (Tier B, ``ray.util.state``).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class RayNodeStatus(BaseModel):
    """One alive or dead cluster node, normalized from ``ray.nodes()``."""

    node_id: str
    node_ip: str = ""
    node_manager_address: str = ""
    node_manager_port: int | None = None
    node_name: str | None = None
    alive: bool = False
    is_head: bool = False
    ray_version: str | None = None
    #: Total resources registered by this node (CPU/GPU/accelerator-type/...),
    #: normalized ``float`` for numeric entries.
    resources: dict[str, float] = Field(default_factory=dict)
    #: Prometheus metrics export port for this node (Tier C).  ``0``/``-1``
    #: mean the metrics agent did not open a port (metrics disabled).
    metrics_export_port: int | None = None


class RayResourceTotals(BaseModel):
    """Cluster-wide resource totals from ``ray.cluster_resources()``."""

    cpu: float = 0.0
    gpu: float = 0.0
    memory: float = 0.0
    object_store_memory: float = 0.0
    #: Accelerator + custom resource labels (e.g. ``accelerator_type:TITAN-RTX``).
    accelerators: dict[str, float] = Field(default_factory=dict)
    #: Everything else (node:<ip>, custom labels) preserved verbatim.
    other: dict[str, float] = Field(default_factory=dict)


class RayAvailableResources(BaseModel):
    """Currently schedulable resources from ``ray.available_resources()``."""

    cpu: float = 0.0
    gpu: float = 0.0
    memory: float = 0.0
    object_store_memory: float = 0.0
    accelerators: dict[str, float] = Field(default_factory=dict)


class RayMetricsTargets(BaseModel):
    """Tier C: Prometheus scrape targets discovered via ``ray.nodes()``."""

    enabled: bool = False
    #: ``http://<NodeManagerAddress>:<MetricsExportPort>/metrics`` per node.
    targets: list[dict[str, Any]] = Field(default_factory=list)


class RayReplicaState(BaseModel):
    """One replica-state bucket (``ReplicaState`` -> count) for a deployment."""

    state: str
    count: int = 0


class RayDeploymentStatus(BaseModel):
    """One deployment, normalized from ``serve.status()``."""

    name: str
    status: str = ""
    status_trigger: str | None = None
    message: str = ""
    replica_states: list[RayReplicaState] = Field(default_factory=list)
    num_replicas_ready: int = 0
    num_replicas_pending: int = 0


class RayApplicationStatus(BaseModel):
    """One Serve application, normalized from ``serve.status()``."""

    name: str
    status: str = ""
    message: str = ""
    last_deployed_time_s: float | None = None
    healthy: bool | None = None
    deployments: dict[str, RayDeploymentStatus] = Field(default_factory=dict)


class RayServeStatusTier(BaseModel):
    """Serve tier.  ``available`` is False when Serve is not up / no apps yet;
    this NEVER fails the overall cluster health (Tier A is independent)."""

    available: bool = False
    active: bool = False
    apps: dict[str, RayApplicationStatus] = Field(default_factory=dict)
    #: Error message when Serve status could not be obtained.
    detail: str | None = None


class RayStateCapability(BaseModel):
    """Tier B (optional) ``ray.util.state`` availability — never gates health."""

    available: bool = False
    detail: str | None = None


class RayCapabilities(BaseModel):
    """Component capability + tier flags reported on every status response."""

    #: SDK attach succeeded and a GCS round-trip answered.
    cluster_sdk: bool = False
    #: Ray Serve Python API importable + Serve reachable.
    serve: bool = False
    #: ``ray.util.state`` importable AND Dashboard component reachable (Tier B).
    state: bool = False
    #: Metrics export ports present on nodes (Tier C).
    metrics: bool = False


class RayEnvironmentStatus(BaseModel):
    """The enriched ``GET_RAY_STATUS`` result (and ``GET_RAY_SERVE_STATUS``).

    The flat top-level fields (``alive``/``version``/``num_nodes``/``nodes``/
    ``total_gpus``/``available_gpus``/``cluster_address``) are preserved so the
    existing backend parser keeps working unchanged; the new structured fields
    (``nodes`` DTO, ``resources``/``available`` totals, ``serve``,
    ``metrics``, ``capabilities``) are additive.
    """

    alive: bool = False
    version: str | None = None
    num_nodes: int = 0
    #: Normalized node records (backwards-compatible dict shape with added
    #: ``ray_version`` + ``metrics_export_port`` keys).
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    # --- structured totals (additive) ---
    resources: RayResourceTotals | None = None
    available: RayAvailableResources | None = None
    total_gpus: float = 0.0
    available_gpus: float = 0.0
    total_cpus: float = 0.0
    cluster_address: str | None = None
    #: Head node IP/GCS address for diagnostics.
    head_address: str | None = None
    # --- tier flags + optional tiers ---
    capabilities: RayCapabilities = Field(default_factory=RayCapabilities)
    serve: RayServeStatusTier | None = None
    metrics: RayMetricsTargets | None = None
    state: RayStateCapability | None = None
