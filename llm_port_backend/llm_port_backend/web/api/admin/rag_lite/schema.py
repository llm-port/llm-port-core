"""Pydantic DTOs for RAG Lite admin endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


# -----------------------------------------------------------------------
# Upload
# -----------------------------------------------------------------------


class RagLiteUploadResponse(BaseModel):
    document_id: uuid.UUID
    job_id: uuid.UUID
    filename: str
    doc_type: str
    status: str = "pending"
    message: str = "File accepted — processing in background."


# -----------------------------------------------------------------------
# Documents
# -----------------------------------------------------------------------


class RagLiteDocumentDTO(BaseModel):
    id: uuid.UUID
    filename: str
    doc_type: str
    collection_id: uuid.UUID | None
    size_bytes: int
    chunk_count: int
    status: str
    summary: str | None = None
    created_at: datetime


class RagLiteDocumentDetailDTO(RagLiteDocumentDTO):
    file_store_key: str | None = None
    sha256: str
    metadata_json: dict | None = None


# -----------------------------------------------------------------------
# Search
# -----------------------------------------------------------------------


class RagLiteSearchRequest(BaseModel):
    query: str = Field(min_length=1)
    top_k: int = Field(default=5, ge=1, le=50)
    collection_ids: list[uuid.UUID] | None = None


class RagLiteSearchResult(BaseModel):
    chunk_text: str
    document_id: uuid.UUID
    filename: str
    chunk_index: int
    score: float


class RagLiteSearchResponse(BaseModel):
    results: list[RagLiteSearchResult]
    query: str


# -----------------------------------------------------------------------
# Collections
# -----------------------------------------------------------------------


class RagLiteCollectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    description: str | None = None
    parent_id: uuid.UUID | None = None


class RagLiteCollectionUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=256)
    description: str | None = None
    parent_id: uuid.UUID | None = ...


class RagLiteCollectionDTO(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    parent_id: uuid.UUID | None = None
    document_count: int = 0
    created_at: datetime
    updated_at: datetime


class RagLiteDocumentMoveRequest(BaseModel):
    collection_id: uuid.UUID | None = None


class RagLiteSummaryResponse(BaseModel):
    summary: str


class RagLiteGraphSearchRequest(BaseModel):
    query: str = Field(min_length=1)
    top_k_collections: int = Field(default=3, ge=1, le=20)
    top_k_chunks: int = Field(default=5, ge=1, le=50)


class RagLiteGraphSearchCollectionHit(BaseModel):
    collection_id: uuid.UUID
    collection_name: str
    score: float


class RagLiteGraphSearchResponse(BaseModel):
    query: str
    collection_hits: list[RagLiteGraphSearchCollectionHit]
    results: list[RagLiteSearchResult]


# -----------------------------------------------------------------------
# Jobs
# -----------------------------------------------------------------------


class RagLiteJobEventDTO(BaseModel):
    id: uuid.UUID
    event_type: str
    message: str
    created_at: datetime


class RagLiteJobDTO(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    status: str
    error_message: str | None = None
    stats_json: dict | None = None
    created_at: datetime
    updated_at: datetime
    events: list[RagLiteJobEventDTO] = []


# -----------------------------------------------------------------------
# Config / Health
# -----------------------------------------------------------------------


class RagLiteHealthResponse(BaseModel):
    status: str = "ok"
    mode: str = "lite"


class RagLiteConfigDTO(BaseModel):
    embedding_provider_id: str
    embedding_model: str
    embedding_dim: int
    chunk_max_tokens: int
    chunk_overlap_tokens: int
    file_store_root: str
    upload_max_file_mb: int
