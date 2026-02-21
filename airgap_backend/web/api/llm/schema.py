"""Pydantic schemas for the LLM API."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from airgap_backend.db.models.llm import (
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


class ProviderUpdateRequest(BaseModel):
    """Request body for patching a provider."""

    name: str | None = Field(None, min_length=1, max_length=256)
    capabilities: dict | None = None


class ProviderDTO(BaseModel):
    """Response DTO for a provider."""

    id: uuid.UUID
    name: str
    type: ProviderType
    target: ProviderTarget
    capabilities: dict | None = None
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
