"""RAG Lite service orchestrator.

Coordinates: upload → store → extract → chunk → embed → persist.
"""

from __future__ import annotations

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
            chunks = chunk_text(content_text, self.chunker_config)
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
    ) -> list[dict[str, Any]]:
        """Embed *query* and run pgvector cosine search."""
        vectors = await embedding_client.embed_texts([query])
        query_vector = vectors[0]
        return await chunk_dao.search_similar(
            query_vector=query_vector,
            top_k=top_k,
            collection_ids=collection_ids,
        )

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
