"""vLLM a machine already runs, found by its agent, and taken over (Phase 8).

The agent reports every vLLM container it did not start (``vllm_containers``
in its inventory; see ``llm_port_node_agent/vllm_discovery.py``). This module
reads those reports and does the one thing LLM.Port can safely do without
touching the container: route it at the gateway under a name, as it is, and
stop routing it again. The container keeps running exactly as before, and
stays whoever's it was.

The plan is ``llm-port-dev/llm_port_ray_migration/phase8_concrete_implementation_plan.md``.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.dao.node_control_dao import NodeControlDAO
from llm_port_backend.db.models.inference import (
    OPEN_ADOPTION_STATES,
    AdoptionState,
    InferenceAdoption,
)
from llm_port_backend.db.models.llm import LLMProvider, ProviderTarget, ProviderType
from llm_port_backend.db.models.node_control import InfraNode

log = logging.getLogger(__name__)

#: What the gateway calls a route that LLM.Port found rather than started.
SOURCE_KIND = "found_container"

#: How long asking a found container what it serves may take.
_PROBE_TIMEOUT_SEC = 3.0

#: Tasks the gateway can serve requests for. Scoring (rerank) models are
#: found and listed, but the gateway has no route for their requests yet.
_ROUTABLE_TASKS = {None, "chat", "embeddings"}

_ALIAS = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")


class FoundError(Exception):
    """A step that cannot be taken; the message says why, for the operator."""


# ---------------------------------------------------------------------------
# What the machines reported
# ---------------------------------------------------------------------------


def _host(node: InfraNode) -> str | None:
    text = str(node.host or "").strip()
    if text.startswith("host="):
        text = text[len("host="):]
    return text or None


def base_url(node: InfraNode, container: dict[str, Any]) -> str | None:
    """Where LLM.Port reaches a found container: the machine, the published port."""
    host, port = _host(node), container.get("host_port")
    if not host or not port:
        return None
    netloc = f"[{host}]" if ":" in host else host
    return f"http://{netloc}:{port}/v1"


async def _reports(session: AsyncSession) -> list[tuple[InfraNode, dict[str, Any], datetime | None]]:
    """Every machine with the vLLM its last inventory reported."""
    nodes = list((await session.execute(select(InfraNode).order_by(InfraNode.agent_id))).scalars())
    snapshots = await NodeControlDAO(session).latest_inventory_snapshots(node_ids=[n.id for n in nodes])
    out = []
    for node in nodes:
        snap = snapshots.get(node.id)
        inventory = (snap.inventory_json if snap else None) or {}
        out.append((node, inventory, snap.created_at if snap else None))
    return out


def _container(inventory: dict[str, Any], name: str) -> dict[str, Any] | None:
    return next(
        (c for c in inventory.get("vllm_containers") or [] if isinstance(c, dict) and c.get("name") == name),
        None,
    )


async def probe(url: str) -> dict[str, Any]:
    """Ask a found container what it serves: ``GET /v1/models``."""
    import httpx  # noqa: PLC0415

    from llm_port_backend.services.tls import default_httpx_verify  # noqa: PLC0415

    try:
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT_SEC, verify=default_httpx_verify()) as client:
            response = await client.get(f"{url.rstrip('/')}/models")
    except Exception as exc:  # noqa: BLE001 - not answering is the answer
        return {"ok": False, "models": [], "needs_key": False, "error": f"No answer from {url}: {type(exc).__name__}"}
    if response.status_code == 401:
        return {"ok": False, "models": [], "needs_key": True, "error": "It asks for an API key."}
    if response.status_code != 200:
        return {"ok": False, "models": [], "needs_key": False, "error": f"It answered {response.status_code}."}
    try:
        models = [str(m.get("id")) for m in (response.json().get("data") or []) if isinstance(m, dict)]
    except ValueError:
        return {"ok": False, "models": [], "needs_key": False, "error": "It did not answer as an OpenAI API."}
    return {"ok": True, "models": models, "needs_key": False, "error": None}


def _refusal(node: InfraNode, container: dict[str, Any], url: str | None) -> str | None:
    """Why this container cannot be routed as it is, or ``None``."""
    if container.get("state") != "running":
        return "It is not running."
    if not url:
        return "Its port is not published on the machine, so nothing outside the machine can reach it."
    if container.get("task") not in _ROUTABLE_TASKS:
        return "It serves scoring (rerank) requests, which the gateway does not route yet."
    if container.get("api_key_required"):
        return "It asks for an API key, which routing a found container does not handle yet."
    return None


async def _open_adoptions(session: AsyncSession) -> dict[tuple[str, str], InferenceAdoption]:
    rows = await session.execute(
        select(InferenceAdoption).where(InferenceAdoption.state.in_(OPEN_ADOPTION_STATES))
    )
    return {(str(a.node_id), a.container_name): a for a in rows.scalars()}


async def found(session: AsyncSession, *, check: bool = True) -> list[dict[str, Any]]:
    """Every vLLM container the machines reported, and what can be done with it.

    With *check*, each running one that LLM.Port can reach is asked what it
    serves, all at once and briefly, so a list of many is not a wait of many.
    """
    adoptions = await _open_adoptions(session)
    entries: list[dict[str, Any]] = []
    for node, inventory, reported_at in await _reports(session):
        for container in inventory.get("vllm_containers") or []:
            if not isinstance(container, dict):
                continue
            url = base_url(node, container)
            adoption = adoptions.get((str(node.id), container.get("name")))
            entries.append({
                "node": {
                    "id": str(node.id),
                    "name": node.agent_id,
                    "host": _host(node),
                    "status": str(node.status),
                },
                "reported_at": reported_at.isoformat() if reported_at else None,
                "container": container,
                "base_url": url,
                "adoption": describe(adoption) if adoption else None,
                "reason": None if adoption else _refusal(node, container, url),
                "check": None,
            })
    if check:
        targets = [e for e in entries if e["container"].get("state") == "running" and e["base_url"]]
        results = await asyncio.gather(*(probe(e["base_url"]) for e in targets))
        for entry, result in zip(targets, results, strict=True):
            entry["check"] = result
            if entry["adoption"] is None and entry["reason"] is None and not result["ok"]:
                entry["reason"] = result["error"]
    for entry in entries:
        entry["can_route"] = entry["adoption"] is None and entry["reason"] is None
    return entries


# ---------------------------------------------------------------------------
# Routing it as it is
# ---------------------------------------------------------------------------


async def route(
    session: AsyncSession,
    gateway_sync: Any,
    *,
    node_id: uuid.UUID,
    container_name: str,
    alias: str,
    user_id: uuid.UUID | None = None,
) -> InferenceAdoption:
    """Route a found container at the gateway under *alias*, touching nothing else."""
    if gateway_sync is None or not getattr(gateway_sync, "enabled", False):
        raise FoundError("There is no gateway to route it through.")
    alias = (alias or "").strip()
    if not _ALIAS.match(alias):
        raise FoundError("Give it a name of letters, digits and . _ - : / (up to 128), starting with a letter or digit.")
    node = await session.get(InfraNode, node_id)
    if node is None:
        raise FoundError("That machine is not enrolled.")
    snapshot = await NodeControlDAO(session).get_latest_inventory_snapshot(node_id=node_id)
    container = _container((snapshot.inventory_json if snapshot else None) or {}, container_name)
    if container is None:
        raise FoundError(f"{node.agent_id} has not reported a vLLM container called {container_name}.")
    if (str(node_id), container_name) in await _open_adoptions(session):
        raise FoundError("It is already routed.")
    url = base_url(node, container)
    if why := _refusal(node, container, url):
        raise FoundError(why)
    check = await probe(url)
    if check["needs_key"]:
        raise FoundError("It asks for an API key, which routing a found container does not handle yet.")
    if not check["ok"]:
        raise FoundError(check["error"])
    served = next((n for n in container.get("served_model_names") or [] if n in check["models"]), None)
    served = served or (check["models"][0] if check["models"] else None)
    if not served:
        raise FoundError("It answers, but serves no model.")
    if any(m.get("member_enabled") for m in await gateway_sync.members(alias)):
        raise FoundError(f"The name {alias} already routes to something else. Pick another.")

    adoption = InferenceAdoption(
        node_id=node_id,
        container_name=container_name,
        alias=alias,
        served_model_name=served,
        base_url=url,
        task=container.get("task"),
        state=AdoptionState.ROUTED.value,
        created_by=user_id,
        routed_at=datetime.now(tz=UTC),
        detail_json={"container": container, "container_state": "running"},
    )
    session.add(adoption)
    await session.flush()
    provider = LLMProvider(
        name=alias,
        type=ProviderType.VLLM,
        target=ProviderTarget.REMOTE_ENDPOINT,
        endpoint_url=url,
        litellm_model=served,
        capabilities={
            "found_on": node.agent_id,
            "container": container_name,
            "task": container.get("task"),
            "managed_by": container.get("managed_by"),
        },
        source_kind=SOURCE_KIND,
        source_id=str(adoption.id),
    )
    session.add(provider)
    await session.flush()
    adoption.provider_id = provider.id
    await gateway_sync.publish_runtime(
        runtime_id=adoption.id,
        alias=alias,
        base_url=url,
        backend_provider_type=ProviderType.VLLM.value,
        is_remote=False,
        health_status="healthy",
        litellm_model=served,
        node_id=node_id,
        # ``task`` is how the gateway's model list says what it is for, so a
        # chat screen does not offer an embedding model.
        node_metadata={
            "found_container": container_name,
            "managed_by": container.get("managed_by"),
            "task": container.get("task"),
        },
        source_kind=SOURCE_KIND,
        source_id=adoption.id,
        # Unknown stays unknown: a found container that says nothing is not
        # declared chat on LLM.Port's guess.
        task=container.get("task"),
    )
    log.info("Routing %s on %s as %s -> %s", container_name, node.agent_id, alias, url)
    return adoption


async def release(session: AsyncSession, gateway_sync: Any, adoption: InferenceAdoption) -> InferenceAdoption:
    """Stop routing a found container. The container is not touched."""
    if adoption.state != AdoptionState.ROUTED.value:
        raise FoundError("It is not being routed as it is.")
    if gateway_sync is not None and getattr(gateway_sync, "enabled", False):
        await gateway_sync.unpublish_runtime(runtime_id=adoption.id, alias=adoption.alias)
    if adoption.provider_id is not None:
        provider = await session.get(LLMProvider, adoption.provider_id)
        if provider is not None:
            await session.delete(provider)
        adoption.provider_id = None
    adoption.state = AdoptionState.RELEASED.value
    adoption.released_at = datetime.now(tz=UTC)
    return adoption


async def follow_containers(session: AsyncSession, gateway_sync: Any) -> int:
    """Route a found container only while it runs.

    Its state comes from its machine's latest inventory. A machine that has
    not reported since is left alone: silence says nothing about the
    container. Returns how many routes changed.
    """
    if gateway_sync is None or not getattr(gateway_sync, "enabled", False):
        return 0
    rows = await session.execute(
        select(InferenceAdoption).where(InferenceAdoption.state == AdoptionState.ROUTED.value)
    )
    adoptions = list(rows.scalars())
    if not adoptions:
        return 0
    node_ids = [a.node_id for a in adoptions if a.node_id is not None]
    snapshots = await NodeControlDAO(session).latest_inventory_snapshots(node_ids=node_ids)
    changed = 0
    for adoption in adoptions:
        snap = snapshots.get(adoption.node_id)
        if snap is None:
            continue
        container = _container(snap.inventory_json or {}, adoption.container_name)
        state = "gone" if container is None else str(container.get("state") or "")
        detail = dict(adoption.detail_json or {})
        if detail.get("container_state") == state:
            continue
        running = state == "running"
        await gateway_sync.set_instance_enabled(runtime_id=adoption.id, enabled=running)
        await gateway_sync.set_instance_health(
            runtime_id=adoption.id, health_status="healthy" if running else "unhealthy",
        )
        detail["container_state"] = state
        adoption.detail_json = detail
        changed += 1
        log.info("Found container %s is %s: routing %s", adoption.container_name, state, "on" if running else "off")
    return changed


def describe(adoption: InferenceAdoption) -> dict[str, Any]:
    """An adoption as the page shows it."""
    detail = adoption.detail_json or {}
    return {
        "id": str(adoption.id),
        "node_id": str(adoption.node_id) if adoption.node_id else None,
        "container": adoption.container_name,
        "alias": adoption.alias,
        "served_model_name": adoption.served_model_name,
        "base_url": adoption.base_url,
        "task": adoption.task,
        "state": adoption.state,
        "container_state": detail.get("container_state"),
        "provider_id": str(adoption.provider_id) if adoption.provider_id else None,
        "routed_at": adoption.routed_at.isoformat() if adoption.routed_at else None,
        "released_at": adoption.released_at.isoformat() if adoption.released_at else None,
    }
