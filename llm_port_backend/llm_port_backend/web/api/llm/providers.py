"""LLM Provider CRUD endpoints."""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from llm_port_backend.db.dao.audit_dao import AuditDAO
from llm_port_backend.db.dao.llm_dao import ArtifactDAO, ModelDAO, ProviderDAO, RuntimeDAO
from llm_port_backend.db.models.containers import AuditResult
from llm_port_backend.db.models.inference import (
    InferenceDeployment,
    InferenceEnvironment,
)
from llm_port_backend.db.models.llm import (
    LLMModel,
    LLMProvider,
    LLMRuntime,
    ModelSource,
    ModelStatus,
    ProviderTarget,
    ProviderType,
)
from llm_port_backend.db.models.users import User
from llm_port_backend.services.llm import residency as residency_mod
from llm_port_backend.services.llm.monitoring import get_monitoring_provisioner
from llm_port_backend.services.llm.service import LLMService
from llm_port_backend.services.tls import default_httpx_verify
from llm_port_backend.web.api.admin.dependencies import audit_action
from llm_port_backend.web.api.llm.dependencies import get_llm_service
from llm_port_backend.web.api.llm.schema import (
    ManagedByDTO,
    ProviderCreateRequest,
    ProviderDTO,
    ProviderUpdateRequest,
    ResidencyDTO,
    ResidencyOverrideRequest,
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
        async with httpx.AsyncClient(verify=default_httpx_verify(), timeout=30.0) as client:
            resp = await client.get(health_url, headers=headers, params=params)
    except httpx.ConnectError:
        return TestEndpointResponse(
            compatible=False,
            error=f"Connection refused to {litellm_provider} API.",
        )
    except httpx.TimeoutException:
        return TestEndpointResponse(
            compatible=False,
            error=f"Request to {litellm_provider} API timed out after 30 s.",
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
        models=model_ids[:500],  # cap to keep response manageable
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


def _provider_to_dto(
    provider: LLMProvider,
    owners: "dict[str, ManagedByDTO] | None" = None,
) -> ProviderDTO:
    """Serialize a provider including derived remote_model metadata."""
    dto = ProviderDTO.model_validate(provider)
    update: dict[str, Any] = {
        "remote_model": _extract_remote_model(provider.capabilities)
    }
    if provider.source_kind and owners:
        owner = owners.get(str(provider.source_id))
        if owner is not None:
            update["managed_by"] = owner
    return dto.model_copy(update=update)


async def _residencies(
    session: AsyncSession, providers: list[LLMProvider]
) -> dict[str, ResidencyDTO]:
    """Where each provider's prompts go, in one pass: machines read once, DNS resolved together."""
    from llm_port_backend.db.models.node_control import InfraNode  # noqa: PLC0415
    from llm_port_backend.settings import settings  # noqa: PLC0415

    nodes = list((await session.execute(select(InfraNode))).scalars().all())
    ctx = residency_mod.Context(
        machine_addresses=residency_mod.machine_addresses(nodes),
        internal_networks=residency_mod.parse_networks(settings.residency_internal_networks),
    )
    found = await residency_mod.classify_all(providers, ctx)
    return {pid: ResidencyDTO(**r.to_dict()) for pid, r in found.items()}


async def _resolve_owners(
    session: "AsyncSession", providers: "list[LLMProvider]"
) -> dict[str, ManagedByDTO]:
    """Look up the deployments that own derived providers, in one query.

    A cluster-backed provider has no container and no runtime row, so without
    its owner there is nothing honest to put in a status column -- the page
    rendered a blank cell. Resolved here rather than by the screen because two
    lookups are two answers that can disagree, and this is exactly where they
    would be seen together.
    """
    def ids_of(kind: str) -> list[uuid.UUID]:
        out = []
        for p in providers:
            if p.source_kind == kind and p.source_id:
                try:
                    out.append(uuid.UUID(str(p.source_id)))
                except (ValueError, TypeError):
                    continue
        return out

    owners: dict[str, ManagedByDTO] = {}
    deployment_ids = ids_of("inference_deployment")
    if deployment_ids:
        rows = await session.execute(
            select(InferenceDeployment, LLMModel)
            .outerjoin(LLMModel, LLMModel.id == InferenceDeployment.model_id)
            .where(InferenceDeployment.id.in_(deployment_ids))
        )
        for dep, model in rows.all():
            owners[str(dep.id)] = ManagedByDTO(
                kind="inference_deployment",
                id=str(dep.id),
                name=dep.name,
                state=str(dep.phase or "") or None,
                model_name=(model.display_name if model is not None else None),
            )

    # A vLLM container LLM.Port found on a machine and routes as it is: the
    # container is the owner, and whether it runs is its state.
    found_ids = ids_of("found_container")
    if found_ids:
        from llm_port_backend.db.models.inference import InferenceAdoption  # noqa: PLC0415
        from llm_port_backend.db.models.node_control import InfraNode  # noqa: PLC0415

        rows = await session.execute(
            select(InferenceAdoption, InfraNode)
            .outerjoin(InfraNode, InfraNode.id == InferenceAdoption.node_id)
            .where(InferenceAdoption.id.in_(found_ids))
        )
        for adoption, node in rows.all():
            machine = node.agent_id if node is not None else "a machine"
            owners[str(adoption.id)] = ManagedByDTO(
                kind="found_container",
                id=str(adoption.id),
                name=f"{adoption.container_name} on {machine}",
                state=(adoption.detail_json or {}).get("container_state"),
                model_name=adoption.served_model_name,
                node_id=str(adoption.node_id) if adoption.node_id else None,
            )
    return owners


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
        async with httpx.AsyncClient(verify=default_httpx_verify(), timeout=5.0) as client:
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
        async with httpx.AsyncClient(verify=default_httpx_verify(), timeout=30.0) as client:
            resp = await client.get(f"{url}/models", headers=headers)
    except httpx.ConnectError:
        return TestEndpointResponse(
            compatible=False,
            error="Connection refused — check the URL and make sure the service is reachable.",
        )
    except httpx.TimeoutException:
        return TestEndpointResponse(
            compatible=False,
            error="Request timed out after 30 s.",
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
    owners = await _resolve_owners(provider_dao.session, providers)
    residencies = await _residencies(provider_dao.session, providers)
    return [
        _provider_to_dto(p, owners).model_copy(update={"residency": residencies.get(str(p.id))})
        for p in providers
    ]


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

    # Commit eagerly so the provider is visible to the follow-up
    # runtime creation request the frontend fires immediately after.
    await provider_dao.session.commit()

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
        # Prefer litellm_provider over user-given provider name so that
        # the routing alias (= model_alias in the request log) stays a
        # recognisable model identifier rather than a display label.
        if not alias_name and body.litellm_provider:
            alias_name = body.litellm_provider.strip()
        if not alias_name:
            alias_name = body.name.strip()

        # Reuse an existing model with the same display_name when one
        # already exists (avoids duplicates when the same model is
        # served from multiple providers / remote nodes).
        placeholder_model = await model_dao.find_by_display_name(
            alias_name, status_filter=ModelStatus.AVAILABLE,
        )
        if placeholder_model is None:
            placeholder_model = await model_dao.create(
                display_name=alias_name,
                source=ModelSource.REMOTE,
                status=ModelStatus.AVAILABLE,
                tags=["remote", "auto-provisioned"],
            )
        elif placeholder_model.source != ModelSource.REMOTE:
            # Model was previously registered locally / from HF; update
            # source so the UI shows "remote" instead of "local_path".
            placeholder_model.source = ModelSource.REMOTE
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
    residencies = await _residencies(provider_dao.session, [provider])
    return _provider_to_dto(provider).model_copy(update={"residency": residencies.get(str(provider.id))})


@router.put("/{provider_id}/residency", response_model=ProviderDTO)
async def set_provider_residency(
    provider_id: uuid.UUID,
    body: ResidencyOverrideRequest,
    user: User = Depends(require_permission("llm.providers", "update")),
    provider_dao: ProviderDAO = Depends(),
    audit_dao: AuditDAO = Depends(),
) -> ProviderDTO:
    """Say where a provider's prompts go, or clear that to detect it again.

    Allowed on providers a deployment owns too: it is a statement about where
    the data goes, which no reconcile rewrites.
    """
    provider = await provider_dao.get(provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="Provider not found")
    provider.residency_override = body.override
    await provider_dao.session.flush()
    await provider_dao.session.refresh(provider)  # updated_at is the database's
    await audit_action(
        action="llm.provider.residency",
        target_type="llm_provider",
        target_id=str(provider_id),
        result=AuditResult.ALLOW,
        actor_id=user.id,
        severity="normal",
        audit_dao=audit_dao,
        metadata_json=json.dumps({"override": body.override}),
    )
    residencies = await _residencies(provider_dao.session, [provider])
    return _provider_to_dto(provider).model_copy(update={"residency": residencies.get(str(provider.id))})


def _refuse_if_derived(provider: object, action: str) -> None:
    """A provider owned by a deployment is managed from that deployment.

    Editing or deleting it here would not stick: the next reconcile recreates
    or overwrites it, and in between the screen shows something that is not
    true. Refusing with the owner named is more useful than a control that
    appears to work.
    """
    source_kind = getattr(provider, "source_kind", None)
    if not source_kind:
        return
    source_id = getattr(provider, "source_id", None)
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=(
            f"This provider is served by deployment {source_id} and cannot be "
            f"{action} here. Change it on the deployment instead."
        ),
    )


@router.get("/{provider_id}/monitoring-stats")
async def provider_monitoring_stats(
    provider_id: uuid.UUID,
    user: User = Depends(require_permission("llm.providers", "read")),
    provider_dao: ProviderDAO = Depends(),
    runtime_dao: RuntimeDAO = Depends(),
) -> dict:
    """Live stat-card values and a dashboard link, for either kind of provider.

    One endpoint rather than two, because the screen showing these cards does
    not care how the model is being served and should not have to branch. It
    also means a person whose role reaches the providers page but not the
    deployments page can still see whether the hardware is working, instead of
    being handed a link to somewhere they cannot go.

    The two kinds resolve differently underneath:

    * A **local runtime** is scraped at its own ``/metrics`` and labelled with
      the runtime's name.
    * A **cluster-backed** provider has no runtime and no container. Its
      replicas are labelled with the *environment's* name -- that is the label
      ``sync_ray_targets`` writes and the one the cluster dashboard selects on
      -- so the figures are asked for under that name, and the dashboard is
      the cluster's.

    Always 200. ``{"enabled": false}`` says "there is nothing to show here",
    which the card row renders as a muted state rather than an error.
    """
    off = {"enabled": False, "stale": True, "stats": {}, "dashboard_url": None}

    provider = await provider_dao.get(provider_id)
    if provider is None:
        raise HTTPException(status_code=404, detail="Provider not found")
    monitoring = get_monitoring_provisioner()
    if monitoring is None:
        return off

    session = provider_dao.session

    if provider.source_kind == "inference_deployment" and provider.source_id:
        try:
            deployment_id = uuid.UUID(str(provider.source_id))
        except (ValueError, TypeError):
            return off
        row = await session.execute(
            select(InferenceEnvironment)
            .join(
                InferenceDeployment,
                InferenceDeployment.environment_id == InferenceEnvironment.id,
            )
            .where(InferenceDeployment.id == deployment_id)
        )
        environment = row.scalars().first()
        if environment is None:
            return off
        return await monitoring.stats(environment.id, environment.name)

    if provider.type != ProviderType.VLLM:
        return off
    row = await session.execute(
        select(LLMRuntime).where(LLMRuntime.provider_id == provider.id).limit(1)
    )
    runtime = row.scalars().first()
    if runtime is None:
        return off
    return await monitoring.stats(runtime.id, runtime.name)


@router.patch("/{provider_id}", response_model=ProviderDTO)
async def update_provider(
    provider_id: uuid.UUID,
    body: ProviderUpdateRequest,
    user: User = Depends(require_permission("llm.providers", "update")),
    provider_dao: ProviderDAO = Depends(),
    runtime_dao: RuntimeDAO = Depends(),
    model_dao: ModelDAO = Depends(),
    audit_dao: AuditDAO = Depends(),
) -> ProviderDTO:
    """Patch writable fields on a provider."""
    existing = await provider_dao.get(provider_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="Provider not found")
    _refuse_if_derived(existing, "edited")

    capabilities_changed = False
    next_capabilities: dict | None = None
    if body.capabilities is not None:
        next_capabilities = dict(body.capabilities)
        capabilities_changed = True

    # Track the old remote_model so we can cascade the rename.
    old_remote_model: str | None = None
    new_remote_model: str | None = None

    if "remote_model" in body.model_fields_set:
        if next_capabilities is None:
            base = existing.capabilities if isinstance(existing.capabilities, dict) else {}
            next_capabilities = dict(base)
        old_caps = existing.capabilities if isinstance(existing.capabilities, dict) else {}
        old_remote_model = (old_caps.get("remote_model") or "").strip() or None
        remote_model = body.remote_model.strip() if isinstance(body.remote_model, str) else None
        if remote_model:
            next_capabilities["remote_model"] = remote_model
            new_remote_model = remote_model
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

    # ── Cascade remote_model rename to auto-provisioned model + runtime ──
    if (
        new_remote_model
        and old_remote_model
        and new_remote_model != old_remote_model
        and existing.target == ProviderTarget.REMOTE_ENDPOINT
    ):
        rts = await runtime_dao.list_by_provider(provider_id)
        for rt in rts:
            model = await model_dao.get(rt.model_id)
            if (
                model
                and model.tags
                and "auto-provisioned" in model.tags
                and model.display_name == old_remote_model
            ):
                model.display_name = new_remote_model
            # Also rename the runtime if it still matches the old model name
            if rt.name == old_remote_model:
                rt.name = new_remote_model

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
    model_dao: ModelDAO = Depends(),
    llm_service: LLMService = Depends(get_llm_service),
    audit_dao: AuditDAO = Depends(),
) -> None:
    """Delete a provider, cascade-deleting any associated runtimes."""
    existing = await provider_dao.get(provider_id)
    if existing is not None:
        _refuse_if_derived(existing, "deleted")
    try:
        await llm_service.delete_provider(
            provider_dao, runtime_dao, provider_id, model_dao=model_dao,
        )
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
