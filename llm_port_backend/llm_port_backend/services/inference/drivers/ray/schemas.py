"""Backend-side schemas for Ray driver config and status."""

from typing import Any
from pydantic import BaseModel


class RayEnvironmentConfig(BaseModel):
    """Configuration stored in InferenceEnvironment.config_json."""
    ray_version: str = "2.58.0"
    head_port: int = 6379
    dashboard_port: int = 8265
    dashboard_host: str = "127.0.0.1"
    object_store_memory: int | None = None
    extra_ray_start_args: dict[str, str] = {}
    node_env_vars: dict[str, str] = {}
    serve_proxy_location: str = "HeadOnly"
    serve_http_host: str | None = None
    serve_http_port: int = 8000


class RayProbeResult(BaseModel):
    """Structured probe output."""
    alive: bool
    version: str | None = None
    num_nodes: int = 0
    total_gpus: float = 0
    available_gpus: float = 0


class RayClusterStatus(BaseModel):
    """Parsed cluster state from GET_RAY_STATUS command.

    The agent answers with the enriched ``RayEnvironmentStatus`` (SDK-first,
    Dashboard-independent): the flat tier (Tier A) is what maps environment
    health; ``serve`` / ``metrics`` / ``state`` / ``capabilities`` are
    additive tiers — an unhealthy tier never flips cluster health.  New
    fields default so a result from an older agent still parses.
    """
    alive: bool
    version: str | None = None
    num_nodes: int = 0
    nodes: list[dict[str, Any]] = []
    total_gpus: float = 0
    available_gpus: float = 0
    cluster_address: str | None = None
    # Enriched flat tier (still Tier A — part of cluster health).
    total_cpus: float = 0
    head_address: str | None = None
    # Additive tiers (non-gating; best-effort parse).
    capabilities: dict[str, Any] = {}
    serve: dict[str, Any] | None = None
    metrics: dict[str, Any] | None = None
    state: dict[str, Any] | None = None

    @property
    def all_healthy(self) -> bool:
        """Are all nodes reported alive?"""
        return self.alive and self.num_nodes > 0

    @property
    def serve_available(self) -> bool:
        """Whether the Serve control plane reported itself available."""
        return bool((self.serve or {}).get("available", False))

    @property
    def metrics_enabled(self) -> bool:
        """Whether Prometheus scrape targets were discovered (Tier C)."""
        return bool((self.metrics or {}).get("enabled", False))

