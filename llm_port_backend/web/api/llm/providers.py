"""LLM Provider CRUD endpoints."""

from __future__ import annotations

import logging
import uuid

import httpx
from fastapi import APIRouter, Depends, HTTPException
from starlette import status

from llm_port_backend.db.dao.audit_dao import AuditDAO
from llm_port_backend.db.dao.llm_dao import ArtifactDAO, ModelDAO, ProviderDAO, RuntimeDAO
from llm_port_backend.db.models.containers import AuditResult
from llm_port_backend.db.models.llm import LLMProvider, ModelSource, ModelStatus, ProviderTarget
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

log = logging.getLogger(__name__)

# ── Known provider health-check URLs (used when no endpoint_url given) ───────
_PROVIDER_HEALTH_URLS: dict[str, str] = {
    "openai": "https://api.openai.com/v1/models",
    "anthropic": "https://api.anthropic.com/v1/models",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/models",
    "mistral": "https://api.mistral.ai/v1/models",
    "groq": "https://api.groq.com/openai/v1/models",
    "deepseek": "https://api.deepseek.com/v1/models",
    "cohere": "https://api.cohere.com/v2/models",
    "openrouter": "https://openrouter.ai/api/v1/models",
}

_PROVIDER_AUTH_HEADER: dict[str, tuple[str, str]] = {
    # provider -> (header_name_template, value_template)
    # Most providers use Bearer, Anthropic uses x-api-key
    "anthropic": ("x-api-key", "{key}"),
    "gemini": ("x-goog-api-key", "{key}"),
}


async def _test_litellm_provider(
    *,
    litellm_provider: str,
    api_key: str | None,
    litellm_model: str | None,
) -> TestEndpointResponse:
    """Test connectivity to a known LiteLLM provider via its health endpoint."""
    health_url = _PROVIDER_HEALTH_URLS.get(litellm_provider)
    if not health_url:
        # Unknown provider — we can't auto-test without an endpoint URL
        return TestEndpointResponse(
            compatible=False,
            error=(
                f"No known health endpoint for provider '{litellm_provider}'. "
                "Please provide an endpoint URL to test connectivity."
            ),
        )

    headers: dict[str, str] = {"Accept": "application/json"}
    if api_key:
        if litellm_provider in _PROVIDER_AUTH_HEADER:
            hdr_name, hdr_tpl = _PROVIDER_AUTH_HEADER[litellm_provider]
            headers[hdr_name] = hdr_tpl.format(key=api_key)
        else:
            headers["Authorization"] = f"Bearer {api_key}"

    # For Gemini, the API key goes as a query param
    params: dict[str, str] = {}
    if litellm_provider == "gemini" and api_key:
        params["key"] = api_key

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(health_url, headers=headers, params=params)
    except httpx.ConnectError:
        return TestEndpointResponse(
            compatible=False,
            error=f"Connection refused to {litellm_provider} API.",
        )
    except httpx.TimeoutException:
        return TestEndpointResponse(
            compatible=False,
            error=f"Request to {litellm_provider} API timed out after 10 s.",
        )
    except Exception as exc:
        return TestEndpointResponse(
            compatible=False,
            error=f"Connection to {litellm_provider} failed: {exc}",
        )

    if resp.status_code == 401:
        return TestEndpointResponse(
            compatible=False,
            error="Authentication failed (HTTP 401). Check your API key.",
        )
    if resp.status_code == 403:
        return TestEndpointResponse(
            compatible=False,
            error="Access denied (HTTP 403). The API key may lack required permissions.",
        )
    if resp.status_code >= 400:
        return TestEndpointResponse(
            compatible=False,
            error=f"{litellm_provider} API returned HTTP {resp.status_code}.",
        )

    # Try to extract model IDs from the response
    model_ids: list[str] = []
    try:
        payload = resp.json()
        # OpenAI-style: {"data": [{"id": ...}]}
        data = payload.get("data") if isinstance(payload, dict) else None
        # Gemini-style: {"models": [{"name": ...}]}
        if data is None and isinstance(payload, dict):
            data = payload.get("models")
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict):
                    model_ids.append(str(item.get("id") or item.get("name", "")))
    except Exception:
        pass

    return TestEndpointResponse(
        compatible=True,
        models=model_ids[:20],  # cap at 20 to keep response small
    )

router = APIRouter()


def _extract_remote_model(capabilities: dict | None) -> str | None:
    """Return the optional remote model name stored in provider capabilities."""
    if not isinstance(capabilities, dict):
        return None
    value = capabilities.get("remote_model")
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return None


def _provider_to_dto(provider: LLMProvider) -> ProviderDTO:
    """Serialize a provider including derived remote_model metadata."""
    dto = ProviderDTO.model_validate(provider)
    return dto.model_copy(update={"remote_model": _extract_remote_model(provider.capabilities)})


