"""Ray Core Python SDK probe and status normalization (Dashboard-independent)."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from llm_port_ray_runtime.models import (
    RayAvailableResources,
    RayClusterStatus,
    RayNodeStatus,
    RayResourceTotals,
)

log = logging.getLogger(__name__)

_HEAD_RESOURCE_KEY = "node:__internal_head__"
_ACCELERATOR_PREFIX = "accelerator_type:"


class RayCoreClient:
    """Idempotent in-process attach and status probe for Ray Core."""

    def __init__(self, address: str = "auto") -> None:
        self.address = address
        self._attached = False

    def ensure_attached(self) -> None:
        """Attach to the local Ray cluster if not already attached."""
        import ray

        if not ray.is_initialized():
            ray.init(address=self.address, ignore_reinit_error=True)
            self._attached = True

    def probe(self) -> RayClusterStatus:
        """Query cluster state via direct GCS / Core Python APIs."""
        import ray

        try:
            self.ensure_attached()
        except Exception as exc:
            return RayClusterStatus(alive=False, error=str(exc) if hasattr(RayClusterStatus, "error") else None)

        raw_nodes = ray.nodes()
        cluster_res = ray.cluster_resources()
        avail_res = ray.available_resources()

        head_ip = None
        normalized_nodes: List[RayNodeStatus] = []
        for node in raw_nodes:
            is_head = _HEAD_RESOURCE_KEY in node.get("Resources", {})
            ip = node.get("NodeManagerAddress", "")
            if is_head:
                head_ip = ip

            normalized_nodes.append(
                RayNodeStatus(
                    node_id=node.get("NodeID", ""),
                    node_ip=ip,
                    node_manager_address=ip,
                    node_manager_port=node.get("NodeManagerPort"),
                    node_name=node.get("NodeName"),
                    alive=node.get("Alive", False),
                    is_head=is_head,
                    resources={k: float(v) for k, v in node.get("Resources", {}).items() if isinstance(v, (int, float))},
                    metrics_export_port=node.get("MetricsExportPort"),
                )
            )

        # Split resources into standard, accelerators, and other
        accel_total: Dict[str, float] = {}
        other_total: Dict[str, float] = {}
        for k, v in cluster_res.items():
            if k.startswith(_ACCELERATOR_PREFIX):
                accel_total[k] = float(v)
            elif k not in ("CPU", "GPU", "memory", "object_store_memory"):
                other_total[k] = float(v)

        resources = RayResourceTotals(
            cpu=float(cluster_res.get("CPU", 0.0)),
            gpu=float(cluster_res.get("GPU", 0.0)),
            memory=float(cluster_res.get("memory", 0.0)),
            object_store_memory=float(cluster_res.get("object_store_memory", 0.0)),
            accelerators=accel_total,
            other=other_total,
        )

        accel_avail: Dict[str, float] = {}
        for k, v in avail_res.items():
            if k.startswith(_ACCELERATOR_PREFIX):
                accel_avail[k] = float(v)

        available = RayAvailableResources(
            cpu=float(avail_res.get("CPU", 0.0)),
            gpu=float(avail_res.get("GPU", 0.0)),
            memory=float(avail_res.get("memory", 0.0)),
            object_store_memory=float(avail_res.get("object_store_memory", 0.0)),
            accelerators=accel_avail,
        )

        return RayClusterStatus(
            alive=True,
            ray_version=ray.__version__,
            num_nodes=len(normalized_nodes),
            total_gpus=resources.gpu,
            available_gpus=available.gpu,
            total_cpus=resources.cpu,
            available_cpus=available.cpu,
            cluster_address=getattr(ray.get_runtime_context(), "gcs_address", None),
            head_address=head_ip,
            nodes=normalized_nodes,
            resources=resources,
            available=available,
        )

