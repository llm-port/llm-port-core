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

        sql = text(
            f"""
            SELECT c.id,
                   c.chunk_text,
                   c.chunk_index,
                   d.filename,
                   d.id AS document_id,
                   1 - (c.embedding <=> CAST(:query AS vector)) AS score
            FROM rag_lite_chunks c
            JOIN rag_lite_documents d ON d.id = c.document_id
            WHERE c.embedding IS NOT NULL
              {filters}
            ORDER BY c.embedding <=> CAST(:query AS vector)
            LIMIT :top_k
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
