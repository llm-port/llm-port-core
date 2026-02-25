"""Pydantic schemas for the admin networks API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class NetworkContainerDTO(BaseModel):
    """A container attached to a network."""

    id: str
    name: str
    ipv4_address: str = ""
    mac_address: str = ""


class NetworkSummaryDTO(BaseModel):
    """Summary of a Docker network."""

    id: str
    name: str
    driver: str = ""
    scope: str = ""
    internal: bool = False
    created: str = ""
    is_system: bool = False
    container_count: int = 0


class NetworkDetailDTO(NetworkSummaryDTO):
    """Full details of a Docker network including connected containers."""

    subnet: str = ""
    gateway: str = ""
    containers: list[NetworkContainerDTO] = Field(default_factory=list)
    labels: dict[str, str] = Field(default_factory=dict)
    options: dict[str, str] = Field(default_factory=dict)


class CreateNetworkRequest(BaseModel):
    """Request body for creating a network."""

    name: str = Field(..., min_length=1, max_length=256)
    driver: str = Field(default="bridge", description="bridge, overlay, macvlan, etc.")
    internal: bool = Field(default=False)
    subnet: str | None = Field(default=None, description="e.g. 172.28.0.0/16")
    gateway: str | None = Field(default=None, description="e.g. 172.28.0.1")
    labels: dict[str, str] = Field(default_factory=dict)
