"""SQLAlchemy models for the RAG Lite subsystem."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from llm_port_backend.db.base import Base

# Max pgvector column width – covers OpenAI ada-002 (1536), nomic (768),
# text-embedding-3-small (1536), etc.  Must be ≤2000 for HNSW indexes.
MAX_EMBEDDING_DIM = 2000


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class RagLiteDocumentStatus(enum.StrEnum):
    """Lifecycle status of an uploaded document."""

    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    ERROR = "error"


class RagLiteJobStatus(enum.StrEnum):
    """Lifecycle status of an ingest job."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class RagLiteEventType(enum.StrEnum):
    """Severities for ingest job events."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class RagLiteCollection(Base):
    """A named group of documents for scoping searches."""

    __tablename__ = "rag_lite_collections"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("rag_lite_collections.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    # relationships
    parent: Mapped[RagLiteCollection | None] = relationship(
        remote_side="RagLiteCollection.id",
        back_populates="children",
    )
    children: Mapped[list[RagLiteCollection]] = relationship(
        back_populates="parent",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    documents: Mapped[list[RagLiteDocument]] = relationship(
        back_populates="collection",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class RagLiteDocument(Base):
    """An uploaded document with extracted text and processing status."""

    __tablename__ = "rag_lite_documents"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    collection_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("rag_lite_collections.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    doc_type: Mapped[str] = mapped_column(String(32), nullable=False)
    content_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    summary: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        doc="AI-generated or user-written summary of the document.",
    )
    metadata_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[RagLiteDocumentStatus] = mapped_column(
        SAEnum(
            RagLiteDocumentStatus,
            name="rag_lite_document_status",
            create_type=False,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=RagLiteDocumentStatus.PENDING,
        index=True,
    )
    file_store_key: Mapped[str | None] = mapped_column(
        String(1024),
        nullable=True,
        doc="Key / path of the original file in the file store.",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # relationships
    collection: Mapped[RagLiteCollection | None] = relationship(
        back_populates="documents",
    )
    chunks: Mapped[list[RagLiteChunk]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    ingest_jobs: Mapped[list[RagLiteIngestJob]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class RagLiteChunk(Base):
    """A chunk of a document with its embedding vector."""

    __tablename__ = "rag_lite_chunks"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("rag_lite_documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    collection_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        nullable=True,
        index=True,
        doc="Denormalised from document for fast search filtering.",
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding = mapped_column(Vector(MAX_EMBEDDING_DIM), nullable=True)
    embedding_dim: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # relationships
    document: Mapped[RagLiteDocument] = relationship(back_populates="chunks")

    __table_args__ = (
        Index(
            "ix_rag_lite_chunks_embedding_hnsw",
            embedding,
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 64},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )


class RagLiteIngestJob(Base):
    """Tracks a background document ingest pipeline run."""

    __tablename__ = "rag_lite_ingest_jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("rag_lite_documents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[RagLiteJobStatus] = mapped_column(
        SAEnum(
            RagLiteJobStatus,
            name="rag_lite_job_status",
            create_type=False,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=RagLiteJobStatus.QUEUED,
        index=True,
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    stats_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    # relationships
    document: Mapped[RagLiteDocument] = relationship(back_populates="ingest_jobs")
    events: Mapped[list[RagLiteIngestEvent]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="RagLiteIngestEvent.created_at",
    )


class RagLiteIngestEvent(Base):
    """Individual event / log entry within an ingest job."""

    __tablename__ = "rag_lite_ingest_events"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("rag_lite_ingest_jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    event_type: Mapped[RagLiteEventType] = mapped_column(
        SAEnum(
            RagLiteEventType,
            name="rag_lite_event_type",
            create_type=False,
            values_callable=lambda e: [m.value for m in e],
        ),
        nullable=False,
        default=RagLiteEventType.INFO,
    )
    message: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )

    # relationships
    job: Mapped[RagLiteIngestJob] = relationship(back_populates="events")
