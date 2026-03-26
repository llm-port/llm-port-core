"""Unified Scheduler API — aggregates all background job types."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from starlette import status

from llm_port_backend.db.dao.audit_dao import AuditDAO
from llm_port_backend.db.dao.llm_dao import DownloadJobDAO, ModelDAO
from llm_port_backend.db.dao.rag_lite_dao import RagLiteIngestJobDAO
from llm_port_backend.db.models.containers import AuditResult
from llm_port_backend.db.models.llm import DownloadJobStatus, ModelStatus
from llm_port_backend.db.models.rag_lite import RagLiteJobStatus
from llm_port_backend.db.models.users import User
from llm_port_backend.settings import settings
from llm_port_backend.web.api.admin.dependencies import audit_action
from llm_port_backend.web.api.rbac import require_permission

router = APIRouter()

# ── Unified DTO ──────────────────────────────────────────────────────


class UnifiedJobDTO(BaseModel):
    """A job normalised across all background-task types."""

    id: uuid.UUID
    job_type: str  # "model_download" | "rag_ingest"
    status: str  # normalised: queued | running | success | failed | canceled
    label: str  # human-readable subject (model name, document name…)
    progress: int  # 0-100 (-1 = indeterminate)
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime
    meta: dict[str, Any] | None = None

    model_config = ConfigDict(from_attributes=True)


# ── Normalisation helpers ────────────────────────────────────────────

_DL_STATUS_MAP: dict[DownloadJobStatus, str] = {
    DownloadJobStatus.QUEUED: "queued",
    DownloadJobStatus.RUNNING: "running",
    DownloadJobStatus.SUCCESS: "success",
    DownloadJobStatus.FAILED: "failed",
    DownloadJobStatus.CANCELED: "canceled",
}

_RAG_STATUS_MAP: dict[RagLiteJobStatus, str] = {
    RagLiteJobStatus.QUEUED: "queued",
    RagLiteJobStatus.RUNNING: "running",
    RagLiteJobStatus.COMPLETED: "success",
    RagLiteJobStatus.FAILED: "failed",
}


# ── Endpoints ────────────────────────────────────────────────────────


@router.get("/jobs", response_model=list[UnifiedJobDTO])
async def list_all_jobs(
    job_type: str | None = None,
    status_filter: str | None = None,
    user: User = Depends(require_permission("llm.jobs", "read")),
    dl_job_dao: DownloadJobDAO = Depends(),
    model_dao: ModelDAO = Depends(),
    rag_job_dao: RagLiteIngestJobDAO = Depends(),
) -> list[UnifiedJobDTO]:
    """List all background jobs across subsystems, newest first."""
    results: list[UnifiedJobDTO] = []

    # ── Model download jobs ──────────────────────────────────────
    if job_type is None or job_type == "model_download":
        dl_status = None
        if status_filter:
            dl_status = _reverse_dl_status(status_filter)
        dl_jobs = await dl_job_dao.list_all(status_filter=dl_status)
        models_list = await model_dao.list_all()
        model_map = {m.id: m for m in models_list}

        for j in dl_jobs:
            m = model_map.get(j.model_id)
            results.append(
                UnifiedJobDTO(
                    id=j.id,
                    job_type="model_download",
                    status=_DL_STATUS_MAP.get(j.status, str(j.status)),
                    label=m.display_name if m else str(j.model_id)[:8],
                    progress=j.progress,
                    error_message=j.error_message,
                    created_at=j.created_at,
                    updated_at=j.updated_at,
                    meta={
                        "model_id": str(j.model_id),
                        "hf_repo_id": m.hf_repo_id if m else None,
                    },
                ),
            )

    # ── RAG Lite ingest jobs ─────────────────────────────────────
    if job_type is None or job_type == "rag_ingest":
        rag_status = None
        if status_filter:
            rag_status = _reverse_rag_status(status_filter)
        rag_jobs = await rag_job_dao.list_recent(
            limit=200,
            status_filter=rag_status,
        )

        # Resolve document names in bulk
        from llm_port_backend.db.dao.rag_lite_dao import RagLiteDocumentDAO  # noqa: PLC0415

        doc_dao = RagLiteDocumentDAO(rag_job_dao.session)
        doc_ids = {j.document_id for j in rag_jobs}
        doc_map: dict[uuid.UUID, str] = {}
        for did in doc_ids:
            doc = await doc_dao.get(did)
            if doc:
                doc_map[did] = doc.filename

        for j in rag_jobs:
            results.append(
                UnifiedJobDTO(
                    id=j.id,
                    job_type="rag_ingest",
                    status=_RAG_STATUS_MAP.get(j.status, str(j.status)),
                    label=doc_map.get(j.document_id, str(j.document_id)[:8]),
                    progress=100 if j.status == RagLiteJobStatus.COMPLETED else -1,
                    error_message=j.error_message,
                    created_at=j.created_at,
                    updated_at=j.updated_at,
                    meta={
                        "document_id": str(j.document_id),
                        "stats": j.stats_json,
                    },
                ),
            )

    # Sort all results newest first
    results.sort(key=lambda r: r.created_at, reverse=True)
    return results


@router.post("/jobs/{job_id}/cancel", response_model=UnifiedJobDTO)
async def cancel_job(
    job_id: uuid.UUID,
    user: User = Depends(require_permission("llm.jobs", "cancel")),
    dl_job_dao: DownloadJobDAO = Depends(),
    model_dao: ModelDAO = Depends(),
    audit_dao: AuditDAO = Depends(),
) -> UnifiedJobDTO:
    """Cancel a queued or running download job."""
    job = await dl_job_dao.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status not in (DownloadJobStatus.QUEUED, DownloadJobStatus.RUNNING):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot cancel job in status '{job.status}'.",
        )
    job = await dl_job_dao.set_canceled(job_id)
    await audit_action(
        action="scheduler.job.cancel",
        target_type="download_job",
        target_id=str(job_id),
        result=AuditResult.ALLOW,
        actor_id=user.id,
        severity="normal",
        audit_dao=audit_dao,
    )
    m = await model_dao.get(job.model_id)
    return UnifiedJobDTO(
        id=job.id,
        job_type="model_download",
        status=_DL_STATUS_MAP.get(job.status, str(job.status)),
        label=m.display_name if m else str(job.model_id)[:8],
        progress=job.progress,
        error_message=job.error_message,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


@router.post("/jobs/{job_id}/retry", response_model=UnifiedJobDTO)
async def retry_job(
    job_id: uuid.UUID,
    user: User = Depends(require_permission("llm.jobs", "create")),
    dl_job_dao: DownloadJobDAO = Depends(),
    model_dao: ModelDAO = Depends(),
    audit_dao: AuditDAO = Depends(),
) -> UnifiedJobDTO:
    """Re-dispatch a stuck/failed download job."""
    job = await dl_job_dao.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status not in (
        DownloadJobStatus.QUEUED,
        DownloadJobStatus.FAILED,
        DownloadJobStatus.CANCELED,
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot retry job in status '{job.status}'.",
        )

    job.status = DownloadJobStatus.QUEUED
    job.progress = 0
    job.error_message = None

    model = await model_dao.get(job.model_id)
    if model is not None:
        model.status = ModelStatus.DOWNLOADING

    await dl_job_dao.session.flush()

    from llm_port_backend.services.llm.tasks import download_model_task  # noqa: PLC0415

    hf_repo_id = model.hf_repo_id if model else ""
    hf_revision = model.hf_revision if model else None
    target_dir = settings.model_store_root

    await download_model_task.kiq(
        model_id=str(job.model_id),
        job_id=str(job.id),
        hf_repo_id=hf_repo_id,
        hf_revision=hf_revision,
        target_dir=target_dir,
    )

    await audit_action(
        action="scheduler.job.retry",
        target_type="download_job",
        target_id=str(job_id),
        result=AuditResult.ALLOW,
        actor_id=user.id,
        severity="normal",
        audit_dao=audit_dao,
    )
    return UnifiedJobDTO(
        id=job.id,
        job_type="model_download",
        status="queued",
        label=model.display_name if model else str(job.model_id)[:8],
        progress=0,
        error_message=None,
        created_at=job.created_at,
        updated_at=job.updated_at,
    )


# ── Internal helpers ─────────────────────────────────────────────────

def _reverse_dl_status(normalised: str) -> DownloadJobStatus | None:
    """Map a normalised status string back to DownloadJobStatus."""
    for enum_val, norm in _DL_STATUS_MAP.items():
        if norm == normalised:
            return enum_val
    return None


def _reverse_rag_status(normalised: str) -> RagLiteJobStatus | None:
    """Map a normalised status string back to RagLiteJobStatus."""
    for enum_val, norm in _RAG_STATUS_MAP.items():
        if norm == normalised:
            return enum_val
    return None
