"""RAG Lite chunks get a keyword index, for hybrid search.

Search was by vector alone: exact terms -- product codes, error numbers,
names -- that an embedding blurs were easy to miss. ``content_tsv`` is a
generated full-text column (English: stemmed, stop words out) over the chunk
and its ``context`` (what the chunk belongs to, set at ingest), with a GIN
index. ``content_len`` -- its count of distinct words -- is BM25's document
length. Being generated, both fill themselves for the chunks already stored.

Revision ID: h1br1dsrch
Revises: 4d0pt10ns
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "h1br1dsrch"
down_revision = "4d0pt10ns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # A stored generated column rewrites the table, which rebuilds its HNSW
    # index. Built in parallel, the graph goes to shared memory: in Docker's
    # default 64 MB /dev/shm that failed ("could not resize shared memory
    # segment ... No space left on device") at 8,000 chunks. Built by one
    # process it needs none, and memory to keep the graph whole -- 64 MB held
    # 7,681 padded vectors before the build slowed down.
    op.execute("SET LOCAL max_parallel_maintenance_workers = 0")
    op.execute("SET LOCAL maintenance_work_mem = '512MB'")
    op.add_column("rag_lite_chunks", sa.Column("context", sa.Text(), nullable=True))
    op.execute(
        "ALTER TABLE rag_lite_chunks ADD COLUMN content_tsv tsvector "
        "GENERATED ALWAYS AS (to_tsvector('english', coalesce(context, '') || ' ' || chunk_text)) STORED",
    )
    op.execute(
        "ALTER TABLE rag_lite_chunks ADD COLUMN content_len integer "
        "GENERATED ALWAYS AS (length(to_tsvector('english', coalesce(context, '') || ' ' || chunk_text))) STORED",
    )
    op.execute("CREATE INDEX ix_rag_lite_chunks_content_tsv ON rag_lite_chunks USING gin (content_tsv)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_rag_lite_chunks_content_tsv")
    op.drop_column("rag_lite_chunks", "content_len")
    op.drop_column("rag_lite_chunks", "content_tsv")
    op.drop_column("rag_lite_chunks", "context")
