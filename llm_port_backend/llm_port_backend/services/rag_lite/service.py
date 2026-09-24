"""RAG Lite service orchestrator.

Coordinates: upload → store → extract → chunk → embed → persist.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
import uuid
from typing import Any

from llm_port_backend.db.dao.rag_lite_dao import (
    RagLiteChunkDAO,
    RagLiteCollectionDAO,
    RagLiteDocumentDAO,
    RagLiteIngestJobDAO,
)
from llm_port_backend.db.models.rag_lite import (
    MAX_EMBEDDING_DIM,
    RagLiteDocumentStatus,
    RagLiteEventType,
    RagLiteJobStatus,
)
from llm_port_backend.services.rag_lite.rerank import RerankClient, fuse
from llm_port_backend.services.rag_lite.chunker import ChunkerConfig, chunk_text
from llm_port_backend.services.rag_lite.embedding import EmbeddingClient
from llm_port_backend.services.rag_lite.file_store import FileStore

log = logging.getLogger(__name__)


def _detect_doc_type(filename: str) -> str:
    """Infer document type from filename extension."""
    suffix = filename.rsplit(".", maxsplit=1)[-1].lower() if "." in filename else ""
    return suffix or "unknown"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class RagLiteService:
    """Orchestrates the RAG Lite pipeline."""

    def __init__(
        self,
        file_store: FileStore,
        chunker_config: ChunkerConfig | None = None,
    ) -> None:
        self.file_store = file_store
        self.chunker_config = chunker_config or ChunkerConfig()

    # ------------------------------------------------------------------
    # Upload (fast path — stores file + queues async ingest)
    # ------------------------------------------------------------------

    async def submit_file(
        self,
        file_bytes: bytes,
        filename: str,
        collection_id: uuid.UUID | None,
        *,
        document_dao: RagLiteDocumentDAO,
        job_dao: RagLiteIngestJobDAO,
    ) -> tuple[Any, Any]:
        """Store the uploaded file and queue an ingest job.

        Returns ``(document, job)`` — both in ``pending``/``queued`` state.
        The actual processing happens asynchronously in the Taskiq worker.
        """
        sha = _sha256(file_bytes)
        doc_type = _detect_doc_type(filename)

        # Build a safe file-store key
        col_key = str(collection_id) if collection_id else "default"
        doc_id = uuid.uuid4()
        file_key = f"{col_key}/{doc_id}/{filename}"

        # Persist raw file
        await self.file_store.put_bytes(file_key, file_bytes)

        # Create document record
        doc = await document_dao.create(
            filename=filename,
            doc_type=doc_type,
            size_bytes=len(file_bytes),
            sha256=sha,
            collection_id=collection_id,
            status=RagLiteDocumentStatus.PENDING,
            file_store_key=file_key,
        )
        # Overwrite auto-generated id with our pre-computed one so it
        # matches the file_key.
        doc.id = doc_id

        # Create ingest job
        job = await job_dao.create(doc.id)

        return doc, job

    # ------------------------------------------------------------------
    # Ingest pipeline (runs in Taskiq worker)
    # ------------------------------------------------------------------

    async def process_document(
        self,
        document_id: uuid.UUID,
        job_id: uuid.UUID,
        *,
        document_dao: RagLiteDocumentDAO,
        chunk_dao: RagLiteChunkDAO,
        job_dao: RagLiteIngestJobDAO,
        embedding_client: EmbeddingClient,
        processor: Any | None = None,
    ) -> None:
        """Full ingest pipeline for a single document.

        Called by the Taskiq worker — not in the request path.
        """
        t0 = time.monotonic()

        await job_dao.update_status(job_id, RagLiteJobStatus.RUNNING)
        await document_dao.update_status(
            document_id,
            RagLiteDocumentStatus.PROCESSING,
        )
        await job_dao.add_event(job_id, RagLiteEventType.INFO, "Ingest started")

        doc = await document_dao.get(document_id)
        if doc is None:
            await job_dao.update_status(
                job_id,
                RagLiteJobStatus.FAILED,
                error_message="Document not found",
            )
            return

        try:
            # 1. Fetch file from store
            file_bytes = await self.file_store.get_bytes(doc.file_store_key or "")
            await job_dao.add_event(
                job_id,
                RagLiteEventType.INFO,
                f"Fetched file ({len(file_bytes)} bytes)",
            )

            # 2. Extract text
            if processor is None:
                from llm_port_backend.services.docling.processor import (  # noqa: PLC0415
                    DocumentProcessor,
                )

                processor = DocumentProcessor()

            result = await processor.process(file_bytes, doc.filename)
            content_text = result.get("content", "")
            metadata = result.get("metadata", {})

            await job_dao.add_event(
                job_id,
                RagLiteEventType.INFO,
                f"Text extracted ({len(content_text)} chars)",
            )

            # 3. Chunk
            # The sizes as set now (the worker reads the settings per job),
            # not as they were when this process started.
            from llm_port_backend.settings import settings  # noqa: PLC0415

            chunker_config = ChunkerConfig(
                max_tokens=settings.rag_lite_chunk_max_tokens,
                overlap_tokens=settings.rag_lite_chunk_overlap_tokens,
            )
            chunks = chunk_text(content_text, chunker_config)
            await job_dao.add_event(
                job_id,
                RagLiteEventType.INFO,
                f"Chunked into {len(chunks)} chunks",
            )

            # 4. Embed
            if chunks:
                texts = [c.text for c in chunks]
                vectors = await embedding_client.embed_texts(texts)
            else:
                vectors = []

            await job_dao.add_event(
                job_id,
                RagLiteEventType.INFO,
                f"Embedded {len(vectors)} chunks",
            )

            # 5. Persist chunks
            chunk_records = [
                {
                    "id": uuid.uuid4(),
                    "document_id": document_id,
                    "collection_id": doc.collection_id,
                    "chunk_index": c.index,
                    "chunk_text": c.text,
                    "embedding": v,
                    "embedding_dim": embedding_client.dim,
                }
                for c, v in zip(chunks, vectors)
            ]
            # A document ingested again -- a message redelivered after a
            # crash, a retry -- replaces its chunks rather than adding to them.
            await chunk_dao.delete_by_document(document_id)
            inserted = await chunk_dao.bulk_create(chunk_records)

            # 6. Update document status
            elapsed = time.monotonic() - t0
            await document_dao.update_status(
                document_id,
                RagLiteDocumentStatus.READY,
                content_text=content_text,
                metadata_json=metadata,
                chunk_count=inserted,
            )
            await job_dao.update_status(
                job_id,
                RagLiteJobStatus.COMPLETED,
                stats_json={
                    "chunk_count": inserted,
                    "text_length": len(content_text),
                    "elapsed_ms": int(elapsed * 1000),
                },
            )
            await job_dao.add_event(
                job_id,
                RagLiteEventType.INFO,
                f"Ingest completed — {inserted} chunks in {elapsed:.1f}s",
            )

        except Exception as exc:
            log.exception("Ingest failed for document %s", document_id)
            await document_dao.update_status(
                document_id,
                RagLiteDocumentStatus.ERROR,
            )
            await job_dao.update_status(
                job_id,
                RagLiteJobStatus.FAILED,
                error_message=str(exc),
            )
            await job_dao.add_event(
                job_id,
                RagLiteEventType.ERROR,
                f"Ingest failed: {exc}",
            )

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    async def search(
        self,
        query: str,
        *,
        chunk_dao: RagLiteChunkDAO,
        embedding_client: EmbeddingClient,
        top_k: int = 5,
        collection_ids: list[uuid.UUID] | None = None,
        hybrid: bool = False,
        reranker: RerankClient | None = None,
        candidates: int = 10,
    ) -> list[dict[str, Any]]:
        """The *top_k* chunks for *query*.

        By vector alone, or -- *hybrid* -- by vector and by keyword, the two
        rankings fused by rank. With a *reranker*, the best *candidates* of
        that are re-scored by it and re-ordered. When the reranker fails, the
        fused order stands: a search is not failed for want of re-ordering.
        """
        pool = max(top_k, candidates) if reranker is not None else top_k
        # Deeper lists for fusion: a chunk ranked 40th by one and 3rd by the
        # other should still make it in.
        depth = max(pool, 50) if hybrid else pool
        # The keyword search needs no vector: it runs while the query is
        # being embedded, not after.
        embedding = asyncio.ensure_future(embedding_client.embed_texts([query]))
        try:
            lexical = (
                await chunk_dao.search_lexical(query, top_k=depth, collection_ids=collection_ids)
                if hybrid else []
            )
            vectors = await embedding
        finally:
            embedding.cancel()
        ranked = await chunk_dao.search_similar(
            query_vector=vectors[0],
            top_k=depth,
            collection_ids=collection_ids,
        )
        if hybrid:
            ranked = fuse(ranked, lexical)
        ranked = ranked[:pool]
        if reranker is not None and ranked:
            try:
                scores = await reranker.rerank(query, [r["chunk_text"] for r in ranked])
            except Exception:
                log.warning("Reranking failed; keeping the search order", exc_info=True)
            else:
                ranked = sorted(
                    ({**r, "score": s} for r, s in zip(ranked, scores, strict=True)),
                    key=lambda r: r["score"],
                    reverse=True,
                )
        return ranked[:top_k]

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    async def delete_document(
        self,
        document_id: uuid.UUID,
        *,
        document_dao: RagLiteDocumentDAO,
        chunk_dao: RagLiteChunkDAO,
    ) -> bool:
        """Delete a document, its chunks, and storage artefact."""
        doc = await document_dao.get(document_id)
        if doc is None:
            return False
        # Remove stored file
        if doc.file_store_key:
            await self.file_store.delete(doc.file_store_key)
        # Cascade deletes chunks + jobs via FK
        await document_dao.delete(document_id)
        return True
