"""Admin services manifest + module lifecycle endpoints.

Returns the list of optional modules and their current status so the
frontend can show / hide UI sections dynamically.  Also provides
enable / disable endpoints that start / stop the Docker containers
belonging to a module via ``docker compose --profile <profile>``.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette import status

from llm_port_backend.db.models.users import User
from llm_port_backend.services.notifications import NotificationService
from llm_port_backend.services.system_settings import SystemSettingsService
from llm_port_backend.services.docker.client import DockerService
from llm_port_backend.settings import settings
from llm_port_backend.web.api.admin.system.views import get_system_settings_service
from llm_port_backend.web.api.admin.dependencies import get_docker
from llm_port_backend.web.api.rbac import require_permission

logger = logging.getLogger(__name__)

router = APIRouter()

# ── Module definitions ────────────────────────────────────────────────
# Each entry describes an optional module the backend knows about.
# Adding a new module = append one dict here + add a settings flag.
#
# ``compose_profile`` – the Docker Compose profile name used to bring
#   containers up or tear them down.
# ``compose_services`` – the Docker Compose *service* names to target
#   when stopping a module. Required so that ``docker compose stop``
#   only affects the module's own services, not the entire stack.
# ``container_names`` – the Docker container names belonging to this
#   module (for status queries).

_MODULE_DEFS: list[dict[str, Any]] = [
    {
        "name": "rag",
        "display_name": "RAG Engine",
        "description": (
            "Retrieval-Augmented Generation pipeline with document ingestion, "
            "chunking, embedding, and vector search."
        ),
        "settings_flag": "rag_enabled",
        "health_url_fn": lambda: f"{settings.rag_base_url}/health",
        "compose_profile": "rag",
        "compose_services": [
            "llm-port-rag",
            "llm-port-rag-worker",
            "llm-port-rag-scheduler",
            "llm-port-rag-migrator",
        ],
        "container_names": [
            "llm-port-rag",
            "llm-port-rag-worker",
            "llm-port-rag-scheduler",
        ],
    },
    {
        "name": "pii",
        "display_name": "PII Guard",
        "description": (
            "Personally Identifiable Information detection and redaction "
            "service for request / response payloads."
        ),
        "settings_flag": "pii_enabled",
        "health_url_fn": lambda: f"{settings.pii_service_url}/health",
        "compose_profile": "pii",
        "compose_services": [
            "llm-port-pii",
            "llm-port-pii-worker",
            "llm-port-pii-migrator",
        ],
        "container_names": [
            "llm-port-pii",
            "llm-port-pii-worker",
        ],
    },
    {
        "name": "mailer",
        "display_name": "Mailer",
        "description": (
            "SMTP mail adapter used for password reset and system admin alerts."
        ),
        "settings_flag": "mailer_enabled",
        "health_url_fn": lambda: f"{settings.mailer_service_url.rstrip('/')}/api/health",
        "compose_profile": "mailer",
        "compose_services": [
            "llm-port-mailer",
        ],
        "container_names": [
            "llm-port-mailer",
        ],
    },
]

# Fast lookup by module name.
_MODULE_MAP: dict[str, dict[str, Any]] = {m["name"]: m for m in _MODULE_DEFS}


# ── Helpers ───────────────────────────────────────────────────────────


def _resolve_compose_paths() -> tuple[str, str]:
    """Return (compose_file, env_file) as absolute paths."""
    compose_file = Path(settings.system_compose_file)
    if not compose_file.is_absolute():
        compose_file = Path.cwd() / compose_file
    compose_file = compose_file.resolve()
    env_file = compose_file.parent / ".env"
    return str(compose_file), str(env_file)


async def _run_compose(
    *args: str,
    profile: str | None = None,
) -> tuple[int, str, str]:
    """Run ``docker compose`` with the system compose file.

    Returns ``(returncode, stdout, stderr)``.
    """
    compose_file, env_file = _resolve_compose_paths()
    cmd: list[str] = [
        "docker",
        "compose",
        "-f",
        compose_file,
    ]
    if os.path.isfile(env_file):
        cmd += ["--env-file", env_file]
    if profile:
        cmd += ["--profile", profile]
    cmd += list(args)

    logger.info("Running: %s", " ".join(cmd))

    def _run() -> tuple[int, str, str]:
        result = subprocess.run(
            cmd,
            capture_output=True,
            timeout=120,
        )
        return (
            result.returncode,
            result.stdout.decode(errors="replace").strip(),
            result.stderr.decode(errors="replace").strip(),
        )

    returncode, stdout, stderr = await asyncio.to_thread(_run)
    if returncode != 0:
        logger.warning(
            "docker compose exited %d: %s",
            returncode,
            stderr or stdout,
        )
    return returncode, stdout, stderr


async def _probe_health(url: str) -> str:
    """Return ``"healthy"`` or ``"unhealthy"`` for a single URL."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url)
            return "healthy" if resp.status_code < 400 else "unhealthy"
    except Exception:
        logger.debug("Health check failed for %s", url, exc_info=True)
        return "unhealthy"


