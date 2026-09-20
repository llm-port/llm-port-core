"""RayDriver implementing InferenceDriver."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
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
from llm_port_backend.services.inference.drivers.ray.commands import NodeCommandGateway
from llm_port_backend.services.inference.drivers.ray.logs import RayLogReader
from llm_port_backend.services.inference.observability import (
    DeploymentMetrics,
    EnvironmentMetrics,
    LogPage,
    LogSource,
    MetricsPartial,
    ObservabilityUnsupported,
    ReplicaMetrics,
    ScrapeTarget,
)
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

    # ------------------------------------------------------------------
    # Observability (Phase 6)
    # ------------------------------------------------------------------

    @staticmethod
    def _runtime_bundle_payload(environment: InferenceEnvironment | None) -> dict[str, Any] | None:
        """The pinned runtime bundle, so log reads address the right container."""
        if environment is None:
            return None
        bundle_id = (getattr(environment, "config_json", None) or {}).get("runtime_bundle_id")
        if not bundle_id:
            return None
        from llm_port_backend.services.inference.bundles import default_bundle_registry

        bundle = default_bundle_registry.get_bundle(str(bundle_id))
        if bundle is None:
            return None
        return default_bundle_registry.container_launch_spec(
            bundle, name="llm-port-ray-runtime",
        )

    async def _deployment_context(
        self, session: "AsyncSession", deployment: Any
    ) -> "tuple[InferenceEnvironment | None, uuid.UUID | None, str]":
        """Resolve (environment, head node, app name) for a deployment."""
        from llm_port_backend.services.inference.drivers.ray.deployment import app_name_for

        environment = await session.get(InferenceEnvironment, deployment.environment_id)
        head_node_id = getattr(environment, "head_node_id", None) if environment else None
        if head_node_id is None and environment is not None:
            res = await session.execute(
                select(InferenceEnvironmentNode).where(
                    InferenceEnvironmentNode.environment_id == environment.id
                )
            )
            member = next(
                (m for m in res.scalars().all() if (m.role or "").lower() == "head"), None
            )
            head_node_id = member.node_id if member else None
        return environment, head_node_id, app_name_for(deployment)

    async def logs(
        self,
        session: "AsyncSession",
        deployment: Any,
        *,
        source: LogSource = LogSource.RUNTIME_CONTAINER,
        node_id: str | None = None,
        replica_id: str | None = None,
        tail: int = 200,
        since: str | None = None,
        cursor: str | None = None,
        node_control: object | None = None,
    ) -> LogPage:
        """Read normalized logs for *deployment* (Phase 6, "Logs")."""
        if source == LogSource.AGENT:
            raise ObservabilityUnsupported(self.key, "agent logs")
        if node_control is None:
            return LogPage(
                source=source,
                detail="no node control service available; cannot reach the node",
            )

        environment, head_node_id, app_name = await self._deployment_context(session, deployment)
        if head_node_id is None:
            return LogPage(source=source, detail="environment has no head node bound")

        reader = RayLogReader(NodeCommandGateway(node_control))
        return await reader.read(
            deployment,
            source=source,
            head_node_id=head_node_id,
            app_name=app_name,
            node_id=node_id,
            replica_id=replica_id,
            tail=tail,
            since=since,
            runtime_bundle=self._runtime_bundle_payload(environment),
        )

    async def deployment_metrics(
        self,
        session: "AsyncSession",
        deployment: Any,
        *,
        node_control: object | None = None,
    ) -> DeploymentMetrics:
        """Aggregate the Serve application and replica tiers for *deployment*."""
        from llm_port_backend.services.inference.drivers.ray.deployment import (
            _model_server_deployments,
            _serve_app_entry,
        )

        environment, head_node_id, app_name = await self._deployment_context(session, deployment)
        metrics = DeploymentMetrics(
            deployment_id=str(deployment.id),
            app_name=app_name,
            replicas_ready=int(getattr(deployment, "ready_replicas", 0) or 0),
            replicas_total=int(getattr(deployment, "total_replicas", 0) or 0),
            observed_at=datetime.now(tz=UTC),
        )
        if node_control is None or head_node_id is None:
            metrics.partials.append(
                MetricsPartial(
                    tier="serve",
                    reason="no head node reachable; replica counts are the last observed values",
                )
            )
            return metrics

        client = RayClusterClient(node_control)
        try:
            serve_status = await client.probe_serve(
                head_node_id=head_node_id,
                runtime_bundle=self._runtime_bundle_payload(environment),
            )
        except Exception as exc:  # noqa: BLE001 - metrics never fail a request
            metrics.partials.append(MetricsPartial(tier="serve", reason=f"Serve probe failed: {exc}"))
            return metrics

        entry = _serve_app_entry(serve_status, app_name)
        if entry is None:
            metrics.partials.append(
                MetricsPartial(tier="serve", reason=f"application {app_name!r} not present on the cluster")
            )
            return metrics

        metrics.app_status = (entry.get("status") or None)
        ready = total = 0
        for dep_name, dep in _model_server_deployments(entry).items():
            dep_ready = int(dep.get("num_replicas_ready") or 0)
            dep_pending = int(dep.get("num_replicas_pending") or 0)
            ready += dep_ready
            total += dep_ready + dep_pending
            metrics.deployments.append(
                ReplicaMetrics(
                    deployment_name=dep_name,
                    status=dep.get("status"),
                    replicas_ready=dep_ready,
                    replicas_pending=dep_pending,
                    message=dep.get("message") or None,
                )
            )
        metrics.replicas_ready = ready
        metrics.replicas_total = total or metrics.replicas_total

        if environment is not None:
            env_metrics = await self.environment_metrics(
                session, environment, node_control=node_control
            )
            metrics.scrape_targets = env_metrics.scrape_targets
            metrics.partials.extend(env_metrics.partials)
        return metrics

    async def environment_metrics(
        self,
        session: "AsyncSession",
        environment: InferenceEnvironment,
        *,
        node_control: object | None = None,
    ) -> EnvironmentMetrics:
        """Aggregate the cluster tier for *environment*."""
        metrics = EnvironmentMetrics(
            environment_id=str(environment.id), observed_at=datetime.now(tz=UTC)
        )
        head_node_id = getattr(environment, "head_node_id", None)
        if node_control is None or head_node_id is None:
            metrics.partials.append(
                MetricsPartial(tier="cluster", reason="no head node reachable")
            )
            return metrics

        client = RayClusterClient(node_control)
        try:
            status = await client.probe_cluster(
                head_node_id=head_node_id,
                runtime_bundle=self._runtime_bundle_payload(environment),
                include_metrics=True,
            )
        except Exception as exc:  # noqa: BLE001
            metrics.partials.append(MetricsPartial(tier="cluster", reason=f"probe failed: {exc}"))
            return metrics

        metrics.alive = status.alive
        metrics.version = status.version
        metrics.nodes_total = status.num_nodes
        metrics.nodes_alive = status.alive_nodes
        metrics.gpus_total = status.total_gpus
        metrics.gpus_available = status.available_gpus
        metrics.cpus_total = status.total_cpus
        metrics.raw = {"conditions": (environment.observed_status_json or {}).get("conditions", [])}

        targets = ((status.metrics or {}) or {}).get("targets") or []
        for target in targets:
            if not isinstance(target, dict):
                continue
            address = target.get("address")
            port = target.get("port")
            if address and port:
                metrics.scrape_targets.append(
                    ScrapeTarget(
                        node_id=target.get("node_id"),
                        address=str(address),
                        port=int(port),
                        url=target.get("url") or f"http://{address}:{int(port)}/metrics",
                    )
                )

        # Honest partial rather than a silent zero: the deployed runtime image
        # is missing its metrics dependencies, so worker nodes bind no metrics
        # port and only the head reports.
        if metrics.nodes_alive > len(metrics.scrape_targets):
            missing = metrics.nodes_alive - len(metrics.scrape_targets)
            metrics.partials.append(
                MetricsPartial(
                    tier="node_metrics",
                    reason=(
                        f"{missing} of {metrics.nodes_alive} live nodes export no metrics port; "
                        "rebuild the runtime image if its metrics dependencies are missing"
                    ),
                )
            )
        return metrics


