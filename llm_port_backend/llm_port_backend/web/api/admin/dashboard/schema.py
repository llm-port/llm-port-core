"""Schemas for admin dashboard endpoints."""

from __future__ import annotations

from pydantic import BaseModel, Field


class DashboardHealthBadge(str):
    """Simple health badge values for summary cards."""

    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"


class DashboardHealthItemDTO(BaseModel):
    """Health state for a dependency/service."""

    name: str
    status: str
    detail: str | None = None


class DashboardTopUserDTO(BaseModel):
    """Top container by resource dimension."""

    container_id: str
    name: str
    value: float
    unit: str


class DashboardOverviewDTO(BaseModel):
    """Single payload used by the Admin Dashboard landing page."""

    system_status: str
    system_badge: str

    cpu_percent: float | None = None
    load_1m: float | None = None
    load_5m: float | None = None
    load_15m: float | None = None

    ram_used_bytes: int | None = None
    ram_total_bytes: int | None = None
    swap_used_bytes: int | None = None
    swap_total_bytes: int | None = None

    disk_free_bytes: int
    disk_total_bytes: int
    disk_free_percent: float

    network_rx_bytes: int | None = None
    network_tx_bytes: int | None = None

    containers_running: int
    containers_total: int
    containers_restarting: int
    restart_rate_1h: int
    restart_rate_24h: int

    api_error_rate_5xx: float = 0.0

    postgres_connections: int | None = None
    postgres_max_connections: int | None = None

    gpu_util_percent: float | None = None
    gpu_vram_used_bytes: int | None = None
    gpu_vram_total_bytes: int | None = None

    top_cpu_containers: list[DashboardTopUserDTO] = Field(default_factory=list)
    top_memory_containers: list[DashboardTopUserDTO] = Field(default_factory=list)


class DashboardHealthDTO(BaseModel):
    """Aggregated dependency health response."""

    overall_status: str
    items: list[DashboardHealthItemDTO] = Field(default_factory=list)


class GrafanaPanelDTO(BaseModel):
    """Embed definition for a single Grafana panel."""

    panel_id: int
    title: str
    embed_url: str


class GrafanaPanelsDTO(BaseModel):
    """Grafana embed config returned to the frontend."""

    enabled: bool
    grafana_url: str | None = None
    dashboard_uid: str | None = None
    open_dashboard_url: str | None = None
    panels: list[GrafanaPanelDTO] = Field(default_factory=list)
