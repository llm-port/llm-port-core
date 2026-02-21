"""LLM Runtime endpoints — create, list, get, start/stop/restart, delete, health, logs."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from starlette import status

from airgap_backend.db.dao.audit_dao import AuditDAO
from airgap_backend.db.dao.llm_dao import ArtifactDAO, ModelDAO, ProviderDAO, RuntimeDAO
from airgap_backend.db.models.containers import AuditResult
from airgap_backend.db.models.users import User
from airgap_backend.services.docker.client import DockerService
from airgap_backend.services.llm.service import LLMService
from airgap_backend.web.api.admin.dependencies import audit_action, get_docker
from airgap_backend.web.api.llm.dependencies import get_llm_service
from airgap_backend.web.api.llm.schema import (
    RuntimeCreateRequest,
    RuntimeDTO,
    RuntimeHealthDTO,
)
from airgap_backend.web.api.rbac import require_permission

router = APIRouter()


@router.get("/", response_model=list[RuntimeDTO])
async def list_runtimes(
    user: User = Depends(require_permission("llm.runtimes", "read")),
    runtime_dao: RuntimeDAO = Depends(),
) -> list[RuntimeDTO]:
    """List all runtimes."""
    runtimes = await runtime_dao.list_all()
    return [RuntimeDTO.model_validate(r) for r in runtimes]


@router.post("/", response_model=RuntimeDTO, status_code=status.HTTP_201_CREATED)
async def create_runtime(
    body: RuntimeCreateRequest,
    user: User = Depends(require_permission("llm.runtimes", "create")),
    llm_service: LLMService = Depends(get_llm_service),
    runtime_dao: RuntimeDAO = Depends(),
    provider_dao: ProviderDAO = Depends(),
    model_dao: ModelDAO = Depends(),
    artifact_dao: ArtifactDAO = Depends(),
    audit_dao: AuditDAO = Depends(),
) -> RuntimeDTO:
    """Create a new runtime, validate compatibility, and start the container."""
    try:
        runtime = await llm_service.create_runtime(
            runtime_dao,
            provider_dao,
            model_dao,
            artifact_dao,
            name=body.name,
            provider_id=body.provider_id,
            model_id=body.model_id,
            generic_config=body.generic_config,
            provider_config=body.provider_config,
            openai_compat=body.openai_compat,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    await audit_action(
        action="llm.runtime.create",
        target_type="llm_runtime",
        target_id=str(runtime.id),
        result=AuditResult.ALLOW,
        actor_id=user.id,
        severity="normal",
        audit_dao=audit_dao,
    )
    return RuntimeDTO.model_validate(runtime)


@router.get("/{runtime_id}", response_model=RuntimeDTO)
async def get_runtime(
    runtime_id: uuid.UUID,
    user: User = Depends(require_permission("llm.runtimes", "read")),
    runtime_dao: RuntimeDAO = Depends(),
) -> RuntimeDTO:
    """Get a single runtime by ID."""
    runtime = await runtime_dao.get(runtime_id)
    if runtime is None:
        raise HTTPException(status_code=404, detail="Runtime not found")
    return RuntimeDTO.model_validate(runtime)


@router.post("/{runtime_id}/start", response_model=RuntimeDTO)
async def start_runtime(
    runtime_id: uuid.UUID,
    user: User = Depends(require_permission("llm.runtimes", "start")),
    llm_service: LLMService = Depends(get_llm_service),
    runtime_dao: RuntimeDAO = Depends(),
    audit_dao: AuditDAO = Depends(),
) -> RuntimeDTO:
    """Start a stopped runtime."""
    try:
        runtime = await llm_service.start_runtime(runtime_dao, runtime_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await audit_action(
        action="llm.runtime.start",
        target_type="llm_runtime",
        target_id=str(runtime_id),
        result=AuditResult.ALLOW,
        actor_id=user.id,
        severity="normal",
        audit_dao=audit_dao,
    )
    return RuntimeDTO.model_validate(runtime)


@router.post("/{runtime_id}/stop", response_model=RuntimeDTO)
async def stop_runtime(
    runtime_id: uuid.UUID,
    user: User = Depends(require_permission("llm.runtimes", "stop")),
    llm_service: LLMService = Depends(get_llm_service),
    runtime_dao: RuntimeDAO = Depends(),
    audit_dao: AuditDAO = Depends(),
) -> RuntimeDTO:
    """Stop a running runtime."""
    try:
        runtime = await llm_service.stop_runtime(runtime_dao, runtime_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await audit_action(
        action="llm.runtime.stop",
        target_type="llm_runtime",
        target_id=str(runtime_id),
        result=AuditResult.ALLOW,
        actor_id=user.id,
        severity="normal",
        audit_dao=audit_dao,
    )
    return RuntimeDTO.model_validate(runtime)


@router.post("/{runtime_id}/restart", response_model=RuntimeDTO)
async def restart_runtime(
    runtime_id: uuid.UUID,
    user: User = Depends(require_permission("llm.runtimes", "restart")),
    llm_service: LLMService = Depends(get_llm_service),
    runtime_dao: RuntimeDAO = Depends(),
    audit_dao: AuditDAO = Depends(),
) -> RuntimeDTO:
    """Restart a runtime."""
    try:
        runtime = await llm_service.restart_runtime(runtime_dao, runtime_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    await audit_action(
        action="llm.runtime.restart",
        target_type="llm_runtime",
        target_id=str(runtime_id),
        result=AuditResult.ALLOW,
        actor_id=user.id,
        severity="normal",
        audit_dao=audit_dao,
    )
    return RuntimeDTO.model_validate(runtime)


@router.delete("/{runtime_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_runtime(
    runtime_id: uuid.UUID,
    user: User = Depends(require_permission("llm.runtimes", "delete")),
    llm_service: LLMService = Depends(get_llm_service),
    runtime_dao: RuntimeDAO = Depends(),
    audit_dao: AuditDAO = Depends(),
) -> None:
    """Stop the container and delete the runtime record."""
    try:
        await llm_service.delete_runtime(runtime_dao, runtime_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await audit_action(
        action="llm.runtime.delete",
        target_type="llm_runtime",
        target_id=str(runtime_id),
        result=AuditResult.ALLOW,
        actor_id=user.id,
        severity="normal",
        audit_dao=audit_dao,
    )


@router.get("/{runtime_id}/health", response_model=RuntimeHealthDTO)
async def runtime_health(
    runtime_id: uuid.UUID,
    user: User = Depends(require_permission("llm.runtimes", "read")),
    llm_service: LLMService = Depends(get_llm_service),
    runtime_dao: RuntimeDAO = Depends(),
    provider_dao: ProviderDAO = Depends(),
) -> RuntimeHealthDTO:
    """Probe a runtime's health."""
    try:
        result = await llm_service.get_runtime_health(runtime_dao, provider_dao, runtime_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return RuntimeHealthDTO(**result)


@router.get("/{runtime_id}/logs")
async def runtime_logs(
    runtime_id: uuid.UUID,
    tail: int = 200,
    follow: bool = False,
    user: User = Depends(require_permission("llm.runtimes", "read")),
    runtime_dao: RuntimeDAO = Depends(),
    docker: DockerService = Depends(get_docker),
) -> StreamingResponse:
    """Stream logs from the runtime's container."""
    runtime = await runtime_dao.get(runtime_id)
    if runtime is None:
        raise HTTPException(status_code=404, detail="Runtime not found")
    if not runtime.container_ref:
        raise HTTPException(status_code=400, detail="Runtime has no container")

    async def _stream():  # type: ignore[return]
        async for line in docker.logs(runtime.container_ref, tail=tail, follow=follow):
            yield line

    return StreamingResponse(_stream(), media_type="text/plain")
