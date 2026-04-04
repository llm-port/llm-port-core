"""FastAPI endpoints for the RAG Lite admin API.

Registered under ``/admin/rag`` when the full RAG module is disabled
and RAG Lite is enabled — providing a lightweight, pgvector-only
knowledge-base experience.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from starlette import status

from llm_port_backend.db.dao.llm_dao import ModelDAO, ProviderDAO, RuntimeDAO
from llm_port_backend.db.dao.rag_lite_dao import (
    RagLiteChunkDAO,
    RagLiteCollectionDAO,
    RagLiteDocumentDAO,
    RagLiteIngestJobDAO,
)
from llm_port_backend.db.models.rag_lite import RagLiteDocumentStatus, RagLiteIngestJob
from llm_port_backend.db.models.users import User
from llm_port_backend.services.rag_lite.tasks import rag_lite_ingest_task
from llm_port_backend.web.api.admin.rag_lite.schema import (
    RagLiteCollectionCreate,
    RagLiteCollectionDTO,
    RagLiteCollectionUpdate,
    RagLiteConfigDTO,
    RagLiteDocumentDetailDTO,
    RagLiteDocumentDTO,
    RagLiteDocumentMoveRequest,
    RagLiteGraphSearchCollectionHit,
    RagLiteGraphSearchRequest,
    RagLiteGraphSearchResponse,
    RagLiteHealthResponse,
    RagLiteJobDTO,
    RagLiteJobEventDTO,
    RagLiteSearchRequest,
    RagLiteSearchResponse,
    RagLiteSearchResult,
    RagLiteSummaryResponse,
    RagLiteUploadResponse,
)
from llm_port_backend.web.api.rbac import require_permission

router = APIRouter()

# -----------------------------------------------------------------------
# Health
# -----------------------------------------------------------------------


@router.get("/health", response_model=RagLiteHealthResponse)
async def rag_lite_health(
    _user: Annotated[User, Depends(require_permission("rag.search", "read"))],
) -> RagLiteHealthResponse:
    return RagLiteHealthResponse()


# -----------------------------------------------------------------------
# Config (read-only — managed via Settings page)
# -----------------------------------------------------------------------


@router.get("/config", response_model=RagLiteConfigDTO)
async def get_rag_lite_config(
    _user: Annotated[User, Depends(require_permission("rag.runtime", "read"))],
    provider_dao: ProviderDAO = Depends(),
    runtime_dao: RuntimeDAO = Depends(),
    model_dao: ModelDAO = Depends(),
) -> RagLiteConfigDTO:
    from llm_port_backend.settings import settings  # noqa: PLC0415

    embedding_model = settings.rag_lite_embedding_model
    embedding_provider_id = settings.rag_lite_embedding_provider_id

    # Resolve the actual model name when the setting is empty
    if not embedding_model:
        try:
            pref_id = uuid.UUID(embedding_provider_id) if embedding_provider_id else None
            if pref_id:
                provider = await provider_dao.get(pref_id)
            else:
                providers = await provider_dao.list_embedding_capable()
                provider = providers[0] if providers else None

            if provider:
                if not embedding_provider_id:
                    embedding_provider_id = str(provider.id)
                # Try to get model name from a running runtime for this provider
                runtimes = await runtime_dao.list_by_provider(provider.id)
                for rt in runtimes:
                    if rt.status.value == "running":
                        model = await model_dao.get(rt.model_id)
                        if model:
                            embedding_model = model.hf_repo_id or model.display_name
                        break
                # Fallback to remote_model in capabilities
                if not embedding_model:
                    caps = provider.capabilities or {}
                    embedding_model = caps.get("remote_model", "")
        except Exception:
            pass  # best-effort — return empty if resolution fails

    return RagLiteConfigDTO(
        embedding_provider_id=embedding_provider_id,
        embedding_model=embedding_model,
        embedding_dim=settings.rag_lite_embedding_dim,
        chunk_max_tokens=settings.rag_lite_chunk_max_tokens,
        chunk_overlap_tokens=settings.rag_lite_chunk_overlap_tokens,
        file_store_root=settings.rag_lite_file_store_root,
        upload_max_file_mb=settings.rag_lite_upload_max_file_mb,
    )


# -----------------------------------------------------------------------
# Upload
# -----------------------------------------------------------------------


@router.post("/upload", response_model=RagLiteUploadResponse)
async def upload_file(
    file: UploadFile,
    _user: Annotated[User, Depends(require_permission("rag.search", "write"))],
    document_dao: RagLiteDocumentDAO = Depends(),
    job_dao: RagLiteIngestJobDAO = Depends(),
    collection_id: uuid.UUID | None = None,
    request: Request = None,  # type: ignore[assignment]
) -> RagLiteUploadResponse:
    rag_service = request.app.state.rag_lite_service

    # Read file bytes (enforce size limit)
    from llm_port_backend.settings import settings as _settings  # noqa: PLC0415

    max_mb = _settings.rag_lite_upload_max_file_mb
    file_bytes = await file.read()
    if len(file_bytes) > max_mb * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File too large — max {max_mb} MB.",
        )

    doc, job = await rag_service.submit_file(
        file_bytes,
        file.filename or "upload",
        collection_id,
        document_dao=document_dao,
        job_dao=job_dao,
    )

    # Dispatch async ingest task
    await rag_lite_ingest_task.kiq(str(doc.id), str(job.id))

    return RagLiteUploadResponse(
        document_id=doc.id,
        job_id=job.id,
        filename=doc.filename,
        doc_type=doc.doc_type,
    )


# -----------------------------------------------------------------------
# Documents
# -----------------------------------------------------------------------


@router.get("/documents", response_model=list[RagLiteDocumentDTO])
async def list_documents(
    _user: Annotated[User, Depends(require_permission("rag.search", "read"))],
    document_dao: RagLiteDocumentDAO = Depends(),
    collection_id: uuid.UUID | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[RagLiteDocumentDTO]:
    docs = await document_dao.list_by_collection(
        collection_id,
        limit=limit,
        offset=offset,
    )
    return [
        RagLiteDocumentDTO(
            id=d.id,
            filename=d.filename,
            doc_type=d.doc_type,
            collection_id=d.collection_id,
            size_bytes=d.size_bytes,
            chunk_count=d.chunk_count,
            status=d.status.value,
            summary=d.summary,
            created_at=d.created_at,
        )
        for d in docs
    ]


@router.get("/documents/all", response_model=list[RagLiteDocumentDTO])
async def list_all_documents(
    _user: Annotated[User, Depends(require_permission("rag.search", "read"))],
    document_dao: RagLiteDocumentDAO = Depends(),
    limit: int = 500,
    offset: int = 0,
) -> list[RagLiteDocumentDTO]:
    """List all documents regardless of collection — used for tree view."""
    docs = await document_dao.list_all(limit=limit, offset=offset)
    return [
        RagLiteDocumentDTO(
            id=d.id,
            filename=d.filename,
            doc_type=d.doc_type,
            collection_id=d.collection_id,
            size_bytes=d.size_bytes,
            chunk_count=d.chunk_count,
            status=d.status.value,
            summary=d.summary,
            created_at=d.created_at,
        )
        for d in docs
    ]


@router.get("/documents/{document_id}", response_model=RagLiteDocumentDetailDTO)
async def get_document(
    document_id: uuid.UUID,
    _user: Annotated[User, Depends(require_permission("rag.search", "read"))],
    document_dao: RagLiteDocumentDAO = Depends(),
) -> RagLiteDocumentDetailDTO:
    doc = await document_dao.get(document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return RagLiteDocumentDetailDTO(
        id=doc.id,
        filename=doc.filename,
        doc_type=doc.doc_type,
        collection_id=doc.collection_id,
        size_bytes=doc.size_bytes,
        chunk_count=doc.chunk_count,
        status=doc.status.value,
        summary=doc.summary,
        created_at=doc.created_at,
        file_store_key=doc.file_store_key,
        sha256=doc.sha256,
        metadata_json=doc.metadata_json,
    )


@router.delete("/documents/{document_id}", status_code=204)
async def delete_document(
    document_id: uuid.UUID,
    _user: Annotated[User, Depends(require_permission("rag.search", "write"))],
    document_dao: RagLiteDocumentDAO = Depends(),
    chunk_dao: RagLiteChunkDAO = Depends(),
    request: Request = None,  # type: ignore[assignment]
) -> None:
    rag_service = request.app.state.rag_lite_service
    deleted = await rag_service.delete_document(
        document_id,
        document_dao=document_dao,
        chunk_dao=chunk_dao,
    )
    if not deleted:
        raise HTTPException(status_code=404, detail="Document not found")


@router.post("/documents/{document_id}/retry", status_code=200)
async def retry_document(
    document_id: uuid.UUID,
    _user: Annotated[User, Depends(require_permission("rag.search", "write"))],
    document_dao: RagLiteDocumentDAO = Depends(),
    chunk_dao: RagLiteChunkDAO = Depends(),
    job_dao: RagLiteIngestJobDAO = Depends(),
) -> dict:
    doc = await document_dao.get(document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    if doc.status.value not in ("error", "pending"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot retry document in '{doc.status.value}' state.",
        )

    # Clear old chunks from any partial previous run
    await chunk_dao.delete_by_document(document_id)

    # Reset document status
    await document_dao.update_status(
        document_id,
        RagLiteDocumentStatus.PENDING,
        chunk_count=0,
    )

    # Create a new ingest job and dispatch
    job = await job_dao.create(document_id)
    await rag_lite_ingest_task.kiq(str(document_id), str(job.id))

    return {"document_id": str(document_id), "job_id": str(job.id)}


# -----------------------------------------------------------------------
# Search
# -----------------------------------------------------------------------


@router.post("/search", response_model=RagLiteSearchResponse)
async def search(
    body: RagLiteSearchRequest,
    _user: Annotated[User, Depends(require_permission("rag.search", "read"))],
    chunk_dao: RagLiteChunkDAO = Depends(),
    request: Request = None,  # type: ignore[assignment]
) -> RagLiteSearchResponse:
    rag_service = request.app.state.rag_lite_service

    # Resolve embedding client at request time
    from llm_port_backend.services.rag_lite.embedding import EmbeddingClient  # noqa: PLC0415
    from llm_port_backend.services.system_settings.crypto import SettingsCrypto  # noqa: PLC0415
    from llm_port_backend.settings import settings  # noqa: PLC0415

    crypto = SettingsCrypto(settings.settings_master_key)
    pref_id_str = settings.rag_lite_embedding_provider_id
    pref_id = uuid.UUID(pref_id_str) if pref_id_str else None

    session = chunk_dao.session
    embedding_client = await EmbeddingClient.auto_detect(
        session,
        preferred_provider_id=pref_id,
        model_override=settings.rag_lite_embedding_model or None,
        dim=settings.rag_lite_embedding_dim,
        crypto=crypto,
    )

    results = await rag_service.search(
        body.query,
        chunk_dao=chunk_dao,
        embedding_client=embedding_client,
        top_k=body.top_k,
        collection_ids=body.collection_ids,
    )
    return RagLiteSearchResponse(
        query=body.query,
        results=[
            RagLiteSearchResult(
                chunk_text=r["chunk_text"],
                document_id=uuid.UUID(r["document_id"]),
                filename=r["filename"],
                chunk_index=r["chunk_index"],
                score=r["score"],
            )
            for r in results
        ],
    )


# -----------------------------------------------------------------------
# Collections
# -----------------------------------------------------------------------


@router.post("/collections", response_model=RagLiteCollectionDTO, status_code=201)
async def create_collection(
    body: RagLiteCollectionCreate,
    _user: Annotated[User, Depends(require_permission("rag.search", "write"))],
    collection_dao: RagLiteCollectionDAO = Depends(),
) -> RagLiteCollectionDTO:
    col = await collection_dao.create(
        body.name, body.description, parent_id=body.parent_id,
    )
    return RagLiteCollectionDTO(
        id=col.id,
        name=col.name,
        description=col.description,
        parent_id=col.parent_id,
        document_count=0,
        created_at=col.created_at,
        updated_at=col.updated_at,
    )


@router.get("/collections", response_model=list[RagLiteCollectionDTO])
async def list_collections(
    _user: Annotated[User, Depends(require_permission("rag.search", "read"))],
    collection_dao: RagLiteCollectionDAO = Depends(),
) -> list[RagLiteCollectionDTO]:
    rows = await collection_dao.list_with_doc_counts()
    return [
        RagLiteCollectionDTO(
            id=c.id,
            name=c.name,
            description=c.description,
            parent_id=c.parent_id,
            document_count=count,
            created_at=c.created_at,
            updated_at=c.updated_at,
        )
        for c, count in rows
    ]


@router.patch("/collections/{collection_id}", response_model=RagLiteCollectionDTO)
async def update_collection(
    collection_id: uuid.UUID,
    body: RagLiteCollectionUpdate,
    _user: Annotated[User, Depends(require_permission("rag.search", "write"))],
    collection_dao: RagLiteCollectionDAO = Depends(),
) -> RagLiteCollectionDTO:
    col = await collection_dao.update(
        collection_id,
        name=body.name,
        description=body.description,
        parent_id=body.parent_id,
    )
    if col is None:
        raise HTTPException(status_code=404, detail="Collection not found")
    return RagLiteCollectionDTO(
        id=col.id,
        name=col.name,
        description=col.description,
        parent_id=col.parent_id,
        created_at=col.created_at,
        updated_at=col.updated_at,
    )


@router.delete("/collections/{collection_id}", status_code=204)
async def delete_collection(
    collection_id: uuid.UUID,
    _user: Annotated[User, Depends(require_permission("rag.search", "write"))],
    collection_dao: RagLiteCollectionDAO = Depends(),
) -> None:
    deleted = await collection_dao.delete(collection_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Collection not found")


# -----------------------------------------------------------------------
# Jobs
# -----------------------------------------------------------------------


def _job_to_dto(job: RagLiteIngestJob) -> RagLiteJobDTO:
    events = [
        RagLiteJobEventDTO(
            id=e.id,
            event_type=e.event_type.value,
            message=e.message,
            created_at=e.created_at,
        )
        for e in (job.events or [])
    ]
    return RagLiteJobDTO(
        id=job.id,
        document_id=job.document_id,
        status=job.status.value,
        error_message=job.error_message,
        stats_json=job.stats_json,
        created_at=job.created_at,
        updated_at=job.updated_at,
        events=events,
    )


@router.get("/jobs", response_model=list[RagLiteJobDTO])
async def list_jobs(
    _user: Annotated[User, Depends(require_permission("rag.search", "read"))],
    job_dao: RagLiteIngestJobDAO = Depends(),
    limit: int = 50,
) -> list[RagLiteJobDTO]:
    jobs = await job_dao.list_recent(limit=limit)
    return [_job_to_dto(j) for j in jobs]


@router.get("/jobs/{job_id}", response_model=RagLiteJobDTO)
async def get_job(
    job_id: uuid.UUID,
    _user: Annotated[User, Depends(require_permission("rag.search", "read"))],
    job_dao: RagLiteIngestJobDAO = Depends(),
) -> RagLiteJobDTO:
    job = await job_dao.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return _job_to_dto(job)


# -----------------------------------------------------------------------
# Document move / summary
# -----------------------------------------------------------------------


@router.patch("/documents/{document_id}/move", response_model=RagLiteDocumentDTO)
async def move_document(
    document_id: uuid.UUID,
    body: RagLiteDocumentMoveRequest,
    _user: Annotated[User, Depends(require_permission("rag.search", "write"))],
    document_dao: RagLiteDocumentDAO = Depends(),
) -> RagLiteDocumentDTO:
    """Move a document to a different collection (or root)."""
    doc = await document_dao.move_to_collection(document_id, body.collection_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return RagLiteDocumentDTO(
        id=doc.id,
        filename=doc.filename,
        doc_type=doc.doc_type,
        collection_id=doc.collection_id,
        size_bytes=doc.size_bytes,
        chunk_count=doc.chunk_count,
        status=doc.status.value,
        summary=doc.summary,
        created_at=doc.created_at,
    )


@router.patch("/documents/{document_id}/summary")
async def update_document_summary(
    document_id: uuid.UUID,
    body: RagLiteSummaryResponse,
    _user: Annotated[User, Depends(require_permission("rag.search", "write"))],
    document_dao: RagLiteDocumentDAO = Depends(),
) -> RagLiteSummaryResponse:
    """Update a document's summary."""
    doc = await document_dao.update_summary(document_id, body.summary)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    return RagLiteSummaryResponse(summary=doc.summary or "")


