"""Schemas for system settings and initialization wizard APIs."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SettingUpdateRequest(BaseModel):
    """Single setting update payload."""

    value: object
    target_host: str = Field(default="local")


class SettingUpdateResponse(BaseModel):
    """Setting update response with apply result."""

    key: str
    apply_status: str
    apply_scope: str
    apply_job_id: str | None
    messages: list[str]


class SettingsSchemaItemDTO(BaseModel):
    """Schema metadata for one setting key."""

    key: str
    type: str
    category: str
    group: str
    label: str
    description: str
    is_secret: bool
    default: object
    apply_scope: str
    service_targets: list[str]
    protected: bool
    enum_values: list[str]


class SettingsValuesResponse(BaseModel):
    """Effective settings map."""

    items: dict[str, dict[str, Any]]


class ApplyJobResponse(BaseModel):
    """Apply job status payload."""

    id: str
    status: str
    target_host: str
    triggered_by: str | None
    change_set: dict[str, Any]
    error: str | None
    started_at: str
    ended_at: str | None
    events: list[dict[str, Any]]


class WizardStepDTO(BaseModel):
    """System init wizard step metadata."""

    id: str
    title: str
    description: str
    setting_keys: list[str]


class WizardStepsResponse(BaseModel):
    """List of wizard steps."""

    steps: list[WizardStepDTO]


class WizardApplyRequest(BaseModel):
    """Apply a batch of settings from wizard step."""

    target_host: str = "local"
    values: dict[str, object]


class WizardApplyResponse(BaseModel):
    """Wizard apply response summary."""

    results: list[SettingUpdateResponse]


class AgentRegisterRequest(BaseModel):
    """Agent registration payload."""

    id: str
    host: str
    capabilities: dict[str, Any] = Field(default_factory=dict)
    version: str | None = None


class AgentHeartbeatRequest(BaseModel):
    """Agent heartbeat payload."""

    id: str
    host: str
    status: str = "online"
    capabilities: dict[str, Any] = Field(default_factory=dict)
    version: str | None = None


class AgentDTO(BaseModel):
    """Agent DTO."""

    id: str
    host: str
    status: str
    capabilities: dict[str, Any]
    version: str | None
    last_seen: str | None


class AgentApplyRequest(BaseModel):
    """Remote apply request contract."""

    signed_bundle: dict[str, Any]


class AgentApplyResponse(BaseModel):
    """Remote apply acceptance response."""

    accepted: bool
    job_id: str
    agent_id: str
