"""LLM Model endpoints — download, register, list, get, delete, artifacts."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from starlette import status

from airgap_backend.db.dao.audit_dao import AuditDAO
from airgap_backend.db.dao.llm_dao import ArtifactDAO, DownloadJobDAO, ModelDAO
from airgap_backend.db.models.containers import AuditResult
from airgap_backend.db.models.users import User
from airgap_backend.services.llm.service import LLMService
from airgap_backend.web.api.admin.dependencies import audit_action
from airgap_backend.web.api.llm.dependencies import get_llm_service
from airgap_backend.web.api.llm.schema import (
    ArtifactDTO,
    DownloadJobDTO,
    DownloadResponseDTO,
    ModelDTO,
    ModelDownloadRequest,
    ModelRegisterRequest,
)
from airgap_backend.web.api.rbac import require_permission

router = APIRouter()


@router.get("/", response_model=list[ModelDTO])
async def list_models(
    user: User = Depends(require_permission("llm.models", "read")),
    model_dao: ModelDAO = Depends(),
) -> list[ModelDTO]:
    """List all models."""
    models = await model_dao.list_all()
    return [ModelDTO.model_validate(m) for m in models]


@router.get("/{model_id}", response_model=ModelDTO)
async def get_model(
    model_id: uuid.UUID,
    user: User = Depends(require_permission("llm.models", "read")),
    model_dao: ModelDAO = Depends(),
) -> ModelDTO:
    """Get a single model by ID."""
    model = await model_dao.get(model_id)
    if model is None:
        raise HTTPException(status_code=404, detail="Model not found")
    return ModelDTO.model_validate(model)


@router.post("/download", response_model=DownloadResponseDTO, status_code=status.HTTP_202_ACCEPTED)
async def download_model(
    body: ModelDownloadRequest,
    user: User = Depends(require_permission("llm.models", "download")),
    llm_service: LLMService = Depends(get_llm_service),
    model_dao: ModelDAO = Depends(),
    job_dao: DownloadJobDAO = Depends(),
    audit_dao: AuditDAO = Depends(),
) -> DownloadResponseDTO:
    """Start a background download of a model from Hugging Face."""
    model, job = await llm_service.start_download(
        model_dao,
        job_dao,
        hf_repo_id=body.hf_repo_id,
        hf_revision=body.hf_revision,
        display_name=body.display_name,
        tags=body.tags,
    )
    await audit_action(
        action="llm.model.download",
        target_type="llm_model",
        target_id=str(model.id),
        result=AuditResult.ALLOW,
        actor_id=user.id,
        severity="normal",
        audit_dao=audit_dao,
    )
    dispatched = job.error_message is None
    return DownloadResponseDTO(
        model=ModelDTO.model_validate(model),
        job=DownloadJobDTO.model_validate(job),
        dispatched=dispatched,
        dispatch_error=job.error_message if not dispatched else None,
    )


@router.post("/register", response_model=ModelDTO, status_code=status.HTTP_201_CREATED)
async def register_model(
    body: ModelRegisterRequest,
    user: User = Depends(require_permission("llm.models", "create")),
    llm_service: LLMService = Depends(get_llm_service),
    model_dao: ModelDAO = Depends(),
    artifact_dao: ArtifactDAO = Depends(),
    audit_dao: AuditDAO = Depends(),
) -> ModelDTO:
    """Register a model from a local path."""
    model = await llm_service.register_local_model(
        model_dao,
        artifact_dao,
        display_name=body.display_name,
        path=body.path,
        tags=body.tags,
    )
    await audit_action(
        action="llm.model.register",
        target_type="llm_model",
        target_id=str(model.id),
        result=AuditResult.ALLOW,
        actor_id=user.id,
        severity="normal",
        audit_dao=audit_dao,
    )
    return ModelDTO.model_validate(model)


@router.delete("/{model_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_model(
    model_id: uuid.UUID,
    user: User = Depends(require_permission("llm.models", "delete")),
    llm_service: LLMService = Depends(get_llm_service),
    model_dao: ModelDAO = Depends(),
    job_dao: DownloadJobDAO = Depends(),
    artifact_dao: ArtifactDAO = Depends(),
    audit_dao: AuditDAO = Depends(),
) -> None:
    """Delete a model, cancel active jobs, and remove all related data."""
    try:
        await llm_service.delete_model(
            model_dao,
            model_id,
            job_dao=job_dao,
            artifact_dao=artifact_dao,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    await audit_action(
        action="llm.model.delete",
        target_type="llm_model",
        target_id=str(model_id),
        result=AuditResult.ALLOW,
        actor_id=user.id,
        severity="normal",
        audit_dao=audit_dao,
    )


@router.get("/{model_id}/artifacts", response_model=list[ArtifactDTO])
async def list_artifacts(
    model_id: uuid.UUID,
    user: User = Depends(require_permission("llm.models", "read")),
    artifact_dao: ArtifactDAO = Depends(),
) -> list[ArtifactDTO]:
    """List all artifacts for a model."""
    artifacts = await artifact_dao.list_by_model(model_id)
    return [ArtifactDTO.model_validate(a) for a in artifacts]