async def _probe_first_model(
    endpoint_url: str,
    api_key: str | None,
) -> str | None:
    """Hit ``GET {endpoint_url}/models`` and return the first model id.

    Returns ``None`` on any failure — this is best-effort.
    """
    url = endpoint_url.rstrip("/")
    headers: dict[str, str] = {"Accept": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{url}/models", headers=headers)
        if resp.status_code >= 400:
            return None
        payload = resp.json()
        data = payload.get("data", [])
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and "id" in item:
                    return str(item["id"])
    except Exception:
        log.debug("Could not probe remote models at %s", url, exc_info=True)
    return None


@router.post("/test-endpoint", response_model=TestEndpointResponse)
async def test_endpoint(
    body: TestEndpointRequest,
    user: User = Depends(require_permission("llm.providers", "read")),
) -> TestEndpointResponse:
    """Probe a remote endpoint for OpenAI API compatibility.

    When ``litellm_provider`` is set without an ``endpoint_url``, a
    lightweight LiteLLM completion call is used to verify connectivity.
    Otherwise sends GET ``{endpoint_url}/models``.
    """
    # ── LiteLLM provider test (no endpoint URL needed) ───────────
    if body.litellm_provider and not body.endpoint_url:
        return await _test_litellm_provider(
            litellm_provider=body.litellm_provider,
            api_key=body.api_key,
            litellm_model=body.litellm_model,
        )

    if not body.endpoint_url:
        return TestEndpointResponse(
            compatible=False,
            error="Either an endpoint URL or a LiteLLM provider must be specified.",
        )

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
    return [_provider_to_dto(p) for p in providers]


@router.post("/", response_model=ProviderDTO, status_code=status.HTTP_201_CREATED)
async def create_provider(
    body: ProviderCreateRequest,
    user: User = Depends(require_permission("llm.providers", "create")),
    llm_service: LLMService = Depends(get_llm_service),
    provider_dao: ProviderDAO = Depends(),
    runtime_dao: RuntimeDAO = Depends(),
    model_dao: ModelDAO = Depends(),
    artifact_dao: ArtifactDAO = Depends(),
    audit_dao: AuditDAO = Depends(),
) -> ProviderDTO:
    """Register a new LLM provider.

    For remote providers a placeholder model and runtime are
    auto-created so the API gateway can route traffic immediately.
    """
    provider = await llm_service.create_provider(
        provider_dao,
        name=body.name,
        type_=body.type,
        target=body.target,
        endpoint_url=body.endpoint_url,
        api_key=body.api_key,
        remote_model=body.remote_model,
        litellm_provider=body.litellm_provider,
        litellm_model=body.litellm_model,
        extra_params=body.extra_params,
    )

    # ── Auto-provision remote providers ──────────────────────────
    if body.target == ProviderTarget.REMOTE_ENDPOINT and (
        body.endpoint_url or body.litellm_provider
    ):
        # Determine alias name: prefer explicit remote_model, otherwise
        # probe the remote endpoint for the first available model id.
        alias_name = (body.remote_model or "").strip()
        if not alias_name and body.litellm_model:
            alias_name = body.litellm_model.strip()
        if not alias_name and body.endpoint_url:
            alias_name = await _probe_first_model(
                body.endpoint_url, body.api_key,
            )
        if not alias_name:
            alias_name = body.name.strip()

        placeholder_model = await model_dao.create(
            display_name=alias_name,
            source=ModelSource.LOCAL_PATH,      # lightweight placeholder
            status=ModelStatus.AVAILABLE,
            tags=["remote", "auto-provisioned"],
        )
        try:
            await llm_service.create_runtime(
                runtime_dao,
                provider_dao,
                model_dao,
                artifact_dao,
                name=alias_name,
                provider_id=provider.id,
                model_id=placeholder_model.id,
            )
        except Exception:
            log.warning(
                "Auto-provisioning runtime for remote provider %s failed",
                provider.id,
                exc_info=True,
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
    return _provider_to_dto(provider)


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
    return _provider_to_dto(provider)


@router.patch("/{provider_id}", response_model=ProviderDTO)
async def update_provider(
    provider_id: uuid.UUID,
    body: ProviderUpdateRequest,
    user: User = Depends(require_permission("llm.providers", "update")),
    provider_dao: ProviderDAO = Depends(),
    audit_dao: AuditDAO = Depends(),
) -> ProviderDTO:
    """Patch writable fields on a provider."""
    existing = await provider_dao.get(provider_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Provider not found")

    capabilities_changed = False
    next_capabilities: dict | None = None
    if body.capabilities is not None:
        next_capabilities = dict(body.capabilities)
        capabilities_changed = True

    if "remote_model" in body.model_fields_set:
        if next_capabilities is None:
            base = existing.capabilities if isinstance(existing.capabilities, dict) else {}
            next_capabilities = dict(base)
        remote_model = body.remote_model.strip() if isinstance(body.remote_model, str) else None
        if remote_model:
            next_capabilities["remote_model"] = remote_model
        else:
            next_capabilities.pop("remote_model", None)
        capabilities_changed = True

    provider = await provider_dao.update(
        provider_id,
        name=body.name,
        capabilities=next_capabilities if capabilities_changed else None,
        endpoint_url=body.endpoint_url if body.endpoint_url is not None else ...,
        api_key_encrypted=body.api_key if body.api_key is not None else ...,
        litellm_provider=body.litellm_provider if "litellm_provider" in body.model_fields_set else ...,
        litellm_model=body.litellm_model if "litellm_model" in body.model_fields_set else ...,
        extra_params=body.extra_params if "extra_params" in body.model_fields_set else ...,
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
    return _provider_to_dto(provider)


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
