"""The core reconciliation engine for Ray environments.

The manager drives one :class:`InferenceEnvironment` toward its desired state
by issuing node-control Ray lifecycle commands and then observing the live
cluster.  It is constructed with **no arguments** (the driver's
``__init__`` builds it) and receives the :class:`NodeControlService` per call,
so the same manager instance can serve as many environments/sessions as the
reconciler hands it.

The 8-step loop (RUNNING):

    1. validate node health & eligibility (resolve head + workers)
    2. ensure the Ray runtime & version on every member node
    3. ensure the cluster auth token + ``credential_ref`` on the control plane
    4. start the designated head node
    5. join each worker node to the head
    6. verify cluster membership by probing the head
    7. refresh the environment capability snapshot
    8. persist the observed state (status, conditions, address, generation)

For a STOPPED/DELETED environment the loop performs the mirror-image
tear-down and records the stopped observation.

The manager mutates the session (command rows + observed state) but does not
commit; the owning loop commits after a pass.  Remote actions are never wrapped
in an additional DB transaction around the network calls themselves.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import select

from llm_port_backend.db.models.inference import (
    EnvironmentStatus,
    InferenceControlPlane,
    InferenceEnvironment,
    InferenceEnvironmentNode,
)
from llm_port_backend.db.models.node_control import InfraNode, NodeCommandType
from llm_port_backend.services.inference.drivers.ray.client import RayClusterClient
from llm_port_backend.services.inference.drivers.ray.schemas import (
    RayClusterStatus,
    RayEnvironmentConfig,
)
from llm_port_backend.services.inference.drivers.ray.secrets import (
    generate_cluster_token,
    store_cluster_token,
)
from llm_port_backend.services.inference.drivers.ray.status import (
    build_environment_conditions,
    map_cluster_to_environment_status,
)

if TYPE_CHECKING:  # pragma: no cover - import-time only
    from sqlalchemy.ext.asyncio import AsyncSession

    from llm_port_backend.services.nodes.service import NodeControlService

log = logging.getLogger(__name__)


# How long a fire-and-forget lifecycle command has before it is considered
# overdue.  The authoritative membership check is step 6 (the GET_RAY_STATUS
# probe, which has its own polling budget), so a lifecycle dispatch that never
# completes still leaves the loop able to observe reality.
_LIFECYCLE_TIMEOUT_SEC = 300


class RayEnvironmentManager:
    """Drives a Ray environment toward its desired state via node commands."""

    def __init__(self) -> None:
        # Stateless: the NodeControlService is handed in per reconcile call so
        # a single manager instance can be reused across sessions/environments.
        pass

    async def reconcile_environment(
        self,
        session: "AsyncSession",
        environment: InferenceEnvironment,
        *,
        node_control: "NodeControlService | None" = None,
    ) -> None:
        """Drive *environment* toward its desired state.

        Mutates *session* (command rows + observed state) but does not commit —
        the caller owns the transaction boundary.
        """
        log.info("Reconciling Ray environment %s desired=%s", environment.id, environment.desired_state)

        if node_control is None:
            # No node control service: nothing remote can be done.  Record an
            # honest observation so the stop-gap query keeps working.
            self._observe(
                environment,
                map_cluster_to_environment_status(RayClusterStatus(alive=False)),
                RayClusterStatus(alive=False),
                expected_nodes=0,
                reason="node control service unavailable; no live action taken",
            )
            return

        head, workers, nodes = await self._resolve_members(session, environment)
        if not head:
            log.warning("Environment %s has no head member; waiting for membership", environment.id)
            self._observe(
                environment,
                EnvironmentStatus.PENDING,
                RayClusterStatus(alive=False),
                expected_nodes=len(nodes),
                reason="no head node member bound",
            )
            return

        if environment.desired_state in ("stopped", "deleted"):
            await self._teardown(environment, head, workers, node_control)
            self._observe(
                environment,
                EnvironmentStatus.STOPPED,
                RayClusterStatus(alive=False),
                expected_nodes=len(nodes),
                reason="teardown dispatched",
            )
            return

        # desired_state == running
        config = RayEnvironmentConfig.model_validate(environment.config_json or {})
        credential_ref = await self._ensure_cluster_token(session, environment)

        head_host = await self._host_of(session, head.node_id)
        await self._ensure_runtimes(environment, node_control=node_control, nodes=nodes, config=config)
        await self._start_head(environment, head, credential_ref, config, node_control)
        head_address = f"{head_host}:{config.head_port}" if head_host else config.dashboard_host
        await self._join_workers(
            session, environment, workers, head_address, credential_ref, config, node_control
        )

        # 6. Verify cluster membership by probing the head.
        status = await self._verify_cluster(head, node_control)
        # 7. Refresh the environment capability snapshot.
        await self._refresh_capabilities(environment, config)
        # 8. Persist the observed state for the cluster we just reconciled.
        self._observe(
            environment,
            map_cluster_to_environment_status(status),
            status,
            expected_nodes=len(nodes),
            address=status.cluster_address or head_address,
            version=status.version or config.ray_version,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _resolve_members(self, session, environment):
        """Return (head, workers, all_nodes).

        ``head`` is the preferred head: the member whose ``node_id`` equals
        ``environment.head_node_id`` (if set and present) or, failing that,
        the first member whose role is ``head``.
        """
        result = await session.execute(
            select(InferenceEnvironmentNode).where(
                InferenceEnvironmentNode.environment_id == environment.id
            )
        )
        nodes = list(result.scalars().all())

        head = None
        if environment.head_node_id is not None:
            head = next(
                (n for n in nodes if n.node_id == environment.head_node_id), None
            )

        if head is None:
            head = next(
                (n for n in nodes if (n.role or "").lower() == "head"), None
            )

        workers = [
            n
            for n in nodes
            if head is None or n.id != head.id
        ]
        return head, workers, nodes

    async def _host_of(self, session, node_id) -> str | None:
        node = await session.get(InfraNode, node_id)
        if node is None:
            return None
        host = getattr(node, "host", None)
        if host:
            host = str(host).strip()
            if host.startswith("host="):
                host = host[len("host="):]
            if not host:
                host = None
        return host

    async def _ensure_cluster_token(self, session, environment) -> str | None:
        """Step 3: idempotently ensure the control plane has a token + ref.

        Creates the encrypted Ray auth token and persists the opaque
        ``credential_ref`` on the control plane exactly once (subsequent
        reconciles reuse the existing ref, i.e. no token rotation).
        """
        cp_id = environment.control_plane_id
        try:
            cp = await session.get(InferenceControlPlane, cp_id)
        except Exception:  # pragma: no cover - defensive
            cp = None

        if cp is not None and cp.credential_ref:
            return cp.credential_ref

        token = generate_cluster_token()
        ref = await store_cluster_token(session, cp_id, token)
        if cp is not None:
            cp.credential_ref = ref
        return ref

    async def _ensure_runtimes(
        self,
        environment,
        *,
        node_control,
        nodes,
        config,
    ) -> None:
        """Step 2: ensure the Ray runtime & version on every member node."""
        for node in nodes:
            try:
                await node_control.issue_command(
                    node_id=node.node_id,
                    command_type=NodeCommandType.ENSURE_RAY_RUNTIME.value,
                    payload={"version": config.ray_version},
                    issued_by=None,
                    correlation_id=None,
                    timeout_sec=_LIFECYCLE_TIMEOUT_SEC,
                    idempotency_key=f"inference-env:{environment.id}:{environment.generation}:ensure-runtime:{node.node_id}",
                )
            except Exception as e:  # noqa: BLE001 - keep going
                log.warning("ensure_ray_runtime for node %s: %s", node.node_id, e)


    async def _start_head(
        self, environment, head, credential_ref, config, node_control
    ) -> None:
        """Step 4: start the designated head node."""
        try:
            await node_control.issue_command(
                node_id=head.node_id,
                command_type=NodeCommandType.START_RAY_HEAD.value,
                payload={
                    "credential_ref": credential_ref,
                    "version": config.ray_version,
                    "port": config.head_port,
                    "dashboard_port": config.dashboard_port,
                    "dashboard_host": config.dashboard_host,
                },
                issued_by=None,
                correlation_id=None,
                timeout_sec=_LIFECYCLE_TIMEOUT_SEC,
                idempotency_key=f"inference-env:{environment.id}:{environment.generation}:start-head",
            )
        except Exception as e:  # noqa: BLE001
            log.warning("start_ray_head for node %s: %s", head.node_id, e)


    async def _join_workers(
        self,
        session,
        environment,
        workers,
        head_address,
        credential_ref,
        config,
        node_control,
    ) -> None:
        for worker in workers:
            try:
                host = await self._host_of(session, worker.node_id)
                await node_control.issue_command(
                    node_id=worker.node_id,
                    command_type=NodeCommandType.JOIN_RAY_CLUSTER.value,
                    payload={
                        "head_address": head_address,
                        "credential_ref": credential_ref,
                        "version": config.ray_version,
                        "node_ip_address": host,
                    },
                    issued_by=None,
                    correlation_id=None,
                    timeout_sec=_LIFECYCLE_TIMEOUT_SEC,
                    idempotency_key=f"inference-env:{environment.id}:{environment.generation}:join-worker:{worker.node_id}",
                )
            except Exception as e:  # noqa: BLE001
                log.warning("join_ray_cluster for node %s: %s", worker.node_id, e)


    async def _verify_cluster(self, head, node_control) -> RayClusterStatus:
        """Step 6: probe the head for authoritative cluster membership."""
        client = RayClusterClient(node_control)
        try:
            return await client.probe_cluster(head_node_id=head.node_id)
        except Exception as e:  # noqa: BLE001
            log.warning("Ray cluster verify on %s failed: %s", head.node_id, e)
            return RayClusterStatus(alive=False)


    async def _refresh_capabilities(self, environment, config) -> None:
        """Persist the static Ray capability snapshot (step 7)."""
        try:
            from llm_port_backend.services.inference.drivers.ray.driver import RayDriver

            doc = await RayDriver().capabilities(environment)
            raw = doc.raw or {}
            raw.setdefault("driver", "ray")
            raw.setdefault("backend_version", config.ray_version)
            environment.capabilities_json = raw
        except Exception as e:  # noqa: BLE001 - capabilities must not wedge the loop
            log.warning("Capability refresh for env %s failed: %s", environment.id, e)


    async def _teardown(self, environment, head, workers, node_control) -> None:
        """Issue stop (head) + leave (workers) for a STOPPED/DELETED env."""
        version = environment.ray_version or "2.58.0"
        if head is not None:
            try:
                await node_control.issue_command(
                    node_id=head.node_id,
                    command_type=NodeCommandType.STOP_RAY.value,
                    payload={"force": True, "version": version},
                    issued_by=None,
                    correlation_id=None,
                    timeout_sec=_LIFECYCLE_TIMEOUT_SEC,
                    idempotency_key=f"inference-env:{environment.id}:{environment.generation}:teardown-stop:{head.node_id}",
                )
            except Exception as e:  # noqa: BLE001
                log.warning("teardown stop_ray for node %s: %s", head.node_id, e)

        for worker in workers:
            try:
                await node_control.issue_command(
                    node_id=worker.node_id,
                    command_type=NodeCommandType.LEAVE_RAY_CLUSTER.value,
                    payload={"version": version},
                    issued_by=None,
                    correlation_id=None,
                    timeout_sec=_LIFECYCLE_TIMEOUT_SEC,
                    idempotency_key=f"inference-env:{environment.id}:{environment.generation}:teardown-leave:{worker.node_id}",
                )
            except Exception as e:  # noqa: BLE001
                log.warning("teardown leave_ray_cluster for node %s: %s", worker.node_id, e)


    def _observe(
        self,
        environment,
        status: EnvironmentStatus,
        cluster: RayClusterStatus,
        *,
        expected_nodes: int,
        address: str | None = None,
        version: str | None = None,
        reason: str | None = None,
    ) -> None:
        environment.status = status
        environment.observed_generation = environment.generation
        environment.observed_status_json = {
            "observation": {
                "status": status.value if hasattr(status, "value") else str(status),
                "reconciled": status is not EnvironmentStatus.PENDING,
                "reason": reason,
            },
            "cluster": cluster.model_dump(),
            "conditions": build_environment_conditions(cluster, expected_nodes),
        }
        if address:
            environment.address = address
        if version:
            environment.ray_version = version