async def _container_states(
    docker: DockerService,
    container_names: list[str],
) -> list[dict[str, str]]:
    """Return ``[{name, state}]`` for the requested container names.

    If the Docker daemon is unreachable or returns an error, every
    container is reported as ``"unknown"`` rather than letting the
    exception propagate and crash the services endpoint.
    """
    try:
        all_containers = await docker.list_containers(all_=True)
    except Exception:
        logger.warning("Docker API unreachable — reporting all containers as unknown", exc_info=True)
        return [{"name": cn, "state": "unknown"} for cn in container_names]
    # Docker container names start with "/" in the API response.
    name_map: dict[str, str] = {}
    for c in all_containers:
        for n in c.get("Names", []):
            clean = n.lstrip("/")
            name_map[clean] = c.get("State", "unknown")
    return [{"name": cn, "state": name_map.get(cn, "not_found")} for cn in container_names]


def _container_states_from_name_map(
    name_map: dict[str, str],
    container_names: list[str],
) -> list[dict[str, str]]:
    """Resolve desired container states from a precomputed Docker name->state map."""
    return [{"name": cn, "state": name_map.get(cn, "not_found")} for cn in container_names]


async def _sync_pii_enabled(
    *,
    service: SystemSettingsService,
    enabled: bool,
    actor_id: Any,
) -> list[str]:
    """Sync llm_port_api.pii_enabled and trigger gateway apply flow."""
    try:
        result = await service.update_value(
            key="llm_port_api.pii_enabled",
            value=enabled,
            actor_id=actor_id,
            root_mode_active=False,
            target_host="local",
        )
    except Exception as exc:
        logger.exception("Failed to sync llm_port_api.pii_enabled")
        return [f"Failed to sync llm_port_api.pii_enabled: {exc}"]

    if result.apply_status != "success":
        details = "; ".join(result.messages) if result.messages else "unknown apply failure"
        return [f"Failed to apply llm_port_api.pii_enabled={enabled}: {details}"]
    return []


async def _sync_mailer_enabled(
    *,
    service: SystemSettingsService,
    enabled: bool,
    actor_id: Any,
) -> list[str]:
    """Sync llm_port_mailer.enabled as module lifecycle flag."""
    try:
        result = await service.update_value(
            key="llm_port_mailer.enabled",
            value=enabled,
            actor_id=actor_id,
            root_mode_active=False,
            target_host="local",
        )
    except Exception as exc:
        logger.exception("Failed to sync llm_port_mailer.enabled")
        return [f"Failed to sync llm_port_mailer.enabled: {exc}"]

    if result.apply_status != "success":
        details = "; ".join(result.messages) if result.messages else "unknown apply failure"
        return [f"Failed to apply llm_port_mailer.enabled={enabled}: {details}"]
    return []


async def _emit_module_lifecycle_alert(
    request: Request,
    *,
    module_name: str,
    action: str,
    summary: str,
    details: str,
) -> None:
    session_factory = getattr(request.app.state, "db_session_factory", None)
    if session_factory is None:
        return
    try:
        async with session_factory() as session:
            service = NotificationService(session)
            queued = await service.maybe_enqueue_admin_alert(
                subject=f"Module lifecycle error: {module_name}",
                severity="critical",
                fingerprint=f"module_lifecycle_error:{module_name}:{action}",
                summary=summary,
                details=details,
                source="llm_port_backend.admin.services",
            )
            if queued:
                await session.commit()
    except Exception:
        logger.exception("Failed to enqueue module lifecycle alert for %s.", module_name)


