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
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette import status

from llm_port_backend.services.docker.client import DockerService
from llm_port_backend.settings import settings
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
        "container_names": [
            "llm-port-pii",
            "llm-port-pii-worker",
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
        "docker", "compose",
        "-f", compose_file,
    ]
    if os.path.isfile(env_file):
        cmd += ["--env-file", env_file]
    if profile:
        cmd += ["--profile", profile]
    cmd += list(args)

    logger.info("Running: %s", " ".join(cmd))
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_bytes, stderr_bytes = await proc.communicate()
    stdout = stdout_bytes.decode(errors="replace").strip()
    stderr = stderr_bytes.decode(errors="replace").strip()
    if proc.returncode != 0:
        logger.warning(
            "docker compose exited %d: %s",
            proc.returncode,
            stderr or stdout,
        )
    return proc.returncode, stdout, stderr


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
    """Return ``[{name, state}]`` for the requested container names."""
    all_containers = await docker.list_containers(all_=True)
    # Docker container names start with "/" in the API response.
    name_map: dict[str, str] = {}
    for c in all_containers:
        for n in c.get("Names", []):
            clean = n.lstrip("/")
            name_map[clean] = c.get("State", "unknown")
    return [
        {"name": cn, "state": name_map.get(cn, "not_found")}
        for cn in container_names
    ]


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

    for mod in _MODULE_DEFS:
        configured: bool = getattr(settings, mod["settings_flag"], False)

        # Always query container states so the UI can show toggle state
        # even for modules whose settings flag is off.
        containers = await _container_states(
            docker, mod.get("container_names", []),
        )

        # A module is "running" when at least one of its containers is up.
        any_running = any(c["state"] == "running" for c in containers)

        # Determine health / status
        if any_running:
            health_url = mod["health_url_fn"]()
            status_val = await _probe_health(health_url)
        elif configured:
            status_val = "configured"
        else:
            status_val = "disabled"

        result.append(
            {
                "name": mod["name"],
                "display_name": mod["display_name"],
                "description": mod["description"],
                "configured": configured,
                "enabled": any_running,
                "status": status_val,
                "containers": containers,
            }
        )

    return JSONResponse(status_code=200, content={"services": result})


# ── PUT /services/{name}/enable ───────────────────────────────────────

@router.put("/services/{name}/enable")
async def enable_module(
    name: str,
    request: Request,
    _user=Depends(require_permission("modules", "manage")),
    docker: DockerService = Depends(get_docker),
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
        "up", "-d", "--remove-orphans",
        profile=profile,
    )

    if rc != 0:
        return JSONResponse(
            status_code=200,
            content={
                "module": name,
                "action": "enable",
                "started": [],
                "errors": [stderr or stdout or f"docker compose exited {rc}"],
            },
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
            "errors": [],
        },
    )


# ── PUT /services/{name}/disable ──────────────────────────────────────

@router.put("/services/{name}/disable")
async def disable_module(
    name: str,
    request: Request,
    _user=Depends(require_permission("modules", "manage")),
    docker: DockerService = Depends(get_docker),
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

    profile = mod.get("compose_profile", name)
    rc, stdout, stderr = await _run_compose("stop", profile=profile)

    if rc != 0:
        return JSONResponse(
            status_code=200,
            content={
                "module": name,
                "action": "disable",
                "stopped": [],
                "errors": [stderr or stdout or f"docker compose exited {rc}"],
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
