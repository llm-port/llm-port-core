"""Admin dashboard endpoints (overview, health, grafana embeds)."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
from collections.abc import Iterable
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, Request
from prometheus_client import REGISTRY
from sqlalchemy import text

from llm_port_backend.db.models.users import User
from llm_port_backend.services.docker.client import DockerService
from llm_port_backend.settings import settings
from llm_port_backend.web.api.admin.dashboard.schema import (
    DashboardHealthDTO,
    DashboardHealthItemDTO,
    DashboardOverviewDTO,
    DashboardTopUserDTO,
    GrafanaPanelDTO,
    GrafanaPanelsDTO,
)
from llm_port_backend.web.api.admin.dependencies import get_docker, require_superuser

router = APIRouter()

_REQUEST_COUNTER_NAMES = {
    "http_requests_total",
    "http_request_duration_seconds_count",
    "http_server_requests_total",
}


def _to_float(value: object, default: float = 0.0) -> float:
    """Safely convert any numeric-ish value to float."""
    with contextlib.suppress(TypeError, ValueError):
        return float(value)
    return default


def _parse_grafana_panel_ids(raw: str | None) -> list[int]:
    """Parse comma-separated panel IDs from settings."""
    if not raw:
        return []
    parsed: list[int] = []
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        with contextlib.suppress(ValueError):
            parsed.append(int(token))
    return parsed


def _slugify_dashboard(value: str) -> str:
    """Return a safe Grafana dashboard slug fallback."""
    cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in value).strip("-")
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned or "dashboard"


def _dashboard_health_badge(statuses: Iterable[str]) -> str:
    """Map item statuses to green/yellow/red badge."""
    unique = {s.lower() for s in statuses}
    if "down" in unique:
        return "red"
    if "degraded" in unique:
        return "yellow"
    return "green"


def _docker_cpu_percent(stats: dict[str, object]) -> float:
    """Compute Docker CPU percent from stats payload."""
    cpu_stats = stats.get("cpu_stats", {}) or {}
    precpu_stats = stats.get("precpu_stats", {}) or {}

    current_total = _to_float(
        ((cpu_stats.get("cpu_usage", {}) or {}).get("total_usage")),
        default=0.0,
    )
    previous_total = _to_float(
        ((precpu_stats.get("cpu_usage", {}) or {}).get("total_usage")),
        default=0.0,
    )
    current_system = _to_float(cpu_stats.get("system_cpu_usage"), default=0.0)
    previous_system = _to_float(precpu_stats.get("system_cpu_usage"), default=0.0)
    online_cpus = int(
        _to_float(
            cpu_stats.get("online_cpus")
            or len((cpu_stats.get("cpu_usage", {}) or {}).get("percpu_usage") or []),
            default=1.0,
        ),
    )

    cpu_delta = current_total - previous_total
    system_delta = current_system - previous_system
    if cpu_delta <= 0 or system_delta <= 0:
        return 0.0
    return (cpu_delta / system_delta) * max(online_cpus, 1) * 100.0


def _docker_memory_usage_bytes(stats: dict[str, object]) -> int:
    """Extract Docker memory usage bytes from stats payload."""
    mem = stats.get("memory_stats", {}) or {}
    usage = int(_to_float(mem.get("usage"), default=0.0))
    cache = int(_to_float((mem.get("stats", {}) or {}).get("cache"), default=0.0))
    effective = usage - cache
    return effective if effective > 0 else usage


def _prometheus_api_5xx_rate_percent() -> float:
    """Compute API 5xx ratio (%) from Prometheus counters in-process."""
    total_requests = 0.0
    error_requests = 0.0

    for metric in REGISTRY.collect():
        if metric.name not in _REQUEST_COUNTER_NAMES:
            continue
        for sample in metric.samples:
            if not sample.name.endswith("_total") and not sample.name.endswith("_count"):
                continue
            value = _to_float(sample.value, default=0.0)
            status = (
                sample.labels.get("status")
                or sample.labels.get("status_code")
                or sample.labels.get("code")
                or ""
            )
            if not status:
                continue
            total_requests += value
            if str(status).startswith("5"):
                error_requests += value

    if total_requests <= 0:
        return 0.0
    return (error_requests / total_requests) * 100.0


async def _tcp_health(host: str, port: int, timeout: float = 0.8) -> bool:
    """Quick TCP-level health probe."""
    try:
        conn = asyncio.open_connection(host, port)
        reader, writer = await asyncio.wait_for(conn, timeout=timeout)
        writer.close()
        await writer.wait_closed()
        return True
    except Exception:
        return False


def _collect_gpu_metrics() -> dict[str, float | int | None]:
    """Collect GPU utilization and VRAM usage.

    Delegates to the centralised ``services.gpu.metrics`` module which
    supports NVIDIA (pynvml), AMD (Linux sysfs), and Windows perf
    counters across all GPU vendors.
    """
    from llm_port_backend.services.gpu.metrics import collect_gpu_metrics  # noqa: PLC0415

    m = collect_gpu_metrics()
    return {
        "util": m.util_percent,
        "vram_used": m.vram_used_bytes,
        "vram_total": m.vram_total_bytes,
    }


def _collect_host_snapshot() -> dict[str, object]:
    """Collect host snapshot with optional psutil support."""
    snapshot: dict[str, object] = {
        "cpu_percent": None,
        "load": (None, None, None),
        "ram_used": None,
        "ram_total": None,
        "swap_used": None,
        "swap_total": None,
        "network_rx": None,
        "network_tx": None,
        "gpu_util_percent": None,
        "gpu_vram_used_bytes": None,
        "gpu_vram_total_bytes": None,
    }

    with contextlib.suppress(Exception):
        load = os.getloadavg()
        snapshot["load"] = load

    with contextlib.suppress(ImportError, Exception):
        import psutil  # noqa: PLC0415

        snapshot["cpu_percent"] = float(psutil.cpu_percent(interval=0.1))
        vm = psutil.virtual_memory()
        sm = psutil.swap_memory()
        net = psutil.net_io_counters()
        snapshot["ram_used"] = int(vm.used)
        snapshot["ram_total"] = int(vm.total)
        snapshot["swap_used"] = int(sm.used)
        snapshot["swap_total"] = int(sm.total)
        snapshot["network_rx"] = int(net.bytes_recv)
        snapshot["network_tx"] = int(net.bytes_sent)

    # GPU metrics — try NVIDIA pynvml first, fall back to Windows
    # performance counters (works with AMD / Intel / any GPU).
    gpu = _collect_gpu_metrics()
    snapshot["gpu_util_percent"] = gpu["util"]
    snapshot["gpu_vram_used_bytes"] = gpu["vram_used"]
    snapshot["gpu_vram_total_bytes"] = gpu["vram_total"]

    return snapshot


@router.get("/overview", response_model=DashboardOverviewDTO, name="admin_dashboard_overview")
async def overview(
    request: Request,
    docker: DockerService = Depends(get_docker),
    _user: Annotated[User, Depends(require_superuser)] = None,  # type: ignore[assignment]
) -> DashboardOverviewDTO:
    """Return an aggregated snapshot for the admin dashboard landing page."""
    raw_containers = await docker.list_containers(all_=True)
    containers_total = len(raw_containers)
    containers_running = sum(1 for c in raw_containers if str(c.get("State", "")).lower() == "running")
    containers_restarting = sum(
        1 for c in raw_containers if str(c.get("State", "")).lower() == "restarting"
    )

    restart_counts = [_to_float(c.get("RestartCount"), default=0.0) for c in raw_containers]
    restart_total = int(sum(restart_counts))

    cpu_users: list[DashboardTopUserDTO] = []
    memory_users: list[DashboardTopUserDTO] = []
    aggregate_cpu_percent = 0.0
    aggregate_mem_used = 0
    aggregate_mem_limit = 0
    aggregate_net_rx = 0
    aggregate_net_tx = 0
    running_containers = [c for c in raw_containers if str(c.get("State", "")).lower() == "running"]
    for container in running_containers:
        cid = str(container.get("Id", ""))
        if not cid:
            continue
        try:
            stats = await docker.container_stats(cid)
        except Exception:
            continue
        cpu_percent = round(_docker_cpu_percent(stats), 2)
        mem_bytes = _docker_memory_usage_bytes(stats)
        mem_limit = int(_to_float((stats.get("memory_stats", {}) or {}).get("limit"), default=0.0))
        networks = (stats.get("networks", {}) or {})
        if isinstance(networks, dict):
            for iface in networks.values():
                if isinstance(iface, dict):
                    aggregate_net_rx += int(_to_float(iface.get("rx_bytes"), default=0.0))
                    aggregate_net_tx += int(_to_float(iface.get("tx_bytes"), default=0.0))
        aggregate_cpu_percent += cpu_percent
        aggregate_mem_used += mem_bytes
        if mem_limit > 0:
            aggregate_mem_limit += mem_limit
        names = container.get("Names", []) or []
        display_name = str(names[0]).lstrip("/") if names else cid[:12]
        cpu_users.append(
            DashboardTopUserDTO(
                container_id=cid,
                name=display_name,
                value=cpu_percent,
                unit="%",
            ),
        )
        memory_users.append(
            DashboardTopUserDTO(
                container_id=cid,
                name=display_name,
                value=float(mem_bytes),
                unit="B",
            ),
        )

    cpu_users = sorted(cpu_users, key=lambda item: item.value, reverse=True)[:5]
    memory_users = sorted(memory_users, key=lambda item: item.value, reverse=True)[:5]

    host = _collect_host_snapshot()
    load_1, load_5, load_15 = host["load"]

    cpu_percent = host["cpu_percent"]
    if cpu_percent is None and aggregate_cpu_percent > 0:
        cpu_percent = round(aggregate_cpu_percent, 2)

    ram_used = host["ram_used"]
    ram_total = host["ram_total"]
    if ram_used is None and aggregate_mem_used > 0:
        ram_used = aggregate_mem_used
    if ram_total is None and aggregate_mem_limit > 0:
        ram_total = aggregate_mem_limit

    network_rx = host["network_rx"]
    network_tx = host["network_tx"]
    if network_rx is None and aggregate_net_rx > 0:
        network_rx = aggregate_net_rx
    if network_tx is None and aggregate_net_tx > 0:
        network_tx = aggregate_net_tx

    disk = os.statvfs("/") if os.name != "nt" else None
    if disk is not None:
        disk_total = int(disk.f_blocks * disk.f_frsize)
        disk_free = int(disk.f_bavail * disk.f_frsize)
    else:
        usage = os.path.abspath(os.sep)
        stat = os.statvfs(usage) if hasattr(os, "statvfs") else None
        if stat is not None:
            disk_total = int(stat.f_blocks * stat.f_frsize)
            disk_free = int(stat.f_bavail * stat.f_frsize)
        else:
            import shutil  # noqa: PLC0415

            du = shutil.disk_usage(os.sep)
            disk_total = int(du.total)
            disk_free = int(du.free)

    postgres_connections: int | None = None
    postgres_max_connections: int | None = None
    with contextlib.suppress(Exception):
        async with request.app.state.db_session_factory() as session:
            current = await session.execute(text("SELECT count(*) FROM pg_stat_activity"))
            maximum = await session.execute(text("SHOW max_connections"))
            postgres_connections = int(current.scalar_one())
            postgres_max_connections = int(maximum.scalar_one())

    system_status = "healthy"
    if containers_restarting > 0:
        system_status = "degraded"
    if postgres_connections is None:
        system_status = "degraded"

    badge = "green"
    if system_status == "degraded":
        badge = "yellow"

    return DashboardOverviewDTO(
        system_status=system_status,
        system_badge=badge,
        cpu_percent=cpu_percent,
        load_1m=load_1,
        load_5m=load_5,
        load_15m=load_15,
        ram_used_bytes=ram_used,
        ram_total_bytes=ram_total,
        swap_used_bytes=host["swap_used"],
        swap_total_bytes=host["swap_total"],
        disk_free_bytes=disk_free,
        disk_total_bytes=disk_total,
        disk_free_percent=(float(disk_free) / float(disk_total) * 100.0) if disk_total else 0.0,
        network_rx_bytes=network_rx,
        network_tx_bytes=network_tx,
        containers_running=containers_running,
        containers_total=containers_total,
        containers_restarting=containers_restarting,
        restart_rate_1h=restart_total,
        restart_rate_24h=restart_total,
        api_error_rate_5xx=round(_prometheus_api_5xx_rate_percent(), 4),
        postgres_connections=postgres_connections,
        postgres_max_connections=postgres_max_connections,
        gpu_util_percent=host.get("gpu_util_percent"),
        gpu_vram_used_bytes=host.get("gpu_vram_used_bytes"),
        gpu_vram_total_bytes=host.get("gpu_vram_total_bytes"),
        top_cpu_containers=cpu_users,
        top_memory_containers=memory_users,
    )


@router.get("/health", response_model=DashboardHealthDTO, name="admin_dashboard_health")
async def health(
    request: Request,
    _user: Annotated[User, Depends(require_superuser)] = None,  # type: ignore[assignment]
) -> DashboardHealthDTO:
    """Return aggregate health of key dependencies."""
    items: list[DashboardHealthItemDTO] = []

    db_ok = False
    with contextlib.suppress(Exception):
        async with request.app.state.db_session_factory() as session:
            ping = await session.execute(text("SELECT 1"))
            db_ok = int(ping.scalar_one()) == 1
    items.append(
        DashboardHealthItemDTO(name="postgres", status="up" if db_ok else "down"),
    )

    rabbit_ok = await _tcp_health(settings.rabbit_host, settings.rabbit_port)
    items.append(
        DashboardHealthItemDTO(name="rabbitmq", status="up" if rabbit_ok else "down"),
    )

    grafana_status = "unknown"
    grafana_detail: str | None = None
    if settings.grafana_url:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get(str(settings.grafana_url))
                grafana_status = "up" if resp.status_code < 500 else "degraded"
                grafana_detail = f"http {resp.status_code}"
        except Exception as exc:
            grafana_status = "down"
            grafana_detail = str(exc)
    items.append(
        DashboardHealthItemDTO(name="grafana", status=grafana_status, detail=grafana_detail),
    )

    overall = "healthy"
    statuses = [item.status for item in items]
    badge = _dashboard_health_badge(statuses)
    if badge == "yellow":
        overall = "degraded"
    if badge == "red":
        overall = "down"

    return DashboardHealthDTO(overall_status=overall, items=items)


@router.get("/grafana/panels", response_model=GrafanaPanelsDTO, name="admin_dashboard_grafana_panels")
async def grafana_panels(
    _user: Annotated[User, Depends(require_superuser)] = None,  # type: ignore[assignment]
) -> GrafanaPanelsDTO:
    """Return Grafana iframe embed configuration for the dashboard landing page."""
    grafana_url = settings.grafana_url
    dashboard_uid = settings.grafana_dashboard_uid_overview
    panel_ids = _parse_grafana_panel_ids(settings.grafana_panels_overview)

    if not grafana_url or not dashboard_uid or not panel_ids:
        return GrafanaPanelsDTO(enabled=False)

    clean_url = grafana_url.rstrip("/")
    dashboard_slug = _slugify_dashboard(dashboard_uid)
    now_ms = int(time.time() * 1000)
    from_ms = now_ms - (6 * 60 * 60 * 1000)
    panels = [
        GrafanaPanelDTO(
            panel_id=panel_id,
            title=f"Panel {panel_id}",
            embed_url=(
                f"{clean_url}/d-solo/{dashboard_uid}/{dashboard_slug}"
                f"?orgId=1&from={from_ms}&to={now_ms}&timezone=browser&refresh=30s"
                f"&panelId={panel_id}"
            ),
        )
        for panel_id in panel_ids
    ]

    return GrafanaPanelsDTO(
        enabled=True,
        grafana_url=clean_url,
        dashboard_uid=dashboard_uid,
        open_dashboard_url=f"{clean_url}/d/{dashboard_uid}/{dashboard_slug}?orgId=1",
        panels=panels,
    )
