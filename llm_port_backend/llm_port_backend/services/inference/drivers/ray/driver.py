"""RayDriver implementing InferenceDriver."""

from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from llm_port_backend.db.models.inference import (
    InferenceControlPlane,
    InferenceEnvironment,
    InferenceEnvironmentNode,
)
from llm_port_backend.services.inference.capabilities import CapabilityDocument
from llm_port_backend.services.inference.contracts import InferenceDriver
from llm_port_backend.services.inference.drivers.ray.client import RayClusterClient
from llm_port_backend.services.inference.drivers.ray.deployment import (
    RayDeploymentManager,
)
from llm_port_backend.services.inference.drivers.ray.environment import (
    RayEnvironmentManager,
)

if TYPE_CHECKING:  # pragma: no cover - import-time only
    from sqlalchemy.ext.asyncio import AsyncSession

    from llm_port_backend.services.nodes.service import NodeControlService

log = logging.getLogger(__name__)


async def _resolve_probe_head_node(
    session: "AsyncSession", control_plane_id: uuid.UUID
) -> uuid.UUID | None:
    """Find a bound environment's head node to probe.

    A control plane is probed through a (head) node of one of the environments
    it hosts.  Preference order: an environment's explicit ``head_node_id``
    column, then any environment whose membership marks a node with the
    ``head`` role.  Returns ``None`` when no head is bound — in which case the
    caller records an honest no-op rather than failing.
    """
    envs_result = await session.execute(
        select(InferenceEnvironment).where(
            InferenceEnvironment.control_plane_id == control_plane_id
        )
    )
    envs = list(envs_result.scalars().all())
    for env in envs:
        if env.head_node_id is not None:
            return env.head_node_id
    for env in envs:
        node_result = await session.execute(
            select(InferenceEnvironmentNode).where(
                InferenceEnvironmentNode.environment_id == env.id,
                InferenceEnvironmentNode.role == "head",
            )
        )
        head = node_result.scalars().first()
        if head is not None:
            return head.node_id
    return None


class RayDriver(InferenceDriver):
    """InferenceDriver implementation for Ray clusters.

    The driver is constructed with no arguments by the
    :class:`~llm_port_backend.services.inference.registry.DriverRegistry`;
    everything it needs at call time (a DB session and a node control
    service) is handed in by the reconciliation layer, which owns them.
    """

    key: str = "ray"

    def __init__(self) -> None:
        self.environment_manager = RayEnvironmentManager()
        self.deployment_manager = RayDeploymentManager()

    async def probe(
        self,
        control_plane: InferenceControlPlane,
        session: "AsyncSession | None" = None,
        node_control: "NodeControlService | None" = None,
    ) -> dict[str, Any]:
        """Probe a Ray control plane by reaching one of its head nodes.

        Dispatches ``GET_RAY_STATUS`` to the head node of the first bound
        environment and reports the live cluster state.  When a session or
        node control service is not supplied, or no head node is bound, or the
        probe errors, this returns an **honest no-op** report
        (``reconciled=False``) rather than a fabricated healthy status.
        """
        if session is None or node_control is None:
            return {
                "reconciled": False,
                "probed": False,
                "driver": self.key,
                "reason": "probe context not available",
            }
        try:
            head_node_id = await _resolve_probe_head_node(session, control_plane.id)
        except Exception as exc:  # noqa: BLE001 - never wedge the reconcile loop
            log.warning("Ray probe: failed to resolve head node: %s", exc)
            return {
                "reconciled": False,
                "probed": False,
                "driver": self.key,
                "reason": f"head node resolution failed: {exc}",
            }
        if head_node_id is None:
            return {
                "reconciled": False,
                "probed": False,
                "driver": self.key,
                "reason": "no head node bound to any environment",
            }

        client = RayClusterClient(node_control)
        try:
            status = await client.probe_cluster(head_node_id=head_node_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("Ray probe: GET_RAY_STATUS failed: %s", exc)
            return {
                "reconciled": False,
                "probed": False,
                "driver": self.key,
                "reason": f"probe dispatch failed: {exc}",
            }

        report: dict[str, Any] = {
            "reconciled": True,
            "probed": True,
            "driver": self.key,
            "alive": status.alive,
            "version": status.version,
            "num_nodes": status.num_nodes,
            "total_gpus": status.total_gpus,
            "total_cpus": status.total_cpus,
            "available_gpus": status.available_gpus,
            "cluster_address": status.cluster_address,
            "head_address": status.head_address,
            "cluster": status.model_dump(),
            "reason": None,
        }

        # Additive Serve tier: an extra command per probe, best-effort.  A
        # failed/dispatched-as-noop Serve probe must never fail the report.
        if status.alive:
            try:
                serve = await client.probe_serve(head_node_id=head_node_id)
                report["serve"] = serve.model_dump()
            except Exception as exc:  # noqa: BLE001
                log.warning("Ray probe: GET_RAY_SERVE_STATUS failed: %s", exc)
                report["serve"] = {"alive": False, "available": False}

        return report

    async def capabilities(self, environment: InferenceEnvironment) -> CapabilityDocument:
        """Report the ray driver's static capability document.

        The full environment/version-specific capability snapshot (step 7 of
        the environment reconcile loop) is written by
        :class:`RayEnvironmentManager` into ``capabilities_json``.
        """
        return CapabilityDocument.from_dict({
            "driver": self.key,
            "deployment": {
                "fixed_replicas": True,
                "autoscaling": True,
            },
            "topology": {
                "multi_node": True,
                "tensor_parallel": True,
                "pipeline_parallel": True,
            },
            "routing": {
                "strategies": ["default", "prefix_affinity"],
            },
            "artifacts": {
                "local_path": True,
                "llmport_sync": True,
            },
        })

