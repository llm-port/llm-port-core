"""The model marketplace: browse Hugging Face, see what fits your clusters, host a model.

Every list answers with ``hub``: ``online`` or ``offline``. A server without
internet access is a normal installation: it still gets the curated list and
the models it already keeps, with a note, rather than an error page.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from llm_port_backend.db.dependencies import get_db_session
from llm_port_backend.db.models.users import User
from llm_port_backend.services.marketplace import curated as curated_mod
from llm_port_backend.services.marketplace import fit as fit_mod
from llm_port_backend.services.marketplace import settings as settings_mod
from llm_port_backend.services.marketplace.hardware import cluster_hardware
from llm_port_backend.services.marketplace.host import HostError, HostRequest, host
from llm_port_backend.services.marketplace.hub import HubClient, HubNotFound, HubUnavailable
from llm_port_backend.web.api.llm.dependencies import get_llm_service
from llm_port_backend.web.api.rbac import require_permission

log = logging.getLogger(__name__)
router = APIRouter()

_READ = require_permission("llm.models", "read")


async def _hub(session: AsyncSession) -> HubClient:
    """A Hub client carrying this server's Hugging Face token, when one is set."""
    from llm_port_backend.db.dao.system_settings_dao import SystemSettingsDAO  # noqa: PLC0415
    from llm_port_backend.services.system_settings.crypto import SettingsCrypto  # noqa: PLC0415
    from llm_port_backend.settings import settings  # noqa: PLC0415

    token = None
    try:
        secret = await SystemSettingsDAO(session).get_secret("llm_backend.hf_token")
        if secret and secret.ciphertext:
            token = SettingsCrypto(settings.settings_master_key).decrypt(secret.ciphertext)
    except Exception:  # noqa: BLE001 - anonymous access still browses public models
        log.info("marketplace: no usable Hugging Face token", exc_info=True)
    return HubClient(token=token or settings.hf_token or None)


async def _local(session: AsyncSession) -> dict[str, dict[str, Any]]:
    """The models this server keeps, by repository: status and how many deployments use each."""
    from sqlalchemy import func, select  # noqa: PLC0415

    from llm_port_backend.db.models.inference import InferenceDeployment  # noqa: PLC0415
    from llm_port_backend.db.models.llm import LLMModel, ModelStatus  # noqa: PLC0415

    counts = dict((await session.execute(
        select(InferenceDeployment.model_id, func.count()).group_by(InferenceDeployment.model_id),
    )).all())
    kept: dict[str, dict[str, Any]] = {}
    for model in (await session.execute(select(LLMModel).where(LLMModel.status != ModelStatus.DELETING))).scalars():
        if not model.hf_repo_id:
            continue
        entry = kept.get(model.hf_repo_id)
        rank = {"available": 0, "downloading": 1}.get(str(model.status.value if hasattr(model.status, "value") else model.status), 2)
        if entry is None or rank < entry["_rank"]:
            kept[model.hf_repo_id] = {
                "model_id": str(model.id),
                "status": str(model.status.value if hasattr(model.status, "value") else model.status),
                "deployments": int(counts.get(model.id, 0)),
                "_rank": rank,
            }
    for entry in kept.values():
        entry.pop("_rank", None)
    return kept


def _cluster(hardware: list[fit_mod.ClusterHardware], cluster_id: str | None) -> fit_mod.ClusterHardware | None:
    if cluster_id:
        return next((c for c in hardware if c.environment_id == cluster_id), None)
    usable = [c for c in hardware if c.gpus]
    return usable[0] if usable else (hardware[0] if hardware else None)