# -----------------------------------------------------------------------
# AI Summary Generation
# -----------------------------------------------------------------------


async def _get_completion_client(session: Any) -> Any:
    """Resolve a CompletionClient from available providers."""
    from llm_port_backend.services.rag_lite.completion import CompletionClient  # noqa: PLC0415
    from llm_port_backend.services.system_settings.crypto import SettingsCrypto  # noqa: PLC0415
    from llm_port_backend.settings import settings  # noqa: PLC0415

    crypto = SettingsCrypto(settings.settings_master_key)
    return await CompletionClient.auto_detect(session, crypto=crypto)


@router.post(
    "/collections/{collection_id}/generate-summary",
    response_model=RagLiteSummaryResponse,
)
async def generate_collection_summary(
    collection_id: uuid.UUID,
    _user: Annotated[User, Depends(require_permission("rag.search", "write"))],
    collection_dao: RagLiteCollectionDAO = Depends(),
    document_dao: RagLiteDocumentDAO = Depends(),
) -> RagLiteSummaryResponse:
    """Generate an AI summary for a collection based on its documents."""
    col = await collection_dao.get(collection_id)
    if col is None:
        raise HTTPException(status_code=404, detail="Collection not found")

    docs = await document_dao.list_by_collection(collection_id, limit=50)
    doc_names = [d.filename for d in docs]
    doc_summaries = [d.summary for d in docs]

    try:
        client = await _get_completion_client(collection_dao.session)
        summary = await client.generate_collection_summary(
            col.name, doc_names, doc_summaries,
        )
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    # Persist the generated summary
    await collection_dao.update(collection_id, description=summary)
    return RagLiteSummaryResponse(summary=summary)


