"""Pydantic schemas for the LLM API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from llm_port_backend.db.models.llm import (
    ArtifactFormat,
    DownloadJobStatus,
    ModelSource,
    ModelStatus,
    ProviderTarget,
    ProviderType,
    RuntimeStatus,
)

# -----------------------------------------------------------------------
# Provider
# -----------------------------------------------------------------------


class ProviderCreateRequest(BaseModel):
    """Request body for creating a provider."""

    name: str = Field(..., min_length=1, max_length=256)
    type: ProviderType
    target: ProviderTarget = ProviderTarget.LOCAL_DOCKER
    endpoint_url: str | None = Field(
        None,
        max_length=1024,
        description="Base URL when target is remote_endpoint (e.g. https://api.example.com/v1).",
    )
    api_key: str | None = Field(
        None,
        max_length=512,
        description="Optional API key for authenticating with the remote endpoint.",
    )
    remote_model: str | None = Field(
        None,
        max_length=256,
        description="Optional default model name for remote providers (metadata/display only).",
    )
    litellm_provider: str | None = Field(
        None,
        max_length=64,
        description="LiteLLM provider prefix (e.g. 'anthropic', 'openrouter'). Auto-detected if omitted.",
    )
    litellm_model: str | None = Field(
        None,
        max_length=256,
        description="LiteLLM model identifier. Defaults to remote_model or probed model.",
    )
    extra_params: dict | None = Field(
        None,
        description="Provider-specific params (extra_headers, api_version, etc.).",
    )


class TestEndpointRequest(BaseModel):
    """Request body for testing a remote endpoint or LiteLLM provider."""

    endpoint_url: str | None = Field(None, max_length=1024)
    api_key: str | None = Field(None, max_length=512)
    litellm_provider: str | None = Field(None, max_length=64)
    litellm_model: str | None = Field(None, max_length=256)


class TestEndpointResponse(BaseModel):
    """Response for the test-endpoint probe."""

    compatible: bool
    models: list[str] = Field(default_factory=list)
    error: str | None = None


class ProviderUpdateRequest(BaseModel):
    """Request body for patching a provider."""

    name: str | None = Field(None, min_length=1, max_length=256)
    capabilities: dict | None = None
    endpoint_url: str | None = Field(None, max_length=1024)
    api_key: str | None = Field(None, max_length=512)
    remote_model: str | None = Field(None, max_length=256)
    litellm_provider: str | None = Field(None, max_length=64)
    litellm_model: str | None = Field(None, max_length=256)
    extra_params: dict | None = None


class ProviderDTO(BaseModel):
    """Response DTO for a provider."""

    id: uuid.UUID
    name: str
    type: ProviderType
    target: ProviderTarget
    endpoint_url: str | None = None
    capabilities: dict | None = None
    remote_model: str | None = None
    litellm_provider: str | None = None
    litellm_model: str | None = None
    extra_params: dict | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# -----------------------------------------------------------------------
# Model
# -----------------------------------------------------------------------


class ModelDownloadRequest(BaseModel):
    """Request body for HF model download."""

    hf_repo_id: str = Field(..., min_length=1)
    hf_revision: str | None = None
    display_name: str | None = None
    tags: list[str] | None = None


class ModelRegisterRequest(BaseModel):
    """Request body for registering a local model."""

    display_name: str = Field(..., min_length=1)
    path: str = Field(..., min_length=1)
    tags: list[str] | None = None

    @field_validator("path")
    @classmethod
    def _reject_path_traversal(cls, v: str) -> str:
        if ".." in v:
            msg = "Path must not contain '..' segments."
            raise ValueError(msg)
        return v


class ModelDTO(BaseModel):
    """Response DTO for a model."""

    id: uuid.UUID
    display_name: str
    source: ModelSource
    hf_repo_id: str | None = None
    hf_revision: str | None = None
    license_ack_required: bool = False
    tags: list[str] | None = None
    status: ModelStatus
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ModelInstanceDTO(BaseModel):
    """Where a model is deployed — one entry per runtime."""

    runtime_id: uuid.UUID
    runtime_name: str
    runtime_status: RuntimeStatus
    provider_id: uuid.UUID
    provider_name: str
    provider_type: ProviderType
    execution_target: str  # "local" | "node"
    node_id: uuid.UUID | None = None
    node_host: str | None = None


class ModelWithInstancesDTO(ModelDTO):
    """Model DTO enriched with its running instances (locations)."""

    instances: list[ModelInstanceDTO] = Field(default_factory=list)


class DownloadResponseDTO(BaseModel):
    """Response for the download endpoint — includes dispatch status."""

    model: ModelDTO
    job: DownloadJobDTO
    dispatched: bool = True
    dispatch_error: str | None = None


class ArtifactDTO(BaseModel):
    """Response DTO for a model artifact."""

    id: uuid.UUID
    model_id: uuid.UUID
    format: ArtifactFormat
    path: str
    size_bytes: int
    sha256: str | None = None
    engine_compat: list[str] | None = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# -----------------------------------------------------------------------
# Runtime
# -----------------------------------------------------------------------


class RuntimeCreateRequest(BaseModel):
    """Request body for creating a runtime."""

    name: str = Field(..., min_length=1, max_length=256)
    provider_id: uuid.UUID
    model_id: uuid.UUID
    generic_config: dict | None = None
    provider_config: dict | None = None
    openai_compat: bool = True
    target_node_id: uuid.UUID | None = None
    placement_hints: dict | None = None
    model_source: str | None = Field(
        None,
        pattern=r"^(sync_from_server|download_from_hf)$",
        description="How the model reaches the remote node.",
    )
    image_source: str | None = Field(
        None,
        pattern=r"^(pull_from_registry|transfer_from_server)$",
        description="How the container image reaches the remote node.",
    )


class RuntimeUpdateRequest(BaseModel):
    """Request body for updating a runtime's config (triggers container rebuild)."""

    name: str | None = Field(None, min_length=1, max_length=256)
    generic_config: dict | None = None
    provider_config: dict | None = None
    openai_compat: bool | None = None
    target_node_id: uuid.UUID | None = None
    placement_hints: dict | None = None


