"""LLM Download Job endpoints — list, get, cancel, retry."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from starlette import status

from llm_port_backend.db.dao.audit_dao import AuditDAO
from llm_port_backend.db.dao.llm_dao import DownloadJobDAO, ModelDAO
from llm_port_backend.db.models.containers import AuditResult
from llm_port_backend.db.models.llm import DownloadJobStatus, ModelStatus
from llm_port_backend.db.models.users import User
from llm_port_backend.settings import settings
from llm_port_backend.web.api.admin.dependencies import audit_action
from llm_port_backend.web.api.llm.schema import DownloadJobDTO
from llm_port_backend.web.api.rbac import require_permission

router = APIRouter()


@router.get("/", response_model=list[DownloadJobDTO])
async def list_jobs(
    status_filter: DownloadJobStatus | None = None,
    model_id: uuid.UUID | None = None,
    user: User = Depends(require_permission("llm.jobs", "read")),
    job_dao: DownloadJobDAO = Depends(),
) -> list[DownloadJobDTO]:
    """List download jobs with optional filters."""
    jobs = await job_dao.list_all(
        status_filter=status_filter,
        model_id=model_id,
    )
    return [DownloadJobDTO.model_validate(j) for j in jobs]


@router.get("/{job_id}", response_model=DownloadJobDTO)
async def get_job(
    job_id: uuid.UUID,
    user: User = Depends(require_permission("llm.jobs", "read")),
    job_dao: DownloadJobDAO = Depends(),
) -> DownloadJobDTO:
    """Get a single download job by ID."""
    job = await job_dao.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return DownloadJobDTO.model_validate(job)


@router.post("/{job_id}/cancel", status_code=status.HTTP_200_OK)
async def cancel_job(
    job_id: uuid.UUID,
    user: User = Depends(require_permission("llm.jobs", "cancel")),
    job_dao: DownloadJobDAO = Depends(),
    audit_dao: AuditDAO = Depends(),
) -> DownloadJobDTO:
    """Cancel a queued or running download job."""
    job = await job_dao.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status not in (DownloadJobStatus.QUEUED, DownloadJobStatus.RUNNING):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot cancel job in status '{job.status}'.",
        )
    job = await job_dao.set_canceled(job_id)
    await audit_action(
        action="llm.job.cancel",
        target_type="download_job",
        target_id=str(job_id),
        result=AuditResult.ALLOW,
        actor_id=user.id,
        severity="normal",
        audit_dao=audit_dao,
    )
    return DownloadJobDTO.model_validate(job)


@router.post("/{job_id}/retry", status_code=status.HTTP_200_OK)
async def retry_job(
    job_id: uuid.UUID,
    user: User = Depends(require_permission("llm.jobs", "create")),
    job_dao: DownloadJobDAO = Depends(),
    model_dao: ModelDAO = Depends(),
    audit_dao: AuditDAO = Depends(),
) -> DownloadJobDTO:
    """Re-dispatch a stuck queued or failed download job.

    Resets the job status to QUEUED, then dispatches a new Taskiq message
    so the worker picks it up. The model is also reset to DOWNLOADING.
    """
    job = await job_dao.get(job_id)
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

    # Reset job state
    job.status = DownloadJobStatus.QUEUED
    job.progress = 0
    job.error_message = None

    # Ensure model is in downloading state
    model = await model_dao.get(job.model_id)
    if model is not None:
        model.status = ModelStatus.DOWNLOADING

    # Flush changes before dispatching
    await job_dao.session.flush()

    # Re-dispatch the task
    from llm_port_backend.services.llm.tasks import download_model_task  # noqa: PLC0415

    hf_repo_id = model.hf_repo_id if model else ""
    hf_revision = model.hf_revision if model else None
    rev = hf_revision or "main"
    target_dir = f"{settings.model_store_root}/hf/{hf_repo_id}/{rev}"

    await download_model_task.kiq(
        model_id=str(job.model_id),
        job_id=str(job.id),
        hf_repo_id=hf_repo_id,
        hf_revision=hf_revision,
        target_dir=target_dir,
        hf_token=settings.hf_token,
    )

    await audit_action(
        action="llm.job.retry",
        target_type="download_job",
        target_id=str(job_id),
        result=AuditResult.ALLOW,
        actor_id=user.id,
        severity="normal",
        audit_dao=audit_dao,
    )
    return DownloadJobDTO.model_validate(job)