@router.post(
    "/documents/{document_id}/generate-summary",
    response_model=RagLiteSummaryResponse,
)
async def generate_document_summary(
    document_id: uuid.UUID,
    _user: Annotated[User, Depends(require_permission("rag.search", "write"))],
    document_dao: RagLiteDocumentDAO = Depends(),
) -> RagLiteSummaryResponse:
    """Generate an AI summary for a document based on its content."""
    doc = await document_dao.get(document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")

    try:
        client = await _get_completion_client(document_dao.session)
        summary = await client.generate_document_summary(
            doc.filename, doc.content_text,
        )
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    # Persist the generated summary
    await document_dao.update_summary(document_id, summary)
    return RagLiteSummaryResponse(summary=summary)


# -----------------------------------------------------------------------
# Graph-RAG Search (two-level)
# -----------------------------------------------------------------------


@router.post("/search/graph", response_model=RagLiteGraphSearchResponse)
async def graph_search(
    body: RagLiteGraphSearchRequest,
    _user: Annotated[User, Depends(require_permission("rag.search", "read"))],
    collection_dao: RagLiteCollectionDAO = Depends(),
    chunk_dao: RagLiteChunkDAO = Depends(),
    request: Request = None,  # type: ignore[assignment]
) -> RagLiteGraphSearchResponse:
    """Two-level graph-RAG search.

    Level 1: Embed the query, compare against collection summaries to
    find the most relevant collections.

    Level 2: Run chunk-level vector search within those collections.
    """
    from llm_port_backend.services.rag_lite.embedding import EmbeddingClient  # noqa: PLC0415
    from llm_port_backend.services.system_settings.crypto import SettingsCrypto  # noqa: PLC0415
    from llm_port_backend.settings import settings  # noqa: PLC0415

    crypto = SettingsCrypto(settings.settings_master_key)
    pref_id_str = settings.rag_lite_embedding_provider_id
    pref_id = uuid.UUID(pref_id_str) if pref_id_str else None

    session = chunk_dao.session
    embedding_client = await EmbeddingClient.auto_detect(
        session,
        preferred_provider_id=pref_id,
        model_override=settings.rag_lite_embedding_model or None,
        dim=settings.rag_lite_embedding_dim,
        crypto=crypto,
    )

    # Level 1: Find best collections by embedding summaries
    rows = await collection_dao.list_with_doc_counts()
    cols_with_desc = [
        (c, count) for c, count in rows if c.description and c.description.strip()
    ]

    collection_hits: list[RagLiteGraphSearchCollectionHit] = []
    target_collection_ids: list[uuid.UUID] = []

    if cols_with_desc:
        # Embed query + all collection descriptions in one batch
        texts_to_embed = [body.query] + [c.description for c, _ in cols_with_desc]  # type: ignore[misc]
        all_vectors = await embedding_client.embed_texts(texts_to_embed)
        query_vec = all_vectors[0]

        # Compute cosine similarity for each collection
        scored: list[tuple[Any, int, float]] = []
        for i, (col, count) in enumerate(cols_with_desc):
            col_vec = all_vectors[i + 1]
            sim = _cosine_similarity(query_vec, col_vec)
            scored.append((col, count, sim))

        scored.sort(key=lambda x: x[2], reverse=True)
        top = scored[: body.top_k_collections]

        for col, count, sim in top:
            collection_hits.append(
                RagLiteGraphSearchCollectionHit(
                    collection_id=col.id,
                    collection_name=col.name,
                    score=sim,
                ),
            )
            target_collection_ids.append(col.id)

    # Level 2: Search chunks within the matched collections
    if target_collection_ids:
        results = await chunk_dao.search_similar(
            query_vector=query_vec,
            top_k=body.top_k_chunks,
            collection_ids=target_collection_ids,
        )
    else:
        # No collections matched — search all
        query_vecs = await embedding_client.embed_texts([body.query])
        results = await chunk_dao.search_similar(
            query_vector=query_vecs[0],
            top_k=body.top_k_chunks,
        )

    return RagLiteGraphSearchResponse(
        query=body.query,
        collection_hits=collection_hits,
        results=[
            RagLiteSearchResult(
                chunk_text=r["chunk_text"],
                document_id=uuid.UUID(r["document_id"]),
                filename=r["filename"],
                chunk_index=r["chunk_index"],
                score=r["score"],
            )
            for r in results
        ],
    )


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Compute cosine similarity between two vectors."""
    import math  # noqa: PLC0415

    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
