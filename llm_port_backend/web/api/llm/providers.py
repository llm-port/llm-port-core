"""LLM Provider CRUD endpoints."""

from __future__ import annotations

import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException
from starlette import status

from llm_port_backend.db.dao.audit_dao import AuditDAO
from llm_port_backend.db.dao.llm_dao import ProviderDAO, RuntimeDAO
from llm_port_backend.db.models.containers import AuditResult
from llm_port_backend.db.models.users import User
from llm_port_backend.services.llm.service import LLMService
from llm_port_backend.web.api.admin.dependencies import audit_action
from llm_port_backend.web.api.llm.dependencies import get_llm_service
from llm_port_backend.web.api.llm.schema import (
    ProviderCreateRequest,
    ProviderDTO,
    ProviderUpdateRequest,
    TestEndpointRequest,
    TestEndpointResponse,
)
from llm_port_backend.web.api.rbac import require_permission

router = APIRouter()


@router.post("/test-endpoint", response_model=TestEndpointResponse)
async def test_endpoint(
    body: TestEndpointRequest,
    user: User = Depends(require_permission("llm.providers", "read")),
) -> TestEndpointResponse:
    """Probe a remote endpoint for OpenAI API compatibility.

    Sends GET ``{endpoint_url}/models`` and checks whether the response
    follows the ``{"data": [{"id": ...}, ...]}`` schema used by the
    OpenAI-compatible API.
    """
    url = body.endpoint_url.rstrip("/")

    headers: dict[str, str] = {"Accept": "application/json"}
    if body.api_key:
        headers["Authorization"] = f"Bearer {body.api_key}"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(f"{url}/models", headers=headers)
    except httpx.ConnectError:
        return TestEndpointResponse(
            compatible=False,
            error="Connection refused — check the URL and make sure the service is reachable.",
        )
    except httpx.TimeoutException:
        return TestEndpointResponse(
            compatible=False,
            error="Request timed out after 10 s.",
        )
    except Exception as exc:
        return TestEndpointResponse(
            compatible=False,
            error=f"Connection failed: {exc}",
        )

    if resp.status_code == 401:
        return TestEndpointResponse(
            compatible=False,
            error="Authentication failed (HTTP 401). Check your API key.",
        )

    if resp.status_code == 403:
        return TestEndpointResponse(
            compatible=False,
            error="Access denied (HTTP 403). The API key may lack the required permissions.",
        )

    if resp.status_code >= 400:
        return TestEndpointResponse(
            compatible=False,
            error=f"Endpoint returned HTTP {resp.status_code}.",
        )

    # Validate OpenAI-compatible /models response shape
    try:
        payload = resp.json()
    except Exception:
        return TestEndpointResponse(
            compatible=False,
            error="Response is not valid JSON — the endpoint is not OpenAI API compatible.",
        )

    if not isinstance(payload, dict) or "data" not in payload:
        return TestEndpointResponse(
            compatible=False,
            error=(
                'Response JSON does not contain a "data" field. '
                "This endpoint does not appear to be OpenAI API compatible."
            ),
        )

    data = payload["data"]
    if not isinstance(data, list):
        return TestEndpointResponse(
            compatible=False,
            error='The "data" field is not a list — unexpected response format.',
        )

    model_ids: list[str] = []
    for item in data:
        if isinstance(item, dict) and "id" in item:
            model_ids.append(str(item["id"]))

    if not model_ids:
        return TestEndpointResponse(
            compatible=False,
            error="The /models endpoint returned an empty list — no models available.",
        )

    return TestEndpointResponse(compatible=True, models=model_ids)


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
        endpoint_url=body.endpoint_url,
        api_key=body.api_key,
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
        endpoint_url=body.endpoint_url if body.endpoint_url is not None else ...,
        api_key_encrypted=body.api_key if body.api_key is not None else ...,
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
    runtime_dao: RuntimeDAO = Depends(),
    llm_service: LLMService = Depends(get_llm_service),
    audit_dao: AuditDAO = Depends(),
) -> None:
    """Delete a provider, cascade-deleting any associated runtimes."""
    try:
        await llm_service.delete_provider(provider_dao, runtime_dao, provider_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await audit_action(
        action="llm.provider.delete",
        target_type="llm_provider",
        target_id=str(provider_id),
        result=AuditResult.ALLOW,
        actor_id=user.id,
        severity="normal",
        audit_dao=audit_dao,
    )
