"""LLM Provider CRUD endpoints."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from starlette import status

from llm_port_backend.db.dao.audit_dao import AuditDAO
from llm_port_backend.db.dao.llm_dao import ProviderDAO
from llm_port_backend.db.models.containers import AuditResult
from llm_port_backend.db.models.users import User
from llm_port_backend.services.llm.service import LLMService
from llm_port_backend.web.api.admin.dependencies import audit_action
from llm_port_backend.web.api.llm.dependencies import get_llm_service
from llm_port_backend.web.api.llm.schema import (
    ProviderCreateRequest,
    ProviderDTO,
    ProviderUpdateRequest,
)
from llm_port_backend.web.api.rbac import require_permission

router = APIRouter()


@router.get("/", response_model=list[ProviderDTO])
async def list_providers(
    user: User = Depends(require_permission("llm.providers", "read")),
    provider_dao: ProviderDAO = Depends(),
) -> list[ProviderDTO]:
    """List all registered LLM providers."""
    providers = await provider_dao.list_all()
    return [ProviderDTO.model_validate(p) for p in providers]


@router.post("/", response_model=ProviderDTO, status_code=status.HTTP_201_CREATED)
async def create_provider(
    body: ProviderCreateRequest,
    user: User = Depends(require_permission("llm.providers", "create")),
    llm_service: LLMService = Depends(get_llm_service),
    provider_dao: ProviderDAO = Depends(),
    audit_dao: AuditDAO = Depends(),
) -> ProviderDTO:
    """Register a new LLM provider."""
    provider = await llm_service.create_provider(
        provider_dao,
        name=body.name,
        type_=body.type,
        target=body.target,
    )
    await audit_action(
        action="llm.provider.create",
        target_type="llm_provider",
        target_id=str(provider.id),
        result=AuditResult.ALLOW,
        actor_id=user.id,
        severity="normal",
        audit_dao=audit_dao,
    )
    return ProviderDTO.model_validate(provider)


@router.get("/{provider_id}", response_model=ProviderDTO)
async def get_provider(
    provider_id: uuid.UUID,
    user: User = Depends(require_permission("llm.providers", "read")),
    provider_dao: ProviderDAO = Depends(),
) -> ProviderDTO:
    """Get a single provider by ID."""
    provider = await provider_dao.get(provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="Provider not found")
    return ProviderDTO.model_validate(provider)


@router.patch("/{provider_id}", response_model=ProviderDTO)
async def update_provider(
    provider_id: uuid.UUID,
    body: ProviderUpdateRequest,
    user: User = Depends(require_permission("llm.providers", "update")),
    provider_dao: ProviderDAO = Depends(),
    audit_dao: AuditDAO = Depends(),
) -> ProviderDTO:
    """Patch writable fields on a provider."""
    provider = await provider_dao.update(
        provider_id,
        name=body.name,
        capabilities=body.capabilities,
    )
    if provider is None:
        raise HTTPException(status_code=404, detail="Provider not found")
    await audit_action(
        action="llm.provider.update",
        target_type="llm_provider",
        target_id=str(provider_id),
        result=AuditResult.ALLOW,
        actor_id=user.id,
        severity="normal",
        audit_dao=audit_dao,
    )
    return ProviderDTO.model_validate(provider)


@router.delete("/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_provider(
    provider_id: uuid.UUID,
    user: User = Depends(require_permission("llm.providers", "delete")),
    provider_dao: ProviderDAO = Depends(),
    audit_dao: AuditDAO = Depends(),
) -> None:
    """Delete a provider (blocked if runtimes exist)."""
    if await provider_dao.has_runtimes(provider_id):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cannot delete provider with existing runtimes.",
        )
    deleted = await provider_dao.delete(provider_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Provider not found")
    await audit_action(
        action="llm.provider.delete",
        target_type="llm_provider",
        target_id=str(provider_id),
        result=AuditResult.ALLOW,
        actor_id=user.id,
        severity="normal",
        audit_dao=audit_dao,
    )