class RuntimeDTO(BaseModel):
    """Response DTO for a runtime."""

    id: uuid.UUID
    name: str
    provider_id: uuid.UUID
    model_id: uuid.UUID
    status: RuntimeStatus
    endpoint_url: str | None = None
    openai_compat: bool = True
    generic_config: dict | None = None
    provider_config: dict | None = None
    container_ref: str | None = None
    execution_target: str = "local"
    assigned_node_id: uuid.UUID | None = None
    desired_state: str = "running"
    placement_explain_json: dict | None = None
    last_command_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class RuntimeHealthDTO(BaseModel):
    """Health probe result for a runtime."""

    healthy: bool
    detail: str = ""


# -----------------------------------------------------------------------
# Download Job
# -----------------------------------------------------------------------


class DownloadJobDTO(BaseModel):
    """Response DTO for a download job."""

    id: uuid.UUID
    model_id: uuid.UUID
    status: DownloadJobStatus
    progress: int
    log_ref: str | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


# -----------------------------------------------------------------------
# Settings
# -----------------------------------------------------------------------


class HFTokenStatusDTO(BaseModel):
    """Whether an HF token is configured."""

    configured: bool


class HFTokenSetRequest(BaseModel):
    """Request body for setting the HF token."""

    token: str = Field(..., min_length=1)


# -----------------------------------------------------------------------
# HF Cache Scan
# -----------------------------------------------------------------------


class HFCacheScanResultDTO(BaseModel):
    """Result of auto-importing models from the local HF cache."""

    imported_count: int
    imported: list[ModelDTO]


# -----------------------------------------------------------------------
# Graph
# -----------------------------------------------------------------------


class GraphNodeDTO(BaseModel):
    """Graph node used by the LLM visualizer."""

    id: str
    type: str
    label: str
    status: str | None = None
    meta: dict[str, Any] | None = None


class GraphEdgeDTO(BaseModel):
    """Graph edge used by the LLM visualizer."""

    id: str
    source: str
    target: str
    type: str = "default"


class TopologyResponseDTO(BaseModel):
    """Response for graph topology."""

    generated_at: datetime
    nodes: list[GraphNodeDTO]
    edges: list[GraphEdgeDTO]


class TraceEventDTO(BaseModel):
    """Trace event emitted to the graph UI."""

    event_id: int
    ts: datetime
    request_id: str
    trace_id: str | None = None
    tenant_id: str
    user_id: str
    model_alias: str | None = None
    provider_instance_id: str | None = None
    status: int
    latency_ms: int
    ttft_ms: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    error_code: str | None = None


class TraceSnapshotResponseDTO(BaseModel):
    """Initial graph trace snapshot."""

    items: list[TraceEventDTO]
    next_cursor: str | None = None


class DataUsagePerInstanceDTO(BaseModel):
    """Token and request usage for a single provider instance (runtime)."""

    provider_instance_id: str
    total_requests: int = 0
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens: int = 0
    error_count: int = 0


class DataUsageSummaryDTO(BaseModel):
    """Aggregated data-usage across all provider instances."""

    generated_at: datetime
    instances: list[DataUsagePerInstanceDTO]
    grand_total_requests: int = 0
    grand_total_tokens: int = 0
