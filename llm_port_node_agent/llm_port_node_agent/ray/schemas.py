"""Pydantic schemas for Ray node command payloads."""

from typing import Any

from pydantic import BaseModel


class EnsureRayRuntimePayload(BaseModel):
    version: str = "2.58.0"


class StartRayHeadPayload(BaseModel):
    credential_ref: str
    version: str = "2.58.0"
    port: int = 6379
    dashboard_port: int = 8265
    dashboard_host: str = "127.0.0.1"
    num_cpus: int | None = None
    num_gpus: int | None = None
    resources: dict[str, float] = {}


class JoinRayClusterPayload(BaseModel):
    head_address: str  # "<ip>:<port>"
    credential_ref: str
    version: str = "2.58.0"
    node_ip_address: str | None = None
    num_cpus: int | None = None
    num_gpus: int | None = None
    resources: dict[str, float] = {}


class StopRayPayload(BaseModel):
    force: bool = False
    version: str = "2.58.0"


class GetRayStatusPayload(BaseModel):
    address: str | None = None  # defaults to localhost


class RayStatusResult(BaseModel):
    alive: bool
    version: str | None = None
    num_nodes: int = 0
    nodes: list[dict[str, Any]] = []
    total_gpus: float = 0
    available_gpus: float = 0
    cluster_address: str | None = None