# ── GET /services ─────────────────────────────────────────────────────


@router.get("/services")
async def list_services(
    request: Request,
    docker: DockerService = Depends(get_docker),
) -> JSONResponse:
    """Return the manifest of optional backend modules.

    The frontend uses this to discover which features are available so
    it can show / hide navigation items and page sections dynamically.
    """
    result: list[dict[str, Any]] = []
    health_checks: list[tuple[int, asyncio.Task[str]]] = []

    docker_reachable = True
    try:
        all_containers = await docker.list_containers(all_=True)
        name_map: dict[str, str] = {}
        for container in all_containers:
            for raw_name in container.get("Names", []):
                name_map[raw_name.lstrip("/")] = container.get("State", "unknown")
    except Exception:
        docker_reachable = False
        logger.warning("Docker API unreachable — reporting all containers as unknown", exc_info=True)
        name_map = {}

    for mod in _MODULE_DEFS:
        configured: bool = getattr(settings, mod["settings_flag"], False)
        container_names = mod.get("container_names", [])
        containers = _container_states_from_name_map(name_map, container_names) if docker_reachable else [
            {"name": cn, "state": "unknown"} for cn in container_names
        ]

        any_running = any(c["state"] == "running" for c in containers)
        if any_running:
            result.append(
                {
                    "name": mod["name"],
                    "display_name": mod["display_name"],
                    "description": mod["description"],
                    "configured": configured,
                    "enabled": True,
                    "status": "unknown",
                    "containers": containers,
                }
            )
            health_checks.append((len(result) - 1, asyncio.create_task(_probe_health(mod["health_url_fn"]()))))
            continue

        result.append(
            {
                "name": mod["name"],
                "display_name": mod["display_name"],
                "description": mod["description"],
                "configured": configured,
                "enabled": False,
                "status": "configured" if configured else "disabled",
                "containers": containers,
            }
        )

    if health_checks:
        statuses = await asyncio.gather(*(task for _, task in health_checks), return_exceptions=False)
        for (idx, _), status_val in zip(health_checks, statuses):
            result[idx]["status"] = status_val

    return JSONResponse(status_code=200, content={"services": result})


# ── PUT /services/{name}/enable ───────────────────────────────────────


@router.put("/services/{name}/enable")
async def enable_module(
    name: str,
    request: Request,
    user: User = Depends(require_permission("modules", "manage")),
    docker: DockerService = Depends(get_docker),
    system_settings: SystemSettingsService = Depends(get_system_settings_service),
) -> JSONResponse:
    """Bring up all containers belonging to a module.

    Uses ``docker compose --profile <profile> up -d`` so that containers
    are *created* if they don't exist yet, or *started* if already present.
    """
    mod = _MODULE_MAP.get(name)
    if mod is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown module: {name}",
        )

    profile = mod.get("compose_profile", name)
    rc, stdout, stderr = await _run_compose(
        "up",
        "-d",
        "--remove-orphans",
        profile=profile,
    )

    if rc != 0:
        detail = stderr or stdout or f"docker compose exited {rc}"
        await _emit_module_lifecycle_alert(
            request,
            module_name=name,
            action="enable",
            summary=f"Failed to enable module '{name}'.",
            details=detail,
        )
        return JSONResponse(
            status_code=200,
            content={
                "module": name,
                "action": "enable",
                "started": [],
                "errors": [detail],
            },
        )

    errors: list[str] = []
    if name == "pii":
        errors.extend(
            await _sync_pii_enabled(
                service=system_settings,
                enabled=True,
                actor_id=user.id,
            ),
        )
    elif name == "mailer":
        errors.extend(
            await _sync_mailer_enabled(
                service=system_settings,
                enabled=True,
                actor_id=user.id,
            ),
        )

    if errors:
        await _emit_module_lifecycle_alert(
            request,
            module_name=name,
            action="enable_sync",
            summary=f"Failed to finalize module enable for '{name}'.",
            details="; ".join(errors),
        )

    # Refresh container states for the response.
    containers = await _container_states(docker, mod.get("container_names", []))
    started = [c["name"] for c in containers if c["state"] == "running"]

    return JSONResponse(
        status_code=200,
        content={
            "module": name,
            "action": "enable",
            "started": started,
            "errors": errors,
        },
    )


