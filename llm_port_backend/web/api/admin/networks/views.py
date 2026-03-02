"""Admin network management endpoints."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from starlette import status

from llm_port_backend.db.dao.audit_dao import AuditDAO
from llm_port_backend.db.models.containers import AuditResult
from llm_port_backend.db.models.users import User
from llm_port_backend.services.docker.client import DockerService
from llm_port_backend.web.api.admin.dependencies import (
    audit_action,
    get_docker,
    require_superuser,
)
from llm_port_backend.web.api.admin.networks.schema import (
    CreateNetworkRequest,
    NetworkContainerDTO,
    NetworkDetailDTO,
    NetworkSummaryDTO,
)

router = APIRouter()

# ──────────────────────────────────────────────────────────────────────────────
# System network detection
# ──────────────────────────────────────────────────────────────────────────────

# Built-in Docker networks that should always be read-only
_BUILTIN_NETWORKS = {"bridge", "host", "none"}

# Container name substrings that mark a network as "system" when any of
# its connected containers match.
_SYSTEM_CONTAINER_WORDS = frozenset(
    [
        "postgres",
        "clickhouse",
        "redis",  # SYSTEM_CORE
        "grafana",
        "loki",
        "alloy",
        "minio",
        "langfuse",
        "rabbitmq",
        "rmq",  # SYSTEM_AUX
    ]
)


def _is_system_network(raw: dict[str, Any]) -> bool:
    """Decide whether a network is a system/built-in network.

    A network is "system" if:
    - Its name is one of the Docker built-in networks (bridge/host/none), OR
    - Any container attached to it matches a known system-component name pattern.
    """
    name = raw.get("Name", "")
    if name in _BUILTIN_NETWORKS:
        return True

    # Check connected containers' names
    containers: dict[str, Any] = raw.get("Containers") or {}
    for _cid, info in containers.items():
        cname = (info.get("Name") or "").lower()
        if any(word in cname for word in _SYSTEM_CONTAINER_WORDS):
            return True

    return False


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


def _summary_from_raw(raw: dict[str, Any], *, full: bool = False) -> dict[str, Any]:
    """Extract common fields from a Docker network inspect dict."""
    ipam_configs = (raw.get("IPAM") or {}).get("Config") or []
    subnet = ipam_configs[0].get("Subnet", "") if ipam_configs else ""
    gateway = ipam_configs[0].get("Gateway", "") if ipam_configs else ""

    base: dict[str, Any] = {
        "id": raw.get("Id", ""),
        "name": raw.get("Name", ""),
        "driver": raw.get("Driver", ""),
        "scope": raw.get("Scope", ""),
        "internal": raw.get("Internal", False),
        "created": raw.get("Created", ""),
        "is_system": _is_system_network(raw),
        "container_count": len(raw.get("Containers") or {}),
    }

    if full:
        containers_raw: dict[str, Any] = raw.get("Containers") or {}
        attached = [
            NetworkContainerDTO(
                id=cid,
                name=info.get("Name", ""),
                ipv4_address=info.get("IPv4Address", ""),
                mac_address=info.get("MacAddress", ""),
            )
            for cid, info in containers_raw.items()
        ]
        base.update(
            {
                "subnet": subnet,
                "gateway": gateway,
                "containers": attached,
                "labels": raw.get("Labels") or {},
                "options": raw.get("Options") or {},
            }
        )

    return base


# ──────────────────────────────────────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────────────────────────────────────


@router.get("/", response_model=list[NetworkSummaryDTO], name="list_networks")
async def list_networks(
    docker: DockerService = Depends(get_docker),
    _user: User = Depends(require_superuser),
) -> list[NetworkSummaryDTO]:
    """List all Docker networks with summary info."""
    raw_nets = await docker.list_networks()

    # list_networks returns shallow data; we need Containers dict for system
    # detection + container_count, so do a full inspect per network.
    result: list[NetworkSummaryDTO] = []
    for net in raw_nets:
        nid = net.get("Id", "")
        try:
            full = await docker.inspect_network(nid)
        except Exception:
            full = net
        result.append(NetworkSummaryDTO(**_summary_from_raw(full)))
    return result


@router.get("/{network_id}", response_model=NetworkDetailDTO, name="get_network")
async def get_network(
    network_id: str,
    docker: DockerService = Depends(get_docker),
    _user: User = Depends(require_superuser),
) -> NetworkDetailDTO:
    """Return full details for a single network."""
    try:
        raw = await docker.inspect_network(network_id)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Network {network_id!r} not found.",
        ) from exc
    return NetworkDetailDTO(**_summary_from_raw(raw, full=True))


@router.post("/", response_model=NetworkDetailDTO, status_code=status.HTTP_201_CREATED, name="create_network")
async def create_network(
    body: CreateNetworkRequest,
    user: User = Depends(require_superuser),
    docker: DockerService = Depends(get_docker),
    audit_dao: AuditDAO = Depends(),
) -> NetworkDetailDTO:
    """Create a new Docker network. Cannot create networks with built-in names."""
    if body.name.lower() in _BUILTIN_NETWORKS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot create a network with reserved name '{body.name}'.",
        )

    raw = await docker.create_network(
        name=body.name,
        driver=body.driver,
        internal=body.internal,
        labels=body.labels or {},
        subnet=body.subnet,
        gateway=body.gateway,
    )
    await audit_action(
        action="network.create",
        target_type="network",
        target_id=raw.get("Id", body.name),
        result=AuditResult.ALLOW,
        actor_id=user.id,
        severity="normal",
        audit_dao=audit_dao,
        metadata_json=json.dumps(
            {
                "name": body.name,
                "driver": body.driver,
                "internal": body.internal,
            }
        ),
    )
    return NetworkDetailDTO(**_summary_from_raw(raw, full=True))


@router.delete(
    "/{network_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    name="delete_network",
)
async def delete_network(
    network_id: str,
    user: User = Depends(require_superuser),
    docker: DockerService = Depends(get_docker),
    audit_dao: AuditDAO = Depends(),
) -> None:
    """Delete a network. System networks cannot be deleted."""
    # Pre-check: inspect the network to decide if it's system
    try:
        raw = await docker.inspect_network(network_id)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Network {network_id!r} not found.",
        ) from exc

    if _is_system_network(raw):
        await audit_action(
            action="network.delete",
            target_type="network",
            target_id=network_id,
            result=AuditResult.DENY,
            actor_id=user.id,
            severity="high",
            audit_dao=audit_dao,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="System networks cannot be deleted.",
        )

    await docker.delete_network(network_id)
    await audit_action(
        action="network.delete",
        target_type="network",
        target_id=network_id,
        result=AuditResult.ALLOW,
        actor_id=user.id,
        severity="normal",
        audit_dao=audit_dao,
        metadata_json=json.dumps({"name": raw.get("Name", "")}),
    )
