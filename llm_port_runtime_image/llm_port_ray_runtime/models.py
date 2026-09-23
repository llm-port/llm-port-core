"""Normalized data models for Ray Core and Serve status."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class RayNodeStatus(BaseModel):
    node_id: str
    node_ip: str = ""
    node_manager_address: str = ""
    node_manager_port: Optional[int] = None
    node_name: Optional[str] = None
    alive: bool = False
    is_head: bool = False
    ray_version: Optional[str] = None
    resources: Dict[str, float] = Field(default_factory=dict)
    metrics_export_port: Optional[int] = None


class RayResourceTotals(BaseModel):
    cpu: float = 0.0
    gpu: float = 0.0
    memory: float = 0.0
    object_store_memory: float = 0.0
    accelerators: Dict[str, float] = Field(default_factory=dict)
    other: Dict[str, float] = Field(default_factory=dict)


class RayAvailableResources(BaseModel):
    cpu: float = 0.0
    gpu: float = 0.0
    memory: float = 0.0
    object_store_memory: float = 0.0
    accelerators: Dict[str, float] = Field(default_factory=dict)


class RayClusterStatus(BaseModel):
    alive: bool = False
    ray_version: Optional[str] = None
    num_nodes: int = 0
    total_gpus: float = 0.0
    available_gpus: float = 0.0
    total_cpus: float = 0.0
    available_cpus: float = 0.0
    cluster_address: Optional[str] = None
    head_address: Optional[str] = None
    nodes: List[RayNodeStatus] = Field(default_factory=list)
    resources: RayResourceTotals = Field(default_factory=RayResourceTotals)
    available: RayAvailableResources = Field(default_factory=RayAvailableResources)


class RayReplicaState(BaseModel):
    state: str
    count: int = 0


class RayDeploymentStatus(BaseModel):
    name: str
    status: str = ""
    status_trigger: Optional[str] = None
    message: str = ""
    num_replicas_ready: int = 0
    num_replicas_pending: int = 0


class RayApplicationStatus(BaseModel):
    name: str
    status: str = ""
    message: str = ""
    healthy: Optional[bool] = None
    deployments: Dict[str, RayDeploymentStatus] = Field(default_factory=dict)


class RayServeClusterStatus(BaseModel):
    available: bool = False
    controller_alive: bool = False
    applications: Dict[str, RayApplicationStatus] = Field(default_factory=dict)
    error: Optional[str] = None