# ── PUT /services/{name}/disable ──────────────────────────────────────


@router.put("/services/{name}/disable")
async def disable_module(
    name: str,
    request: Request,
    user: User = Depends(require_permission("modules", "manage")),
    docker: DockerService = Depends(get_docker),
    system_settings: SystemSettingsService = Depends(get_system_settings_service),
) -> JSONResponse:
    """Stop all containers belonging to a module.

    Uses ``docker compose --profile <profile> stop`` to gracefully
    stop the containers without removing them.
    """
    mod = _MODULE_MAP.get(name)
    if mod is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown module: {name}",
        )

    if name == "pii":
        errors = await _sync_pii_enabled(
            service=system_settings,
            enabled=False,
            actor_id=user.id,
        )
        if errors:
            await _emit_module_lifecycle_alert(
                request,
                module_name=name,
                action="disable_sync",
                summary=f"Failed to sync module disable for '{name}'.",
                details="; ".join(errors),
            )
            return JSONResponse(
                status_code=200,
                content={
                    "module": name,
                    "action": "disable",
                    "stopped": [],
                    "errors": errors,
                },
            )
    elif name == "mailer":
        errors = await _sync_mailer_enabled(
            service=system_settings,
            enabled=False,
            actor_id=user.id,
        )
        if errors:
            await _emit_module_lifecycle_alert(
                request,
                module_name=name,
                action="disable_sync",
                summary=f"Failed to sync module disable for '{name}'.",
                details="; ".join(errors),
            )
            return JSONResponse(
                status_code=200,
                content={
                    "module": name,
                    "action": "disable",
                    "stopped": [],
                    "errors": errors,
                },
            )

    profile = mod.get("compose_profile", name)
    services = mod.get("compose_services", [])
    rc, stdout, stderr = await _run_compose(
        "stop",
        *services,
        profile=profile,
    )

    if rc != 0:
        detail = stderr or stdout or f"docker compose exited {rc}"
        await _emit_module_lifecycle_alert(
            request,
            module_name=name,
            action="disable",
            summary=f"Failed to disable module '{name}'.",
            details=detail,
        )
        return JSONResponse(
            status_code=200,
            content={
                "module": name,
                "action": "disable",
                "stopped": [],
                "errors": [detail],
            },
        )

    containers = await _container_states(docker, mod.get("container_names", []))
    stopped = [c["name"] for c in containers if c["state"] != "running"]

    return JSONResponse(
        status_code=200,
        content={
            "module": name,
            "action": "disable",
            "stopped": stopped,
            "errors": [],
        },
    )


# ── Container logs for a module ───────────────────────────────────────


@router.get("/services/{name}/logs/{container_name}", name="module_container_logs")
async def module_container_logs(
    name: str,
    container_name: str,
    tail: int = Query(default=200, ge=1, le=10000),
    user: User = Depends(require_permission("modules", "manage")),
    docker: DockerService = Depends(get_docker),
) -> StreamingResponse:
    """Stream logs for a specific container belonging to a module.

    The *container_name* must be one of the containers declared in the
    module definition.  This prevents arbitrary container access through
    this simplified endpoint.
    """
    mod = _MODULE_MAP.get(name)
    if not mod:
        raise HTTPException(status_code=404, detail=f"Unknown module: {name}")

    allowed_containers: list[str] = mod.get("container_names", [])
    if container_name not in allowed_containers:
        raise HTTPException(
            status_code=404,
            detail=f"Container '{container_name}' is not part of module '{name}'.",
        )

    # Resolve the Docker container ID from the name.
    try:
        all_containers = await docker.list_containers(all_=True)
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="Docker API is unreachable.",
        )

    container_id: str | None = None
    for c in all_containers:
        names = [n.lstrip("/") for n in c.get("Names", [])]
        if container_name in names:
            container_id = c.get("Id")
            break

    if not container_id:
        raise HTTPException(
            status_code=404,
            detail=f"Container '{container_name}' is not running or does not exist.",
        )

    async def _stream() -> Any:
        async for line in docker.logs(container_id, tail=tail, follow=False):
            yield line

    return StreamingResponse(_stream(), media_type="text/plain")
