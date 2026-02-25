"""Pydantic schemas for the admin containers API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from llm_port_backend.db.models.containers import ContainerClass, ContainerPolicy


class ContainerSummaryDTO(BaseModel):
    """Lightweight summary of a running/stopped container."""

    id: str
    name: str
    image: str
    status: str
    state: str
    created: str
    ports: list[dict[str, Any]] = Field(default_factory=list)
    networks: list[str] = Field(default_factory=list)
    container_class: ContainerClass = ContainerClass.UNTRUSTED
    policy: ContainerPolicy = ContainerPolicy.FREE
    owner_scope: str = "unknown"

    model_config = ConfigDict(from_attributes=True)

    @field_validator("ports", "networks", mode="before")
    @classmethod
    def _coerce_nullable_lists(cls, value: Any) -> Any:
        return [] if value is None else value


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


class PortBinding(BaseModel):
    """A single host→container port mapping."""

    host_port: str = Field(..., description="Host port, e.g. '8080'.")
    container_port: str = Field(..., description="Container port/protocol, e.g. '80/tcp'.")


class CreateContainerRequest(BaseModel):
    """Request body for creating a new container."""

    image: str = Field(..., description="Image name:tag, e.g. 'nginx:latest'.")
    name: str | None = Field(None, description="Optional container name.")
    container_class: ContainerClass = ContainerClass.UNTRUSTED
    owner_scope: str = "platform"
    policy: ContainerPolicy = ContainerPolicy.FREE
    auto_start: bool = False
    ports: list[PortBinding] = Field(default_factory=list)
    env: list[str] = Field(default_factory=list, description="KEY=VALUE strings.")
    cmd: list[str] | None = Field(None, description="Command override.")
    network: str | None = None
    volumes: list[str] = Field(default_factory=list, description="'/host:/container' strings.")
