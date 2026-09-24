"""Backend-side schemas for Ray driver config and status."""

from typing import Any
from pydantic import BaseModel


#: Ports a new cluster is created with, stored in its config.
#:
#: Ray's own defaults collide with LLM.Port on the machine that runs the
#: server: 6379 is Redis (published on the host by the compose file) and 8000
#: is the backend in a dev install. A machine that is both server and GPU node
#: -- the first install most people make -- could not start its cluster: Ray's
#: GCS failed on "port 6379 ... Address already in use", and was retried on
#: the same port every minute. The class defaults below stay Ray's, because
#: clusters created before this run on them.
NEW_CLUSTER_PORTS: dict[str, int] = {"head_port": 6390, "serve_http_port": 8010}


class RayEnvironmentConfig(BaseModel):
    """Configuration stored in InferenceEnvironment.config_json."""
    ray_version: str = "2.58.0"
    head_port: int = 6379
    dashboard_port: int = 8265
    dashboard_host: str = "127.0.0.1"
    object_store_memory: int | None = None
    extra_ray_start_args: dict[str, str] = {}
    node_env_vars: dict[str, str] = {}
    serve_proxy_location: str = "HeadOnly"
    serve_http_host: str | None = None
    serve_http_port: int = 8000


class RayProbeResult(BaseModel):
    """Structured probe output."""
    alive: bool
    version: str | None = None
    num_nodes: int = 0
    total_gpus: float = 0
    available_gpus: float = 0


class RayClusterStatus(BaseModel):
    """Parsed cluster state from GET_RAY_STATUS command.

    The agent answers with the enriched ``RayEnvironmentStatus`` (SDK-first,
    Dashboard-independent): the flat tier (Tier A) is what maps environment
    health; ``serve`` / ``metrics`` / ``state`` / ``capabilities`` are
    additive tiers — an unhealthy tier never flips cluster health.  New
    fields default so a result from an older agent still parses.
    """
    alive: bool
    #: Whether the node answered at all.
    #:
    #: ``alive=False`` is ambiguous on its own: it is both "the cluster told
    #: us it is down" and "we never heard back".  Only the first is a fact,
    #: and reporting the second as one produces a screen full of zeros that
    #: look measured.
    observed: bool = True
    version: str | None = None
    num_nodes: int = 0
    nodes: list[dict[str, Any]] = []
    total_gpus: float = 0
    available_gpus: float = 0
    cluster_address: str | None = None
    # Enriched flat tier (still Tier A — part of cluster health).
    total_cpus: float = 0
    head_address: str | None = None
    # Additive tiers (non-gating; best-effort parse).
    capabilities: dict[str, Any] = {}
    serve: dict[str, Any] | None = None
    metrics: dict[str, Any] | None = None
    state: dict[str, Any] | None = None

    @staticmethod
    def _record_is_alive(node: dict[str, Any]) -> bool:
        """Is one ``ray.nodes()`` record a live cluster member?"""
        if "alive" in node and not node["alive"]:
            return False
        if "state" in node and str(node["state"]).upper() not in {"ALIVE", "UP", "ACTIVE"}:
            return False
        return True

    @property
    def alive_node_records(self) -> list[dict[str, Any]]:
        """The subset of ``nodes`` that are live members right now.

        Ray never removes dead node records from the GCS: a worker restart
        leaves its old record behind forever (and a rejoin adds a *third*
        record with the same IP).  Membership therefore has to be counted over
        live records only — the raw list is history, not state.
        """
        return [n for n in self.nodes if self._record_is_alive(n)]

    @property
    def alive_nodes(self) -> int:
        """Number of live cluster members.

        Falls back to ``num_nodes`` when the probe carried no per-node records
        (older agents), where the raw count is the only signal available.
        """
        if not self.nodes:
            return self.num_nodes
        return len(self.alive_node_records)

    def healthy_for(self, expected_nodes: int) -> bool:
        """Is the cluster healthy for an environment expecting *expected_nodes* members?

        Dead records are ignored entirely; a member is only missing when the
        live count falls below what the environment expects.
        """
        if not self.alive:
            return False
        live = self.alive_nodes
        if live <= 0:
            return False
        if expected_nodes > 0:
            return live >= expected_nodes
        return True

    @property
    def all_healthy(self) -> bool:
        """Healthy without a membership expectation (at least one live node)."""
        return self.healthy_for(0)

    @property
    def serve_available(self) -> bool:
        """Whether the Serve control plane reported itself available."""
        return bool((self.serve or {}).get("available", False))

    @property
    def metrics_enabled(self) -> bool:
        """Whether Prometheus scrape targets were discovered (Tier C)."""
        return bool((self.metrics or {}).get("enabled", False))

