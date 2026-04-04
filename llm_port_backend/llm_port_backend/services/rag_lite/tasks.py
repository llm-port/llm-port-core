"""Taskiq task for asynchronous RAG Lite document ingest."""

from __future__ import annotations

import logging
import uuid

from llm_port_backend.tkq import broker

log = logging.getLogger(__name__)


@broker.task(retry_on_error=False)
async def rag_lite_ingest_task(document_id: str, job_id: str) -> dict:
    """Process a document through the extract → chunk → embed → store pipeline.

    Credentials and service references are resolved at runtime from the
    application state — nothing sensitive is transmitted over RabbitMQ.
    """
    from llm_port_backend.db.dao.rag_lite_dao import (  # noqa: PLC0415
        RagLiteChunkDAO,
        RagLiteDocumentDAO,
        RagLiteIngestJobDAO,
    )
    from llm_port_backend.db.models.rag_lite import (  # noqa: PLC0415
        RagLiteDocumentStatus,
        RagLiteJobStatus,
    )
    from llm_port_backend.services.rag_lite.embedding import EmbeddingClient  # noqa: PLC0415

    _doc_id = uuid.UUID(document_id)
    _job_id = uuid.UUID(job_id)

    app = broker.state.fastapi_app
    session = app.state.db_session_factory()

    try:
        # Resolve embedding client from configured providers
        embedding_client = await _resolve_embedding_client(app, session)

        # Instantiate DAOs with the worker session
        doc_dao = RagLiteDocumentDAO(session)
        chunk_dao = RagLiteChunkDAO(session)
        job_dao = RagLiteIngestJobDAO(session)

        # Get the service from app state
        rag_lite_service = app.state.rag_lite_service

        # Get document processor if available
        processor = getattr(app.state, "document_processor", None)

        await rag_lite_service.process_document(
            _doc_id,
            _job_id,
            document_dao=doc_dao,
            chunk_dao=chunk_dao,
            job_dao=job_dao,
            embedding_client=embedding_client,
            processor=processor,
        )
        await session.commit()
        return {"status": "completed", "document_id": document_id}

    except Exception as exc:
        log.exception("Ingest task failed for document %s: %s", document_id, exc)
        # Best-effort status update in a fresh session
        err_session = app.state.db_session_factory()
        try:
            err_doc_dao = RagLiteDocumentDAO(err_session)
            err_job_dao = RagLiteIngestJobDAO(err_session)
            await err_doc_dao.update_status(
                _doc_id,
                RagLiteDocumentStatus.ERROR,
            )
            await err_job_dao.update_status(
                _job_id,
                RagLiteJobStatus.FAILED,
                error_message=str(exc),
            )
            await err_session.commit()
        except Exception:
            log.exception("Could not update status after ingest crash")
        finally:
            await err_session.close()
        return {"status": "failed", "error": str(exc)}
    finally:
        await session.close()


async def _resolve_embedding_client(app, session) -> "EmbeddingClient":
    """Build an EmbeddingClient from the configured or auto-detected provider."""
    from llm_port_backend.services.rag_lite.embedding import EmbeddingClient  # noqa: PLC0415
    from llm_port_backend.services.system_settings.crypto import SettingsCrypto  # noqa: PLC0415
    from llm_port_backend.settings import settings  # noqa: PLC0415

    crypto = SettingsCrypto(settings.settings_master_key)

    preferred_id_str = settings.rag_lite_embedding_provider_id
    preferred_id = uuid.UUID(preferred_id_str) if preferred_id_str else None
    model_override = settings.rag_lite_embedding_model or None
    dim = settings.rag_lite_embedding_dim

    return await EmbeddingClient.auto_detect(
        session,
        preferred_provider_id=preferred_id,
        model_override=model_override,
        dim=dim,
        crypto=crypto,
    )
