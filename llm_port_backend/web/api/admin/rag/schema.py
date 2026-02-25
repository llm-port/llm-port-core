"""Pydantic schemas for admin RAG proxy endpoints."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class RagChunkingPolicyDTO(BaseModel):
    """Chunking settings pushed to RAG runtime config."""

    max_tokens: int = Field(default=512, ge=64, le=4096)
    overlap: int = Field(default=64, ge=0, le=1024)
    by_headings: bool = False


class RagRuntimeConfigPayloadDTO(BaseModel):
    """Runtime embedding config payload."""

    embedding_provider: str = Field(min_length=1, max_length=64)
    embedding_model: str = Field(min_length=1, max_length=256)
    embedding_base_url: str | None = Field(default=None, max_length=1024)
    embedding_api_key_ref: str | None = Field(default=None, max_length=256)
    embedding_dim: int = Field(ge=8, le=4096)
    chunking_policy: RagChunkingPolicyDTO = Field(default_factory=RagChunkingPolicyDTO)


class RagRuntimeConfigUpdateRequest(BaseModel):
    """Admin request for updating RAG runtime config."""

    payload: RagRuntimeConfigPayloadDTO
    embedding_api_key: str | None = Field(
        default=None,
        min_length=1,
        max_length=4096,
        description="Optional secret sent via header to RAG; never persisted in backend.",
    )


class RagRuntimeConfigResponse(BaseModel):
    """Runtime config response."""

    updated_at: datetime
    payload: RagRuntimeConfigPayloadDTO


class RagPrincipalsDTO(BaseModel):
    """Resolved principals for ACL filtering."""

    user_id: str = Field(min_length=1, max_length=256)
    group_ids: list[str] = Field(default_factory=list)


class RagSearchFiltersDTO(BaseModel):
    """Search filters payload."""

    sources: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    doc_types: list[str] = Field(default_factory=list)
    container_ids: list[str] = Field(default_factory=list)
    include_descendants: bool = True
    source_kind: str | None = Field(default=None, max_length=32)
    asset_ids: list[str] = Field(default_factory=list)
    time_from: datetime | None = None
    time_to: datetime | None = None


class RagKnowledgeSearchRequestDTO(BaseModel):
    """Knowledge search request."""

    tenant_id: str = Field(min_length=1, max_length=256)
    workspace_id: str | None = Field(default=None, max_length=256)
    query: str = Field(min_length=1, max_length=8192)
    principals: RagPrincipalsDTO
    filters: RagSearchFiltersDTO = Field(default_factory=RagSearchFiltersDTO)
    top_k: int = Field(default=5, ge=1, le=50)
    mode: str = Field(default="hybrid", pattern="^(vector|keyword|hybrid)$")
    debug: bool = False


class RagSearchResultDTO(BaseModel):
    """One search hit."""

    chunk_text: str
    doc_title: str | None = None
    source_uri: str
    section: str | None = None
    score: float
    metadata: dict[str, Any] = Field(default_factory=dict)


class RagKnowledgeSearchResponseDTO(BaseModel):
    """Search response payload."""

    results: list[RagSearchResultDTO]
    debug: dict[str, Any] | None = None


class RagCollectorSummaryDTO(BaseModel):
    """Collector summary."""

    id: str
    type: str
    enabled: bool
    schedule: str
    tenant_id: str
    workspace_id: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class RagCollectorListResponseDTO(BaseModel):
    """Collector list response."""

    collectors: list[RagCollectorSummaryDTO]


class RagAdminRunCollectorResponseDTO(BaseModel):
    """Collector run trigger response."""

    job_id: str
    source_id: str
    status: str


class RagContainerPayloadDTO(BaseModel):
    """Create/update container payload."""

    tenant_id: str = Field(min_length=1, max_length=256)
    workspace_id: str | None = Field(default=None, max_length=256)
    parent_id: str | None = None
    name: str = Field(min_length=1, max_length=256)
    sort_order: int = 0
    acl_principals: list[str] = Field(default_factory=list)


class RagContainerDTO(BaseModel):
    """Container DTO."""

    id: str
    tenant_id: str
    workspace_id: str | None = None
    parent_id: str | None = None
    name: str
    slug: str
    path: str
    depth: int
    sort_order: int
    acl_principals: list[str] = Field(default_factory=list)
    is_active: bool
    created_at: datetime
    updated_at: datetime


class RagContainerTreeResponseDTO(BaseModel):
    """Container tree response."""

    containers: list[RagContainerDTO]


class RagUploadPresignRequestDTO(BaseModel):
    """Upload presign request."""

    tenant_id: str = Field(min_length=1, max_length=256)
    workspace_id: str | None = Field(default=None, max_length=256)
    container_id: str
    filename: str = Field(min_length=1, max_length=1024)
    size_bytes: int = Field(ge=1)
    content_type: str = Field(min_length=1, max_length=256)
    sha256: str | None = None


class RagUploadPresignResponseDTO(BaseModel):
    """Upload presign response."""

    object_key: str
    upload_url: str
    required_headers: dict[str, str] = Field(default_factory=dict)
    expires_at: datetime


class RagUploadCompleteRequestDTO(BaseModel):
    """Upload complete request."""

    object_key: str
    tenant_id: str = Field(min_length=1, max_length=256)
    workspace_id: str | None = Field(default=None, max_length=256)
    container_id: str
    filename: str = Field(min_length=1, max_length=1024)
    size_bytes: int = Field(ge=1)
    content_type: str = Field(min_length=1, max_length=256)
    sha256: str = Field(min_length=64, max_length=64)
    draft_id: str | None = None
    tags: list[str] = Field(default_factory=list)
    acl_principals: list[str] = Field(default_factory=list)
    created_by: str | None = None


class RagUploadCompleteResponseDTO(BaseModel):
    """Upload complete response."""

    draft_id: str
    operation_id: int
    status: str


class RagDraftCreateRequestDTO(BaseModel):
    """Create draft request."""

    tenant_id: str = Field(min_length=1, max_length=256)
    workspace_id: str | None = Field(default=None, max_length=256)
    container_id: str
    created_by: str | None = None


class RagDraftOperationPayloadDTO(BaseModel):
    """Draft operation payload."""

    op_type: str = Field(pattern="^(upload|replace|delete|move|retag|set_acl|rename)$")
    asset_id: str | None = None
    target_container_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class RagDraftUpdateRequestDTO(BaseModel):
    """Patch draft request."""

    operations: list[RagDraftOperationPayloadDTO] | None = None
    status: str | None = Field(default=None, pattern="^(open|saved|published|cancelled)$")


class RagDraftOperationDTO(BaseModel):
    """Draft operation DTO."""

    id: int
    op_type: str
    asset_id: str | None = None
    target_container_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    status: str
    error: str | None = None
    created_at: datetime
    updated_at: datetime


class RagDraftDTO(BaseModel):
    """Draft DTO."""

    id: str
    tenant_id: str
    workspace_id: str | None = None
    container_id: str
    status: str
    created_by: str | None = None
    created_at: datetime
    updated_at: datetime
    operations: list[RagDraftOperationDTO] = Field(default_factory=list)


class RagPublishTriggerRequestDTO(BaseModel):
    """Publish trigger request."""

    scheduled_for: datetime | None = None
    triggered_by: str | None = None


class RagPublishTriggerResponseDTO(BaseModel):
    """Publish trigger response."""

    publish_id: str
    job_id: str | None = None
    status: str


class RagPublishDTO(BaseModel):
    """Publish DTO."""

    id: str
    draft_id: str
    tenant_id: str
    workspace_id: str | None = None
    container_id: str
    scheduled_for: datetime | None = None
    status: str
    triggered_by: str
    stats: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    created_at: datetime
    updated_at: datetime


class RagPublishListResponseDTO(BaseModel):
    """Publish list response."""

    publishes: list[RagPublishDTO]


class RagIngestJobEventDTO(BaseModel):
    """One ingestion event."""

    event_type: str
    message: str
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


class RagIngestJobDTO(BaseModel):
    """Ingestion job status payload."""

    job_id: str
    collector_id: str
    job_type: str = "collector_sync"
    publish_id: str | None = None
    container_id: str | None = None
    source_id: str | None = None
    tenant_id: str
    workspace_id: str | None = None
    status: str
    error: str | None = None
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    events: list[RagIngestJobEventDTO] = Field(default_factory=list)


class RagIngestJobListResponseDTO(BaseModel):
    """Ingestion jobs list response."""

    jobs: list[RagIngestJobDTO]
