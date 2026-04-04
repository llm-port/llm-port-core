"""Add RAG Lite tables (collections, documents, chunks, jobs, events).

Revision ID: r4g1l1t3v0c1
Revises: h1i2j3k4l5m6
Create Date: 2026-03-06 10:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "r4g1l1t3v0c1"
down_revision = "h1i2j3k4l5m6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # NOTE: The pgvector extension must be created by a superuser.
    # It is provisioned in llm_port_shared/initdb/01-init.sql.

    # -- Enum types (drop orphans from prior failed runs) ------------------
    op.execute("DROP TYPE IF EXISTS rag_lite_document_status")
    op.execute("DROP TYPE IF EXISTS rag_lite_job_status")
    op.execute("DROP TYPE IF EXISTS rag_lite_event_type")

    # -- Collections -------------------------------------------------------
    op.create_table(
        "rag_lite_collections",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(length=256), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # -- Documents ---------------------------------------------------------
    op.create_table(
        "rag_lite_documents",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "collection_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("rag_lite_collections.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("filename", sa.String(length=512), nullable=False),
        sa.Column("doc_type", sa.String(length=32), nullable=False),
        sa.Column("content_text", sa.Text(), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "metadata_json",
            sa.dialects.postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("chunk_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "status",
            sa.Enum(
                "pending", "processing", "ready", "error",
                name="rag_lite_document_status",
            ),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("file_store_key", sa.String(length=1024), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_rag_lite_documents_collection_id",
        "rag_lite_documents",
        ["collection_id"],
    )
    op.create_index(
        "ix_rag_lite_documents_sha256",
        "rag_lite_documents",
        ["sha256"],
    )
    op.create_index(
        "ix_rag_lite_documents_status",
        "rag_lite_documents",
        ["status"],
    )

    # -- Chunks ------------------------------------------------------------
    op.create_table(
        "rag_lite_chunks",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "document_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("rag_lite_documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "collection_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("chunk_text", sa.Text(), nullable=False),
        sa.Column("embedding_dim", sa.SmallInteger(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # Add the vector column via raw SQL (Alembic doesn't support pgvector natively)
    op.execute(
        "ALTER TABLE rag_lite_chunks "
        "ADD COLUMN embedding vector(2000)"
    )

    op.create_index(
        "ix_rag_lite_chunks_document_id",
        "rag_lite_chunks",
        ["document_id"],
    )
    op.create_index(
        "ix_rag_lite_chunks_collection_id",
        "rag_lite_chunks",
        ["collection_id"],
    )
    op.execute(
        "CREATE INDEX ix_rag_lite_chunks_embedding_hnsw "
        "ON rag_lite_chunks USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64)"
    )

    # -- Ingest Jobs -------------------------------------------------------
    op.create_table(
        "rag_lite_ingest_jobs",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "document_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("rag_lite_documents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "queued", "running", "completed", "failed",
                name="rag_lite_job_status",
            ),
            nullable=False,
            server_default="queued",
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "stats_json",
            sa.dialects.postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_rag_lite_ingest_jobs_document_id",
        "rag_lite_ingest_jobs",
        ["document_id"],
    )
    op.create_index(
        "ix_rag_lite_ingest_jobs_status",
        "rag_lite_ingest_jobs",
        ["status"],
    )

    # -- Ingest Events -----------------------------------------------------
    op.create_table(
        "rag_lite_ingest_events",
        sa.Column("id", sa.dialects.postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "job_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("rag_lite_ingest_jobs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "event_type",
            sa.Enum(
                "info", "warning", "error",
                name="rag_lite_event_type",
            ),
            nullable=False,
            server_default="info",
        ),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_rag_lite_ingest_events_job_id",
        "rag_lite_ingest_events",
        ["job_id"],
    )


def downgrade() -> None:
    op.drop_table("rag_lite_ingest_events")
    op.drop_table("rag_lite_ingest_jobs")
    op.drop_index("ix_rag_lite_chunks_embedding_hnsw", table_name="rag_lite_chunks")
    op.drop_table("rag_lite_chunks")
    op.drop_table("rag_lite_documents")
    op.drop_table("rag_lite_collections")
    op.execute("DROP TYPE IF EXISTS rag_lite_event_type")
    op.execute("DROP TYPE IF EXISTS rag_lite_job_status")
    op.execute("DROP TYPE IF EXISTS rag_lite_document_status")
