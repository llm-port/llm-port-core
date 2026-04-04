"""Schemas for system settings and initialization wizard APIs."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


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


class NodeEnrollmentTokenCreateRequest(BaseModel):
    """Create one-time node enrollment token."""

    note: str | None = None


class NodeEnrollmentTokenCreateResponse(BaseModel):
    """Response with plaintext enrollment token (returned once)."""

    id: str
    token: str
    expires_at: str
    note: str | None = None


class NodeEnrollRequest(BaseModel):
    """Agent enrollment request using one-time token."""

    enrollment_token: str
    agent_id: str
    host: str
    capabilities: dict[str, Any] = Field(default_factory=dict)
    version: str | None = None


class NodeEnrollResponse(BaseModel):
    """Enrollment result for node agent."""

    node_id: str
    agent_id: str
    credential: str
    status: str
    host: str


class NodeRotateCredentialResponse(BaseModel):
    """Credential rotation response for node agent."""

    node_id: str
    credential: str


class NodeDTO(BaseModel):
    """Node control-plane DTO."""

    id: str
    agent_id: str
    host: str
    status: str
    version: str | None = None
    labels: dict[str, Any] = Field(default_factory=dict)
    capabilities: dict[str, Any] = Field(default_factory=dict)
    maintenance_mode: bool = False
    draining: bool = False
    scheduler_eligible: bool = True
    last_seen: str | None = None
    created_at: str
    updated_at: str
    profile_id: str | None = None
    latest_inventory: dict[str, Any] | None = None
    latest_utilization: dict[str, Any] | None = None


class NodeMaintenanceRequest(BaseModel):
    """Enable or disable node maintenance mode."""

    enabled: bool
    reason: str | None = None


class NodeDrainRequest(BaseModel):
    """Enable or disable node draining mode."""

    enabled: bool


class NodeCommandIssueRequest(BaseModel):
    """Issue command to one node."""

    command_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None
    correlation_id: str | None = None
    timeout_sec: int | None = None


class NodeCommandDTO(BaseModel):
    """Node command DTO."""

    id: str
    node_id: str
    command_type: str
    status: str
    correlation_id: str | None = None
    idempotency_key: str
    payload: dict[str, Any] = Field(default_factory=dict)
    result: dict[str, Any] | None = None
    timeout_sec: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    issued_at: str
    dispatched_at: str | None = None
    acked_at: str | None = None
    started_at: str | None = None
    completed_at: str | None = None


class NodeCommandTimelineDTO(BaseModel):
    """Command DTO with ordered timeline."""

    command: NodeCommandDTO
    events: list[dict[str, Any]] = Field(default_factory=list)


class GrafanaAlertItemDTO(BaseModel):
    """Subset of Grafana alert item payload fields."""

    status: str | None = None
    labels: dict[str, Any] = Field(default_factory=dict)
    annotations: dict[str, Any] = Field(default_factory=dict)
    startsAt: str | None = None
    endsAt: str | None = None
    fingerprint: str | None = None
    generatorURL: str | None = None

    model_config = ConfigDict(extra="allow")


class GrafanaWebhookPayloadDTO(BaseModel):
    """Generic Grafana webhook payload contract."""

    status: str | None = None
    title: str | None = None
    message: str | None = None
    groupKey: str | None = None
    startsAt: str | None = None
    commonLabels: dict[str, Any] = Field(default_factory=dict)
    commonAnnotations: dict[str, Any] = Field(default_factory=dict)
    alerts: list[GrafanaAlertItemDTO] = Field(default_factory=list)

    model_config = ConfigDict(extra="allow")


class GrafanaWebhookResponseDTO(BaseModel):
    """Grafana webhook ingestion result."""

    accepted: bool
    fingerprint: str | None = None


# ── Node Profile schemas ──────────────────────────────────


class NodeProfileCreateRequest(BaseModel):
    """Create a node profile."""

    name: str
    description: str | None = None
    is_default: bool = False
    runtime_config: dict[str, Any] = Field(default_factory=dict)
    gpu_config: dict[str, Any] = Field(default_factory=dict)
    storage_config: dict[str, Any] = Field(default_factory=dict)
    network_config: dict[str, Any] = Field(default_factory=dict)
    logging_config: dict[str, Any] = Field(default_factory=dict)
    security_config: dict[str, Any] = Field(default_factory=dict)
    update_config: dict[str, Any] = Field(default_factory=dict)


class NodeProfileUpdateRequest(BaseModel):
    """Update a node profile (partial)."""

    name: str | None = None
    description: str | None = None
    is_default: bool | None = None
    runtime_config: dict[str, Any] | None = None
    gpu_config: dict[str, Any] | None = None
    storage_config: dict[str, Any] | None = None
    network_config: dict[str, Any] | None = None
    logging_config: dict[str, Any] | None = None
    security_config: dict[str, Any] | None = None
    update_config: dict[str, Any] | None = None


class NodeProfileDTO(BaseModel):
    """Node profile DTO."""

    id: str
    name: str
    description: str | None = None
    is_default: bool = False
    runtime_config: dict[str, Any] = Field(default_factory=dict)
    gpu_config: dict[str, Any] = Field(default_factory=dict)
    storage_config: dict[str, Any] = Field(default_factory=dict)
    network_config: dict[str, Any] = Field(default_factory=dict)
    logging_config: dict[str, Any] = Field(default_factory=dict)
    security_config: dict[str, Any] = Field(default_factory=dict)
    update_config: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    updated_at: str


class NodeProfileAssignRequest(BaseModel):
    """Assign a profile to a node."""

    profile_id: str
