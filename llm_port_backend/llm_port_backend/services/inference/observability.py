"""Backend-neutral observability contracts for the inference domain (Phase 6).

Phase 6's rule for logs is explicit: *"Expose through the driver abstraction.
Do not expose Ray's log API shapes directly to frontend/API contracts."*  These
DTOs are that abstraction.  A driver translates whatever its backend produces
-- Ray Serve replica logs, a container's stdout, a future Dynamo adapter --
into the same shapes, so the API and the frontend never learn what the backend
is.

Metrics follow the plan's aggregation requirement (Ray environment / Serve
application-deployment / vLLM replica) without turning the backend into a
Prometheus proxy: a normalized snapshot of what LLM.Port already observes,
plus the scrape targets a real Prometheus should collect.  Putting the backend
on the data path for every panel refresh would make a half-scraped cluster look
like a backend error rather than the partial it is.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class LogSource(StrEnum):
    """Where a log page was read from.

    Deliberately generic: a driver maps its own notion of "the process serving
    this deployment" onto these, and the frontend offers them as a filter
    without knowing what runs underneath.
    """

    RUNTIME_CONTAINER = "runtime_container"
    SERVE_REPLICA = "serve_replica"
    AGENT = "agent"


class LogLine(BaseModel):
    """One normalized log line."""

    model_config = ConfigDict(extra="forbid")

    ts: datetime | None = None
    level: str | None = None
    message: str


class LogPage(BaseModel):
    """A page of normalized log lines from one source."""

    model_config = ConfigDict(extra="forbid")

    source: LogSource
    node_id: str | None = None
    replica_id: str | None = None
    lines: list[LogLine] = Field(default_factory=list)
    truncated: bool = False
    next_cursor: str | None = None
    # Why a page is empty matters to an operator: "nothing logged yet" and
    # "the node never answered" look identical otherwise.
    detail: str | None = None


class ReplicaMetrics(BaseModel):
    """Per-replica counts for one Serve deployment (the vLLM replica tier)."""

    model_config = ConfigDict(extra="forbid")

    deployment_name: str
    status: str | None = None
    replicas_ready: int = 0
    replicas_pending: int = 0
    message: str | None = None


class ScrapeTarget(BaseModel):
    """A Prometheus endpoint a real scraper should collect."""

    model_config = ConfigDict(extra="forbid")

    node_id: str | None = None
    address: str
    port: int
    url: str


class MetricsPartial(BaseModel):
    """A tier that could not be fully reported, and why.

    Phase 6 has to render honestly: the deployed runtime image is missing its
    metrics dependencies, so worker nodes bind no metrics port.  Reporting zero
    there would be a lie, and reporting an error would hide the tiers that do
    work.  Each partial names the tier and the reason instead.
    """

    model_config = ConfigDict(extra="forbid")

    tier: str
    reason: str


class DeploymentMetrics(BaseModel):
    """Aggregated metrics for one deployment (Serve application + replicas)."""

    model_config = ConfigDict(extra="forbid")

    deployment_id: str
    app_name: str | None = None
    app_status: str | None = None
    replicas_ready: int = 0
    replicas_total: int = 0
    deployments: list[ReplicaMetrics] = Field(default_factory=list)
    scrape_targets: list[ScrapeTarget] = Field(default_factory=list)
    partials: list[MetricsPartial] = Field(default_factory=list)
    observed_at: datetime | None = None


class EnvironmentMetrics(BaseModel):
    """Aggregated metrics for one environment (the cluster tier)."""

    model_config = ConfigDict(extra="forbid")

    environment_id: str
    alive: bool = False
    version: str | None = None
    nodes_total: int = 0
    nodes_alive: int = 0
    gpus_total: float = 0.0
    gpus_available: float = 0.0
    cpus_total: float = 0.0
    cpus_available: float = 0.0
    scrape_targets: list[ScrapeTarget] = Field(default_factory=list)
    partials: list[MetricsPartial] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)
    observed_at: datetime | None = None


class ObservabilityUnsupported(NotImplementedError):
    """The driver cannot serve this observability operation.

    Raised rather than returning an empty page so a missing capability is an
    explicit 501 at the API, never an empty log view that looks like a healthy
    deployment with nothing to say.
    """

    def __init__(self, driver_key: str, operation: str) -> None:
        super().__init__(f"driver {driver_key!r} does not support {operation}")
        self.driver_key = driver_key
        self.operation = operation
