"""DAOs for the RAG Lite subsystem."""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import Depends
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.dependencies import get_db_session
from llm_port_backend.db.models.rag_lite import (
    RagLiteChunk,
    RagLiteCollection,
    RagLiteDocument,
    RagLiteDocumentStatus,
    RagLiteEventType,
    RagLiteIngestEvent,
    RagLiteIngestJob,
    RagLiteJobStatus,
)


# -----------------------------------------------------------------------
# Collection DAO
# -----------------------------------------------------------------------


class RagLiteCollectionDAO:
    """CRUD operations for RAG Lite collections."""

    def __init__(self, session: AsyncSession = Depends(get_db_session)) -> None:
        self.session = session

    async def create(
        self,
        name: str,
        description: str | None = None,
        parent_id: uuid.UUID | None = None,
    ) -> RagLiteCollection:
        col = RagLiteCollection(
            id=uuid.uuid4(),
            name=name,
            description=description,
            parent_id=parent_id,
        )
        self.session.add(col)
        await self.session.flush()
        return col

    async def get(self, collection_id: uuid.UUID) -> RagLiteCollection | None:
        result = await self.session.execute(
            select(RagLiteCollection).where(RagLiteCollection.id == collection_id),
        )
        return result.scalar_one_or_none()

    async def list_all(self) -> list[RagLiteCollection]:
        result = await self.session.execute(
            select(RagLiteCollection).order_by(RagLiteCollection.name),
        )
        return list(result.scalars().all())

    async def update(
        self,
        collection_id: uuid.UUID,
        *,
        name: str | None = None,
        description: str | None = ...,  # type: ignore[assignment]
        parent_id: uuid.UUID | None = ...,  # type: ignore[assignment]
    ) -> RagLiteCollection | None:
        col = await self.get(collection_id)
        if col is None:
            return None
        if name is not None:
            col.name = name
        if description is not ...:
            col.description = description
        if parent_id is not ...:
            col.parent_id = parent_id
        return col

    async def list_with_doc_counts(self) -> list[tuple[RagLiteCollection, int]]:
        """Return all collections with their direct document counts."""
        stmt = (
            select(
                RagLiteCollection,
                func.count(RagLiteDocument.id).label("doc_count"),
            )
            .outerjoin(
                RagLiteDocument,
                RagLiteDocument.collection_id == RagLiteCollection.id,
            )
            .group_by(RagLiteCollection.id)
            .order_by(RagLiteCollection.name)
        )
        result = await self.session.execute(stmt)
        return [(row[0], row[1]) for row in result.all()]

    async def delete(self, collection_id: uuid.UUID) -> bool:
        col = await self.get(collection_id)
        if col is None:
            return False
        await self.session.delete(col)
        return True


# -----------------------------------------------------------------------
# Document DAO
# -----------------------------------------------------------------------


