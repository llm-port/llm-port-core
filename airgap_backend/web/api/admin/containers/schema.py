"""Pydantic schemas for the admin containers API."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from airgap_backend.db.models.containers import ContainerClass, ContainerPolicy


class ContainerSummaryDTO(BaseModel):
    """Lightweight summary of a running/stopped container."""

    id: str
    name: str
    image: str
    status: str
    state: str
    created: str
    ports: list[dict] = Field(default_factory=list)
    networks: list[str] = Field(default_factory=list)
    container_class: ContainerClass = ContainerClass.UNTRUSTED
    policy: ContainerPolicy = ContainerPolicy.FREE
    owner_scope: str = "unknown"

    model_config = ConfigDict(from_attributes=True)


class ContainerDetailDTO(ContainerSummaryDTO):
    """Full detail of a container including config."""

    raw: dict = Field(default_factory=dict)  # raw Docker inspect output


class LifecycleAction(str):
    """Valid lifecycle action names."""

    START = "start"
    STOP = "stop"
    RESTART = "restart"
    PAUSE = "pause"
    UNPAUSE = "unpause"


class ExecTokenRequest(BaseModel):
    """Request body for creating an exec session token."""

    cmd: list[str] = Field(default=["/bin/sh"], description="Command to execute.")
    workdir: str = "/"


class ExecTokenDTO(BaseModel):
    """Exec session token to be used by a websocket client."""

    exec_id: str


class RegisterContainerRequest(BaseModel):
    """Body for manually registering/classifying a container."""

    container_class: ContainerClass = ContainerClass.TENANT_APP
    owner_scope: str = "platform"
    policy: ContainerPolicy = ContainerPolicy.FREE