def _decorate(card: dict[str, Any], cluster: fit_mod.ClusterHardware | None,
              local: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """A card with its fit on *cluster* and whether this server keeps it."""
    item = dict(card)
    if cluster is not None and item.get("runnable", True):
        item["fit"] = fit_mod.plan(fit_mod.quick_needs(item.get("weights_bytes")), cluster).to_dict()
    else:
        item["fit"] = None
    item["local"] = local.get(item["repo_id"])
    return item


def _curated_card(entry: curated_mod.Curated) -> dict[str, Any]:
    """What can be shown for a curated model without the Hub."""
    return {
        "repo_id": entry.repo_id,
        "name": entry.repo_id.split("/")[-1],
        "author": entry.repo_id.split("/")[0],
        "params_b": entry.params_b,
        "weights_bytes": None,
        "capabilities": list(entry.capabilities),
        "task": "embedding" if entry.group == "embedding" else ("vision" if entry.group == "vision" else "chat"),
        "runnable": True,
        "format": "safetensors",
    }


@router.get("/clusters")
async def clusters(
    _user: User = Depends(_READ),
    session: AsyncSession = Depends(get_db_session),
) -> list[dict[str, Any]]:
    """Each cluster with its accelerators: what the fit check measures against."""
    return [c.to_dict() for c in await cluster_hardware(session)]


@router.get("/recommended")
async def recommended(
    cluster_id: str | None = None,
    _user: User = Depends(_READ),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """The curated list, grouped, with each model's fit on the chosen cluster."""
    hardware = await cluster_hardware(session)
    cluster = _cluster(hardware, cluster_id)
    local = await _local(session)
    hub_state = "online"
    try:
        cards = await (await _hub(session)).cards_for([c.repo_id for c in curated_mod.CURATED])
    except HubUnavailable:
        hub_state, cards = "offline", {}
    items = []
    for entry in curated_mod.CURATED:
        card = cards.get(entry.repo_id) or _curated_card(entry)
        if entry.capabilities:
            card = {**card, "capabilities": sorted(set(card.get("capabilities") or []) | set(entry.capabilities))}
        item = _decorate(card, cluster, local)
        item["curated"] = {"group": entry.group, "blurb": entry.blurb, "verified": entry.verified}
        items.append(item)
    return {
        "hub": hub_state,
        "cluster_id": cluster.environment_id if cluster else None,
        "groups": list(curated_mod.GROUPS),
        "items": items,
    }


@router.get("/search")
async def search(
    q: str = "",
    sort: Literal["trending", "downloads", "likes", "recent"] = "trending",
    task: Literal["chat", "embedding", "vision"] = "chat",
    limit: int = Query(40, ge=1, le=100),
    cluster_id: str | None = None,
    _user: User = Depends(_READ),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Models on the Hub, with their fit on the chosen cluster."""
    hardware = await cluster_hardware(session)
    cluster = _cluster(hardware, cluster_id)
    local = await _local(session)
    try:
        cards = await (await _hub(session)).search(q, sort=sort, task=task, limit=limit)
    except HubUnavailable:
        return {"hub": "offline", "cluster_id": cluster.environment_id if cluster else None, "items": []}
    for card in cards:
        entry = curated_mod.curated_for(card["repo_id"])
        if entry is not None:
            card["curated"] = {"group": entry.group, "blurb": entry.blurb, "verified": entry.verified}
    return {
        "hub": "online",
        "cluster_id": cluster.environment_id if cluster else None,
        "items": [_decorate(card, cluster, local) for card in cards],
    }


@router.get("/local")
async def kept_models(
    cluster_id: str | None = None,
    _user: User = Depends(_READ),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """The models this server already keeps: host one without downloading anything."""
    hardware = await cluster_hardware(session)
    cluster = _cluster(hardware, cluster_id)
    local = await _local(session)
    hub_state = "online"
    try:
        cards = await (await _hub(session)).cards_for(sorted(local))
    except HubUnavailable:
        hub_state, cards = "offline", {}
    items = []
    for repo_id in sorted(local):
        card = cards.get(repo_id) or {
            "repo_id": repo_id, "name": repo_id.split("/")[-1], "author": repo_id.split("/")[0],
            "weights_bytes": None, "capabilities": [], "task": "chat", "runnable": True, "format": "safetensors",
        }
        items.append(_decorate(card, cluster, local))
    return {"hub": hub_state, "cluster_id": cluster.environment_id if cluster else None, "items": items}


@router.get("/models/{repo_id:path}")
async def model_detail(
    repo_id: str,
    cluster_id: str | None = None,
    context: int | None = Query(None, ge=256, le=4_194_304),
    _user: User = Depends(_READ),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """One model: what it is, how it fits each cluster, and the settings suggested for it."""
    hardware = await cluster_hardware(session)
    local = await _local(session)
    entry = curated_mod.curated_for(repo_id)
    hub_state = "online"
    try:
        detail = await (await _hub(session)).detail(repo_id)
    except HubNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"{repo_id} is not on Hugging Face, or needs access.") from exc
    except HubUnavailable:
        if entry is None and repo_id not in local:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="Hugging Face is not reachable from this server.") from None
        hub_state = "offline"
        detail = _curated_card(entry) if entry else {
            "repo_id": repo_id, "name": repo_id.split("/")[-1], "capabilities": [], "task": "chat", "runnable": True,
        }
    if entry is not None:
        detail["curated"] = {"group": entry.group, "blurb": entry.blurb, "verified": entry.verified}
        if entry.capabilities:
            detail["capabilities"] = sorted(set(detail.get("capabilities") or []) | set(entry.capabilities))
    arch = detail.get("architecture_facts") or {}
    needs = fit_mod.ModelNeeds(
        weights_bytes=detail.get("weights_bytes"),
        kv_bytes_per_token=detail.get("kv_bytes_per_token"),
        max_context=detail.get("max_context"),
        attention_heads=arch.get("num_attention_heads"),
        kv_heads=arch.get("num_kv_heads"),
    )
    fits = {c.environment_id: fit_mod.plan(needs, c, context=context).to_dict() for c in hardware}
    chosen = _cluster(hardware, cluster_id)
    chosen_fit = fit_mod.plan(needs, chosen, context=context) if chosen is not None else None
    suggestion = settings_mod.suggest(detail, chosen_fit if chosen_fit and chosen_fit.status == "fits" else None)
    if entry is not None and entry.settings:
        suggestion["config"].update(entry.settings)
        suggestion["reasons"].update({k: "curated" for k in entry.settings})
    return {
        "hub": hub_state,
        "model": detail,
        "clusters": [c.to_dict() for c in hardware],
        "cluster_id": chosen.environment_id if chosen else None,
        "fits": fits,
        "suggested": suggestion,
        "local": local.get(repo_id),
    }


class HostBody(BaseModel):
    repo_id: str = Field(min_length=3, max_length=256)
    revision: str | None = Field(default=None, max_length=128)
    environment_id: uuid.UUID
    name: str = Field(min_length=1, max_length=128, pattern=r"^[a-z0-9][a-z0-9-]*$")
    alias: str | None = Field(default=None, max_length=128)
    copies: int = Field(default=1, ge=1, le=64)
    gpus_per_copy: float = Field(default=1, gt=0, le=64)
    engine_config: dict[str, Any] = Field(default_factory=dict)


@router.post("/host", status_code=status.HTTP_201_CREATED)
async def host_model(
    body: HostBody,
    _can_download: User = Depends(require_permission("llm.models", "download")),
    _can_deploy: User = Depends(require_permission("inference.deployments", "create")),
    session: AsyncSession = Depends(get_db_session),
    llm_service: Any = Depends(get_llm_service),
) -> dict[str, Any]:
    """Keep the model on this server (downloading it if needed) and deploy it to a cluster."""
    from llm_port_backend.services.inference.service import InferenceError  # noqa: PLC0415

    try:
        return await host(session, llm_service, HostRequest(
            repo_id=body.repo_id.strip(),
            environment_id=body.environment_id,
            name=body.name,
            alias=(body.alias or "").strip() or None,
            copies=body.copies,
            gpus_per_copy=body.gpus_per_copy,
            engine_config=body.engine_config,
            revision=(body.revision or "").strip() or None,
        ))
    except HostError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except InferenceError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