class RagLiteDocumentDAO:
    """CRUD operations for RAG Lite documents."""

    def __init__(self, session: AsyncSession = Depends(get_db_session)) -> None:
        self.session = session

    async def create(
        self,
        *,
        filename: str,
        doc_type: str,
        size_bytes: int,
        sha256: str,
        collection_id: uuid.UUID | None = None,
        content_text: str | None = None,
        metadata_json: dict | None = None,
        chunk_count: int = 0,
        status: RagLiteDocumentStatus = RagLiteDocumentStatus.PENDING,
        file_store_key: str | None = None,
    ) -> RagLiteDocument:
        doc = RagLiteDocument(
            id=uuid.uuid4(),
            collection_id=collection_id,
            filename=filename,
            doc_type=doc_type,
            content_text=content_text,
            size_bytes=size_bytes,
            sha256=sha256,
            metadata_json=metadata_json,
            chunk_count=chunk_count,
            status=status,
            file_store_key=file_store_key,
        )
        self.session.add(doc)
        await self.session.flush()
        return doc

    async def get(self, document_id: uuid.UUID) -> RagLiteDocument | None:
        result = await self.session.execute(
            select(RagLiteDocument).where(RagLiteDocument.id == document_id),
        )
        return result.scalar_one_or_none()

    async def list_by_collection(
        self,
        collection_id: uuid.UUID | None = None,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[RagLiteDocument]:
        query = select(RagLiteDocument).order_by(RagLiteDocument.created_at.desc())
        if collection_id is not None:
            query = query.where(RagLiteDocument.collection_id == collection_id)
        query = query.limit(limit).offset(offset)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def exists_by_sha256(self, sha256: str) -> bool:
        result = await self.session.execute(
            select(RagLiteDocument.id)
            .where(RagLiteDocument.sha256 == sha256)
            .limit(1),
        )
        return result.scalar_one_or_none() is not None

    async def update_status(
        self,
        document_id: uuid.UUID,
        status: RagLiteDocumentStatus,
        *,
        content_text: str | None = ...,  # type: ignore[assignment]
        metadata_json: dict | None = ...,  # type: ignore[assignment]
        chunk_count: int | None = None,
    ) -> RagLiteDocument | None:
        doc = await self.get(document_id)
        if doc is None:
            return None
        doc.status = status
        if content_text is not ...:
            doc.content_text = content_text
        if metadata_json is not ...:
            doc.metadata_json = metadata_json
        if chunk_count is not None:
            doc.chunk_count = chunk_count
        return doc

    async def move_to_collection(
        self,
        document_id: uuid.UUID,
        collection_id: uuid.UUID | None,
    ) -> RagLiteDocument | None:
        """Move a document to a different collection (or root if None)."""
        doc = await self.get(document_id)
        if doc is None:
            return None
        doc.collection_id = collection_id
        # Also update the denormalised collection_id on chunks
        await self.session.execute(
            update(RagLiteChunk)
            .where(RagLiteChunk.document_id == document_id)
            .values(collection_id=collection_id),
        )
        return doc

    async def update_summary(
        self,
        document_id: uuid.UUID,
        summary: str | None,
    ) -> RagLiteDocument | None:
        doc = await self.get(document_id)
        if doc is None:
            return None
        doc.summary = summary
        return doc

    async def list_all(
        self,
        *,
        limit: int = 500,
        offset: int = 0,
    ) -> list[RagLiteDocument]:
        """Return all documents regardless of collection."""
        query = (
            select(RagLiteDocument)
            .order_by(RagLiteDocument.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def delete(self, document_id: uuid.UUID) -> bool:
        doc = await self.get(document_id)
        if doc is None:
            return False
        await self.session.delete(doc)
        return True


# -----------------------------------------------------------------------
# Chunk DAO
# -----------------------------------------------------------------------


class RagLiteChunkDAO:
    """Operations for RAG Lite chunks (bulk insert + vector search)."""

    def __init__(self, session: AsyncSession = Depends(get_db_session)) -> None:
        self.session = session

    async def bulk_create(self, chunks: list[dict[str, Any]]) -> int:
        """Batch-insert chunks. Each dict must contain the column values.

        Returns the number of chunks inserted.
        """
        if not chunks:
            return 0
        objs = [RagLiteChunk(**c) for c in chunks]
        self.session.add_all(objs)
        await self.session.flush()
        return len(objs)

    async def passage(
        self,
        document_id: uuid.UUID,
        *,
        first: int,
        last: int,
    ) -> list[RagLiteChunk]:
        """The chunks of a document from *first* to *last*, in order."""
        result = await self.session.execute(
            select(RagLiteChunk)
            .where(
                RagLiteChunk.document_id == document_id,
                RagLiteChunk.chunk_index >= first,
                RagLiteChunk.chunk_index <= last,
            )
            .order_by(RagLiteChunk.chunk_index),
        )
        return list(result.scalars().all())

    async def delete_by_document(self, document_id: uuid.UUID) -> int:
        stmt = sa_delete(RagLiteChunk).where(
            RagLiteChunk.document_id == document_id,
        )
        result = await self.session.execute(stmt)
        return result.rowcount  # type: ignore[return-value]

    async def search_similar(
        self,
        query_vector: list[float],
        top_k: int = 5,
        collection_ids: list[uuid.UUID] | None = None,
    ) -> list[dict[str, Any]]:
        """Cosine-similarity search via pgvector ``<=>`` operator.

        Returns a list of dicts with ``chunk_text``, ``chunk_index``,
        ``document_id``, ``filename``, and ``score``.
        """
        # Build parameterised raw SQL — pgvector operators are not yet
        # first-class in the SQLAlchemy ORM layer.
        filters = ""
        params: dict[str, Any] = {
            "query": str(query_vector),
            "top_k": top_k,
        }
        if collection_ids:
            filters = "AND c.collection_id = ANY(CAST(:cids AS uuid[]))"
            params["cids"] = [str(cid) for cid in collection_ids]
            # When Postgres uses the HNSW index here, it finds the nearest
            # chunks of all collections and the filter then drops the others'
            # -- with a small collection among large ones, fewer results than
            # asked, or none (pgvector's documented filtering limit; at the
            # sizes measured so far Postgres chose an exact scan instead).
            # pgvector 0.8 keeps scanning until the filter is met; its
            # results can come slightly out of order, hence the outer sort.
            await self.session.execute(text("SET LOCAL hnsw.iterative_scan = relaxed_order"))

        sql = text(
            f"""
            SELECT * FROM (
                SELECT c.id,
                       c.chunk_text,
                       c.chunk_index,
                       d.filename,
                       d.id AS document_id,
                       c.embedding <=> CAST(:query AS vector) AS distance
                FROM rag_lite_chunks c
                JOIN rag_lite_documents d ON d.id = c.document_id
                WHERE c.embedding IS NOT NULL
                  {filters}
                ORDER BY c.embedding <=> CAST(:query AS vector)
                LIMIT :top_k
            ) nearest
            ORDER BY distance
            """,
        )
        result = await self.session.execute(sql, params)
        rows = result.mappings().all()
        return [
            {
                "chunk_id": str(r["id"]),
                "chunk_text": r["chunk_text"],
                "chunk_index": r["chunk_index"],
                "filename": r["filename"],
                "document_id": str(r["document_id"]),
                "score": 1.0 - float(r["distance"]),
            }
            for r in rows
        ]

    async def search_lexical(
        self,
        query: str,
        top_k: int = 5,
        collection_ids: list[uuid.UUID] | None = None,
    ) -> list[dict[str, Any]]:
        """Keyword search over ``content_tsv``, ranked by BM25.

        The query's words are stemmed like the index (English) and any of
        them may match: a question rarely shares all its words with the
        passage that answers it. Ranked by BM25 (k1 = 1.2, b = 0.75) over the
        searched collections: a rare word counts for more than a common one.
        Postgres's own ``ts_rank_cd`` has no such weighting; fused with vector
        search it pulled good results down (RAGBench hit@1 0.92 -> 0.72 on
        hotpotqa).

        The words go into the query as the lexemes they already are
        (``simple``, no second stemming). A chunk's length is its count of
        distinct words -- what a ``tsvector`` keeps (``content_len``). The
        per-word counts are computed once (``MATERIALIZED``): inlined,
        Postgres re-ran them for every matched word of every chunk -- 2.2 s
        for one search.

        Every chunk with a telling word is scored, on the query's words alone:
        ``setweight`` marks them in the chunk's ``tsvector`` and ``ts_filter``
        keeps the marked ones, so a chunk is taken apart into a few words, not
        all of its hundred. Shortlisting the 500 best by ``ts_rank_cd`` first
        cost more than it saved (techqa: 115 ms against 49 ms) and dropped
        chunks BM25 ranks in the top ten.
        """
        filters = ""
        params: dict[str, Any] = {"query": query, "top_k": top_k}
        if collection_ids:
            filters = "AND c.collection_id = ANY(CAST(:cids AS uuid[]))"
            params["cids"] = [str(cid) for cid in collection_ids]
        sql = text(
            f"""
            WITH terms AS MATERIALIZED (
                SELECT DISTINCT lexeme
                FROM unnest(tsvector_to_array(to_tsvector('english', :query))) AS lexeme
            ),
            stats AS MATERIALIZED (
                SELECT count(*)::float8 AS n, greatest(avg(c.content_len), 1)::float8 AS avgdl
                FROM rag_lite_chunks c
                WHERE TRUE {filters}
            ),
            df AS MATERIALIZED (
                SELECT t.lexeme,
                       (SELECT count(*) FROM rag_lite_chunks c
                        WHERE c.content_tsv @@ to_tsquery('simple', quote_literal(t.lexeme)) {filters}
                       )::float8 AS df
                FROM terms t
            ),
            -- Candidates by the words that tell chunks apart: one in more than
            -- half the collection weighs next to nothing in BM25, yet matched
            -- nearly every chunk of a large collection, and each was scored.
            q AS MATERIALIZED (
                SELECT to_tsquery('simple', string_agg(quote_literal(df.lexeme), ' | ')) AS query,
                       array_agg(df.lexeme) AS lexemes
                FROM df, stats
                WHERE df.df > 0 AND df.df <= stats.n * 0.5
            ),
            matches AS MATERIALIZED (
                SELECT c.id, ts_filter(setweight(c.content_tsv, 'A', q.lexemes), '{{a}}') AS hits,
                       c.content_len::float8 AS dl
                FROM rag_lite_chunks c, q
                WHERE q.query IS NOT NULL AND c.content_tsv @@ q.query {filters}
            ),
            scored AS (
                SELECT m.id,
                       sum(
                           ln(1 + (st.n - df.df + 0.5) / (df.df + 0.5))
                           * (array_length(u.positions, 1) * 2.2)
                           / (array_length(u.positions, 1) + 1.2 * (0.25 + 0.75 * m.dl / st.avgdl))
                       ) AS score
                FROM matches m
                CROSS JOIN LATERAL unnest(m.hits) AS u(lexeme, positions, weights)
                JOIN df ON df.lexeme = u.lexeme
                CROSS JOIN stats st
                GROUP BY m.id
                ORDER BY score DESC
                LIMIT :top_k
            )
            SELECT c.id, c.chunk_text, c.chunk_index, d.filename, d.id AS document_id, s.score
            FROM scored s
            JOIN rag_lite_chunks c ON c.id = s.id
            JOIN rag_lite_documents d ON d.id = c.document_id
            ORDER BY s.score DESC
            """,
        )
        rows = (await self.session.execute(sql, params)).mappings().all()
        return [
            {
                "chunk_id": str(r["id"]),
                "chunk_text": r["chunk_text"],
                "chunk_index": r["chunk_index"],
                "filename": r["filename"],
                "document_id": str(r["document_id"]),
                "score": float(r["score"]),
            }
            for r in rows
        ]


# -----------------------------------------------------------------------
# Ingest Job DAO
# -----------------------------------------------------------------------


class RagLiteIngestJobDAO:
    """CRUD operations for RAG Lite ingest jobs."""

    def __init__(self, session: AsyncSession = Depends(get_db_session)) -> None:
        self.session = session

    async def create(
        self,
        document_id: uuid.UUID,
    ) -> RagLiteIngestJob:
        job = RagLiteIngestJob(
            id=uuid.uuid4(),
            document_id=document_id,
            status=RagLiteJobStatus.QUEUED,
        )
        self.session.add(job)
        await self.session.flush()
        return job

    async def get(self, job_id: uuid.UUID) -> RagLiteIngestJob | None:
        result = await self.session.execute(
            select(RagLiteIngestJob).where(RagLiteIngestJob.id == job_id),
        )
        return result.scalar_one_or_none()

    async def update_status(
        self,
        job_id: uuid.UUID,
        status: RagLiteJobStatus,
        *,
        error_message: str | None = None,
        stats_json: dict | None = None,
    ) -> RagLiteIngestJob | None:
        job = await self.get(job_id)
        if job is None:
            return None
        job.status = status
        if error_message is not None:
            job.error_message = error_message
        if stats_json is not None:
            job.stats_json = stats_json
        return job

    async def list_by_document(
        self,
        document_id: uuid.UUID,
    ) -> list[RagLiteIngestJob]:
        result = await self.session.execute(
            select(RagLiteIngestJob)
            .where(RagLiteIngestJob.document_id == document_id)
            .order_by(RagLiteIngestJob.created_at.desc()),
        )
        return list(result.scalars().all())

    async def list_recent(
        self,
        *,
        limit: int = 50,
        status_filter: RagLiteJobStatus | None = None,
    ) -> list[RagLiteIngestJob]:
        query = select(RagLiteIngestJob).order_by(
            RagLiteIngestJob.created_at.desc(),
        )
        if status_filter:
            query = query.where(RagLiteIngestJob.status == status_filter)
        query = query.limit(limit)
        result = await self.session.execute(query)
        return list(result.scalars().all())

    async def add_event(
        self,
        job_id: uuid.UUID,
        event_type: RagLiteEventType,
        message: str,
    ) -> RagLiteIngestEvent:
        event = RagLiteIngestEvent(
            id=uuid.uuid4(),
            job_id=job_id,
            event_type=event_type,
            message=message,
        )
        self.session.add(event)
        await self.session.flush()
        return event
