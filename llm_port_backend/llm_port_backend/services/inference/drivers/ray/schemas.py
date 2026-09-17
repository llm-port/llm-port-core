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


class RayProbeResult(BaseModel):
    """Structured probe output."""
    alive: bool
    version: str | None = None
    num_nodes: int = 0
    total_gpus: float = 0
    available_gpus: float = 0


class RayClusterStatus(BaseModel):
    """Parsed cluster state from GET_RAY_STATUS command."""
    alive: bool
    version: str | None = None
    num_nodes: int = 0
    nodes: list[dict[str, Any]] = []
    total_gpus: float = 0
    available_gpus: float = 0
    cluster_address: str | None = None

    @property
    def all_healthy(self) -> bool:
        """Are all nodes reported alive?"""
        return self.alive and self.num_nodes > 0

