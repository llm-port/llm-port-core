"""Ray metrics discovery — Tier C (spec section 2: metrics via Ray metrics
endpoints → Prometheus; no Dashboard required).

Each Ray node runs a metrics agent that exposes Prometheus metrics at
``http://<NodeManagerAddress>:<MetricsExportPort>/metrics``.  The port is
carried in the ``ray.nodes()`` record (``MetricsExportPort``) — verified live
in Ray 2.58, and it is exactly what Ray's own
``ray._private.metrics_agent.PrometheusServiceDiscoveryWriter`` publishes to
Prometheus.  So discovery needs only a GCS round-trip (``ray.nodes()``); the
Dashboard component is not involved and a Prometheus scrape failure never
affects cluster health.

A port of ``0``/``-1``/absent means the node's metrics agent has no port
bound (metrics disabled) — those nodes are simply omitted from the target
list.
"""

from __future__ import annotations

import logging

from llm_port_node_agent.ray import models
from llm_port_node_agent.ray.core import RayCoreClient

log = logging.getLogger(__name__)


class RayMetricsDiscovery:
    """Discovers per-node Prometheus scrape targets from ``ray.nodes()``."""

    def __init__(self, *, core: RayCoreClient | None = None) -> None:
        self._core = core or RayCoreClient()

    def discover(self) -> models.RayMetricsTargets:
        """Return scrape targets for all nodes with a live metrics port.

        Never raises: a discovery failure (not attached, round-trip error)
        yields ``RayMetricsTargets(enabled=False)`` — Tier C is optional and
        must not fail the overall status.
        """
        try:
            self._core.ensure_attached()
            raw_nodes = self._core.ray.nodes()
        except Exception as exc:
            log.info("metrics discovery unavailable: %s", exc)
            return models.RayMetricsTargets(enabled=False)

        targets: list[dict[str, object]] = []
        for rec in raw_nodes or []:
            if not isinstance(rec, dict) or not rec.get("Alive"):
                continue
            port = rec.get("MetricsExportPort")
            try:
                port_int = int(port)
            except (TypeError, ValueError):
                continue
            if port_int <= 0:
                continue
            addr = str(rec.get("NodeManagerAddress", "") or "")
            if not addr:
                continue
            targets.append(
                {
                    "node_id": rec.get("NodeID"),
                    "host": addr,
                    "port": port_int,
                    "url": f"http://{addr}:{port_int}/metrics",
                    "labels": {
                        "ray_node_id": rec.get("NodeID"),
                        "ray_node_name": rec.get("NodeName"),
                    },
                }
            )
        return models.RayMetricsTargets(enabled=bool(targets), targets=targets)
