"""Admin container lifecycle and management endpoints."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from starlette import status

from llm_port_backend.db.dao.audit_dao import AuditDAO
from llm_port_backend.db.dao.container_registry_dao import ContainerRegistryDAO
from llm_port_backend.db.models.containers import (
    AuditResult,
    ContainerClass,
    ContainerPolicy,
)
from llm_port_backend.db.models.users import User
from llm_port_backend.services.docker.client import DockerService
from llm_port_backend.services.policy.enforcement import Action, PolicyEnforcer
from llm_port_backend.web.api.admin.containers.schema import (
    ContainerDetailDTO,
    ContainerSummaryDTO,
    CreateContainerRequest,
    ExecTokenDTO,
    ExecTokenRequest,
    RegisterContainerRequest,
)
from llm_port_backend.web.api.admin.dependencies import (
    audit_action,
    get_docker,
    get_policy_enforcer,
    get_registry_entry,
    get_root_mode_active,
    require_superuser,
)
from llm_port_backend.web.api.rbac import require_permission

router = APIRouter()

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

_LIFECYCLE_ACTION_MAP: dict[str, Action] = {
    "start": Action.START,
    "stop": Action.STOP,
    "restart": Action.RESTART,
    "pause": Action.PAUSE,
    "unpause": Action.UNPAUSE,
}

_ROOT_MODE_SEVERITY = "high"
_NORMAL_SEVERITY = "normal"


def _severity(root_mode: bool) -> str:
    return _ROOT_MODE_SEVERITY if root_mode else _NORMAL_SEVERITY


# ──────────────────────────────────────────────────────────────────────────────
# Auto-classification rules by container name / compose project
# ──────────────────────────────────────────────────────────────────────────────

# Containers whose name contains any of these substrings are classified accordingly.
# Rules are evaluated top-to-bottom; first match wins.
_AUTO_CLASS_RULES: list[tuple[list[str], ContainerClass, str]] = [
    # SYSTEM_CORE — shared platform infrastructure (non-editable)
    (
        [
            "postgres", "clickhouse", "redis", "nginx",  # data & proxy
            "rabbitmq", "rmq",                            # message broker
            "minio",                                      # object storage
            "loki", "grafana", "alloy",                   # observability
            "langfuse", "llm-port-worker", "llm-port-web",  # LLM tracing
        ],
        ContainerClass.SYSTEM_CORE,
        "platform",
    ),
    # SYSTEM_AUX — application services (checked before MCP so that
    # "llm-port-mcp-migrator" doesn't accidentally match the generic
    # "mcp-" pattern below).
    (
        [
            "llm-port-api",
            "llm_port_api",
            "llm-port-backend",
            "llm-port-frontend",
            "llm-port-mcp",
            "llm-port-skills",
            "llm-port-pii",
            "llm-port-rag",
            "llm-port-auth",
            "llm-port-mailer",
            "llm-port-docling",
            "llm-port-ee",
            "migrator",
        ],
        ContainerClass.SYSTEM_AUX,
        "platform",
    ),
    # MCP — standalone Model Context Protocol servers
    (
        ["mcp-brave", "mcp-searxng", "mcp-"],
        ContainerClass.MCP,
        "platform",
    ),
]


def _classify_by_name(name: str) -> tuple[ContainerClass, str]:
    """Return (container_class, owner_scope) based on well-known name patterns."""
    lower = name.lower()
    for substrings, cls, scope in _AUTO_CLASS_RULES:
        if any(s in lower for s in substrings):
            return cls, scope
    return ContainerClass.UNTRUSTED, "unknown"


def _format_endpoint(ports: list[dict[str, Any]]) -> str:
    """Build a human-readable endpoint string from Docker port entries."""
    seen: list[str] = []
    for p in ports:
        pub = p.get("PublicPort")
        priv = p.get("PrivatePort")
        ip = p.get("IP", "0.0.0.0")
        if pub:
            if ip in ("::", "0.0.0.0"):
                ip = "0.0.0.0"
            entry = f"{ip}:{pub}"
            if entry not in seen:
                seen.append(entry)
        elif priv:
            entry = f":{priv}"
            if entry not in seen:
                seen.append(entry)
    return ", ".join(seen)


def _container_summary_from_docker(
    raw: dict[str, Any],
    container_class: ContainerClass = ContainerClass.UNTRUSTED,
    policy: ContainerPolicy = ContainerPolicy.FREE,
    owner_scope: str = "unknown",
) -> ContainerSummaryDTO:
    names: list[str] = raw.get("Names", [raw.get("Name", "")])
    name = names[0].lstrip("/") if names else raw.get("Id", "")[:12]
    raw_ports = raw.get("Ports")
    ports: list[dict[str, Any]] = list(raw_ports) if isinstance(raw_ports, list) else []
    network_settings = raw.get("NetworkSettings") or {}
    raw_networks = network_settings.get("Networks")
    if not isinstance(raw_networks, dict):
        raw_networks = {}
    network_names = list(raw_networks.keys())
    return ContainerSummaryDTO(
        id=raw.get("Id", ""),
        name=name,
        image=raw.get("Image", ""),
        status=raw.get("Status", ""),
        state=raw.get("State", ""),
        created=str(raw.get("Created", "")),
        ports=ports,
        networks=network_names,
        endpoint=_format_endpoint(ports),
        container_class=container_class,
        policy=policy,
        owner_scope=owner_scope,
    )


# ──────────────────────────────────────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────────────────────────────────────


@router.post("/", response_model=ContainerSummaryDTO, status_code=201, name="create_container")
async def create_container(
    body: CreateContainerRequest,
    user: User = Depends(require_permission("containers", "create")),
    docker: DockerService = Depends(get_docker),
    registry_dao: ContainerRegistryDAO = Depends(),
    audit_dao: AuditDAO = Depends(),
) -> ContainerSummaryDTO:
    """Create a new container and register it in the classification registry."""
    # Build Docker port binding config: {"80/tcp": [{"HostIp": "", "HostPort": "8080"}]}
    port_bindings: dict[str, list[dict[str, str]]] | None = None
    if body.ports:
        port_bindings = {}
        for pb in body.ports:
            port_bindings.setdefault(pb.container_port, []).append({"HostIp": "", "HostPort": pb.host_port})

    raw = await docker.create_container(
        image=body.image,
        name=body.name or None,
        cmd=body.cmd or None,
        env=body.env or None,
        ports=port_bindings,
        volumes=body.volumes or None,
        network=body.network or None,
        auto_start=body.auto_start,
    )

    container_id: str = raw.get("Id", "")
    # Determine the name Docker assigned
    raw_name = (body.name or container_id[:12]).lstrip("/")

    entry = await registry_dao.upsert(
        container_id=container_id,
        name=raw_name,
        container_class=body.container_class,
        owner_scope=body.owner_scope,
        policy=body.policy,
        created_by=user.id,
    )

    await audit_action(
        action="container.create",
        target_type="container",
        target_id=container_id,
        result=AuditResult.ALLOW,
        actor_id=user.id,
        severity="normal",
        audit_dao=audit_dao,
        metadata_json=json.dumps(
            {
                "image": body.image,
                "name": raw_name,
                "class": body.container_class.value,
                "policy": body.policy.value,
            }
        ),
    )

    return _container_summary_from_docker(raw, entry.container_class, entry.policy, entry.owner_scope)


@router.get("/", response_model=list[ContainerSummaryDTO], name="list_containers")
async def list_containers(
    filter_class: ContainerClass | None = Query(default=None, alias="class"),
    docker: DockerService = Depends(get_docker),
    registry_dao: ContainerRegistryDAO = Depends(),
    _user: User = Depends(require_permission("containers", "read")),
) -> list[ContainerSummaryDTO]:
    """
    Return all containers visible on the connected engine.

    Merges live Docker data with the server-side registry classification.
    """
    raw_containers = await docker.list_containers(all_=True)
    registry_map = {r.container_id: r for r in await registry_dao.list_all()}

    result: list[ContainerSummaryDTO] = []
    for raw in raw_containers:
        cid = raw.get("Id", "")
        entry = registry_map.get(cid)

        if entry is None:
            # Auto-classify unregistered containers by their name
            names: list[str] = raw.get("Names", [raw.get("Name", "")])
            raw_name = names[0].lstrip("/") if names else cid[:12]
            auto_cls, auto_scope = _classify_by_name(raw_name)
            # Persist so we don't re-classify every time
            entry = await registry_dao.upsert(
                container_id=cid,
                name=raw_name,
                container_class=auto_cls,
                owner_scope=auto_scope,
                policy=ContainerPolicy.FREE,
            )

        cls = entry.container_class
        pol = entry.policy
        scope = entry.owner_scope

        if filter_class and cls != filter_class:
            continue

        result.append(_container_summary_from_docker(raw, cls, pol, scope))
    return result


@router.get("/{container_id}", response_model=ContainerDetailDTO, name="get_container")
async def get_container(
    container_id: str,
    docker: DockerService = Depends(get_docker),
    entry: Any = Depends(get_registry_entry),
    _user: User = Depends(require_permission("containers", "read")),
    enforcer: PolicyEnforcer = Depends(get_policy_enforcer),
    root_mode: bool = Depends(get_root_mode_active),
) -> ContainerDetailDTO:
    """Return full inspect data for a single container."""
    enforcer.enforce(Action.VIEW, entry.container_class, entry.policy, root_mode)
    raw = await docker.inspect_container(container_id)
    raw_state = raw.get("State", {})
    raw_config = raw.get("Config", {})
    names = [raw.get("Name", "").lstrip("/")]
    network_settings = raw.get("NetworkSettings") or {}
    raw_port_map = network_settings.get("Ports")
    if not isinstance(raw_port_map, dict):
        raw_port_map = {}
    raw_networks = network_settings.get("Networks")
    if not isinstance(raw_networks, dict):
        raw_networks = {}
    ports = list(raw_port_map.keys())
    networks = list(raw_networks.keys())
    return ContainerDetailDTO(
        id=raw.get("Id", ""),
        name=names[0] if names else container_id[:12],
        image=raw_config.get("Image", ""),
        status=raw_state.get("Status", ""),
        state=raw_state.get("Status", ""),
        created=raw.get("Created", ""),
        ports=[{"port": p} for p in ports],
        networks=networks,
        container_class=entry.container_class,
        policy=entry.policy,
        owner_scope=entry.owner_scope,
        raw=raw,
    )


@router.post(
    "/{container_id}/{action}",
    status_code=status.HTTP_204_NO_CONTENT,
    name="container_lifecycle",
)
async def container_lifecycle(
    container_id: str,
    action: str,
    user: User = Depends(require_permission("containers", "start")),
    docker: DockerService = Depends(get_docker),
    entry: Any = Depends(get_registry_entry),
    enforcer: PolicyEnforcer = Depends(get_policy_enforcer),
    root_mode: bool = Depends(get_root_mode_active),
    audit_dao: AuditDAO = Depends(),
) -> None:
    """
    Perform a lifecycle action (start/stop/restart/pause/unpause) on a container.

    Action is policy-enforced before execution.
    """
    policy_action = _LIFECYCLE_ACTION_MAP.get(action)
    if policy_action is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown action '{action}'. Valid: {list(_LIFECYCLE_ACTION_MAP)}",
        )

    try:
        enforcer.enforce(policy_action, entry.container_class, entry.policy, root_mode)
    except HTTPException:
        await audit_action(
            action=f"container.{action}",
            target_type="container",
            target_id=container_id,
            result=AuditResult.DENY,
            actor_id=user.id,
            severity=_severity(root_mode),
            audit_dao=audit_dao,
        )
        raise

    match action:
        case "start":
            await docker.start(container_id)
        case "stop":
            await docker.stop(container_id)
        case "restart":
            await docker.restart(container_id)
        case "pause":
            await docker.pause(container_id)
        case "unpause":
            await docker.unpause(container_id)

    await audit_action(
        action=f"container.{action}",
        target_type="container",
        target_id=container_id,
        result=AuditResult.ALLOW,
        actor_id=user.id,
        severity=_severity(root_mode),
        audit_dao=audit_dao,
    )


@router.get("/{container_id}/logs", name="container_logs")
async def container_logs(
    container_id: str,
    tail: int = Query(default=100, ge=1, le=10000),
    follow: bool = Query(default=False),
    user: User = Depends(require_permission("containers", "logs")),
    docker: DockerService = Depends(get_docker),
    entry: Any = Depends(get_registry_entry),
    enforcer: PolicyEnforcer = Depends(get_policy_enforcer),
    root_mode: bool = Depends(get_root_mode_active),
    audit_dao: AuditDAO = Depends(),
) -> StreamingResponse:
    """
    Stream container logs.

    Supports tail= and follow= query parameters.
    """
    enforcer.enforce(Action.LOGS_VIEW, entry.container_class, entry.policy, root_mode)

    await audit_action(
        action="container.logs.view",
        target_type="container",
        target_id=container_id,
        result=AuditResult.ALLOW,
        actor_id=user.id,
        severity=_severity(root_mode),
        audit_dao=audit_dao,
    )

    async def _log_stream() -> Any:
        async for line in docker.logs(container_id, tail=tail, follow=follow):
            yield line

    return StreamingResponse(_log_stream(), media_type="text/plain")


@router.post("/{container_id}/exec", response_model=ExecTokenDTO, name="container_exec")
async def container_exec(
    container_id: str,
    body: ExecTokenRequest,
    user: User = Depends(require_permission("containers", "exec")),
    docker: DockerService = Depends(get_docker),
    entry: Any = Depends(get_registry_entry),
    enforcer: PolicyEnforcer = Depends(get_policy_enforcer),
    root_mode: bool = Depends(get_root_mode_active),
    audit_dao: AuditDAO = Depends(),
) -> ExecTokenDTO:
    """
    Create an exec session and return its ID.

    The client should use the returned exec_id with a websocket connection.
    """
    try:
        enforcer.enforce(Action.EXEC, entry.container_class, entry.policy, root_mode)
    except HTTPException:
        await audit_action(
            action="container.exec",
            target_type="container",
            target_id=container_id,
            result=AuditResult.DENY,
            actor_id=user.id,
            severity=_severity(root_mode),
            audit_dao=audit_dao,
        )
        raise

    exec_id = await docker.create_exec(container_id, cmd=body.cmd, workdir=body.workdir)

    await audit_action(
        action="container.exec",
        target_type="container",
        target_id=container_id,
        result=AuditResult.ALLOW,
        actor_id=user.id,
        severity="high",  # exec is always high-severity
        audit_dao=audit_dao,
        metadata_json=json.dumps({"cmd": body.cmd}),
    )

    return ExecTokenDTO(exec_id=exec_id)


@router.delete(
    "/{container_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    name="delete_container",
)
async def delete_container(
    container_id: str,
    force: bool = Query(default=False),
    user: User = Depends(require_permission("containers", "delete")),
    docker: DockerService = Depends(get_docker),
    entry: Any = Depends(get_registry_entry),
    enforcer: PolicyEnforcer = Depends(get_policy_enforcer),
    root_mode: bool = Depends(get_root_mode_active),
    registry_dao: ContainerRegistryDAO = Depends(),
    audit_dao: AuditDAO = Depends(),
) -> None:
    """Delete a container (requires policy permission)."""
    try:
        enforcer.enforce(Action.DELETE, entry.container_class, entry.policy, root_mode)
    except HTTPException:
        await audit_action(
            action="container.delete",
            target_type="container",
            target_id=container_id,
            result=AuditResult.DENY,
            actor_id=user.id,
            severity=_severity(root_mode),
            audit_dao=audit_dao,
        )
        raise

    await docker.delete(container_id, force=force)
    await registry_dao.delete(container_id)

    await audit_action(
        action="container.delete",
        target_type="container",
        target_id=container_id,
        result=AuditResult.ALLOW,
        actor_id=user.id,
        severity=_severity(root_mode),
        audit_dao=audit_dao,
    )


@router.put("/{container_id}/register", name="register_container")
async def register_container(
    container_id: str,
    body: RegisterContainerRequest,
    user: User = Depends(require_permission("containers", "update")),
    docker: DockerService = Depends(get_docker),
    registry_dao: ContainerRegistryDAO = Depends(),
    audit_dao: AuditDAO = Depends(),
) -> ContainerSummaryDTO:
    """
    Manually classify/register a container in the server-side registry.

    Backend ignores any client-supplied dgx.* labels; only this endpoint
    sets the authoritative container class.
    """
    try:
        raw = await docker.inspect_container(container_id)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Container {container_id!r} not found on engine.",
        ) from exc

    name = raw.get("Name", "").lstrip("/") or container_id[:12]
    entry = await registry_dao.upsert(
        container_id=container_id,
        name=name,
        container_class=body.container_class,
        owner_scope=body.owner_scope,
        policy=body.policy,
        created_by=user.id,
    )

    await audit_action(
        action="container.register",
        target_type="container",
        target_id=container_id,
        result=AuditResult.ALLOW,
        actor_id=user.id,
        severity="normal",
        audit_dao=audit_dao,
        metadata_json=json.dumps(
            {
                "class": body.container_class.value,
                "policy": body.policy.value,
                "owner_scope": body.owner_scope,
            }
        ),
    )

    raw_list = await docker.list_containers(all_=True)
    raw_c = next((c for c in raw_list if c.get("Id", "").startswith(container_id)), {})
    return _container_summary_from_docker(
        raw_c or {"Id": container_id, "Name": name},
        entry.container_class,
        entry.policy,
        entry.owner_scope,
    )
