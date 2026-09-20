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
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from llm_port_backend.db.models.inference import (
    EnvironmentStatus,
    InferenceControlPlane,
    InferenceEnvironment,
    InferenceEnvironmentNode,
)
from llm_port_backend.db.models.node_control import (
    InfraNode,
    NodeCommandStatus,
    NodeCommandType,
)
from llm_port_backend.services.inference.drivers.ray.client import RayClusterClient
from llm_port_backend.services.inference.drivers.ray.commands import NodeCommandGateway
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


# Node-command lifetime (enforced by the reaper) for lifecycle commands, and
# how long one pass waits for each result.  The wait is shorter than the
# lifetime: an unobserved result is "outcome unknown", the environment stays
# pending, and the next pass resumes the same command via its idempotency key.
_LIFECYCLE_TIMEOUT_SEC = 300
_LIFECYCLE_WAIT_SEC = 150.0

_SUCCEEDED = NodeCommandStatus.SUCCEEDED.value

# One runtime container per node; the head/worker split is decided by which
# ``ray start`` the agent execs inside it.
_RUNTIME_CONTAINER_NAME = "llm-port-ray-runtime"


class _LifecycleFailed(RuntimeError):
    """A lifecycle command reached a terminal failure on the agent."""


class _LifecycleUnobserved(RuntimeError):
    """A lifecycle command did not reach a terminal state within the wait."""


def _gateway(node_control: "NodeControlService | NodeCommandGateway") -> NodeCommandGateway:
    """Normalise the reconcile context's node control into a gateway."""
    if isinstance(node_control, NodeCommandGateway):
        return node_control
    return NodeCommandGateway(node_control)


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
                map_cluster_to_environment_status(RayClusterStatus(alive=False), 0),
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

        gateway = _gateway(node_control)

        if environment.desired_state in ("stopped", "deleted"):
            try:
                await self._teardown(environment, head, workers, gateway)
            except (_LifecycleFailed, _LifecycleUnobserved) as exc:
                # Not confirmed: never claim STOPPED.  Stay unobserved so the
                # next pass retries/resumes the teardown commands.
                log.warning("Environment %s teardown not confirmed: %s", environment.id, exc)
                self._observe(
                    environment,
                    environment.status,
                    RayClusterStatus(alive=False),
                    expected_nodes=len(nodes),
                    reason=f"teardown not confirmed: {exc}",
                    mark_observed=False,
                )
                return
            self._observe(
                environment,
                EnvironmentStatus.STOPPED,
                RayClusterStatus(alive=False),
                expected_nodes=len(nodes),
                reason="teardown confirmed",
            )
            return

        # desired_state == running
        config = RayEnvironmentConfig.model_validate(environment.config_json or {})
        credential_ref = await self._ensure_cluster_token(session, environment)

        resolved_fabric = self._get_resolved_fabric(environment)
        node_bindings = (resolved_fabric or {}).get("node_bindings", {})
        head_binding = node_bindings.get(str(head.node_id))
        head_host = (head_binding or {}).get("ip") or await self._host_of(session, head.node_id)
        head_address = f"{head_host}:{config.head_port}" if head_host else config.dashboard_host

        try:
            # Refuse a re-bind before issuing anything: the agent tolerates an
            # already-running head, so a second apply against a new address
            # would be reported as started while the cluster stayed put.
            self._assert_not_rebinding(environment, head_host)
            await self._ensure_runtimes(environment, gateway=gateway, nodes=nodes, config=config)
            await self._start_head(session, environment, head, credential_ref, config, gateway)
            await self._join_workers(
                session, environment, workers, head_address, credential_ref, config, gateway
            )
        except _LifecycleUnobserved as exc:
            # Outcome unknown (agent slow/offline): not a failure.  PREPARING
            # keeps the env in the queue; the next pass resumes the commands.
            log.info("Environment %s lifecycle pending: %s", environment.id, exc)
            self._observe(
                environment,
                EnvironmentStatus.PREPARING,
                RayClusterStatus(alive=False),
                expected_nodes=len(nodes),
                address=head_address,
                version=config.ray_version,
                reason=str(exc),
            )
            return
        except _LifecycleFailed as exc:
            log.warning("Environment %s reconcile action failed: %s", environment.id, exc)
            self._observe(
                environment,
                EnvironmentStatus.FAILED,
                RayClusterStatus(alive=False),
                expected_nodes=len(nodes),
                address=head_address,
                version=config.ray_version,
                reason=str(exc),
            )
            return

        # 6. Verify cluster membership by probing the head.
        status = await self._verify_cluster(head, gateway)
        # 7. Refresh the environment capability snapshot.
        await self._refresh_capabilities(environment, config)
        # 8. Persist the observed state for the cluster we just reconciled.
        self._observe(
            environment,
            map_cluster_to_environment_status(status, len(nodes)),
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
        try:
            await session.commit()
        except Exception:
            await session.flush()
        return ref

    async def _issue(
        self, gateway: NodeCommandGateway, *, node_id, command_type: str, payload: dict[str, Any], key: str
    ) -> Any:
        return await gateway.issue(
            node_id=node_id,
            command_type=command_type,
            payload=payload,
            idempotency_key=key,
            timeout_sec=_LIFECYCLE_TIMEOUT_SEC,
        )

    async def _await_result(
        self, gateway: NodeCommandGateway, command: Any, *, what: str
    ) -> dict[str, Any]:
        """Wait for *command*'s terminal state; return its result or raise.

        Raises :class:`_LifecycleFailed` on a terminal failure (with the
        agent's error) and :class:`_LifecycleUnobserved` when the wait budget
        runs out before the agent reports.
        """
        final = await gateway.wait(command.id, budget_sec=_LIFECYCLE_WAIT_SEC)
        if final is None:
            raise _LifecycleUnobserved(f"{what}: no result within {_LIFECYCLE_WAIT_SEC:.0f}s")
        if final.status != _SUCCEEDED:
            detail = final.error_message or final.error_code or final.status
            raise _LifecycleFailed(f"{what} failed: {detail}")
        return dict(final.result_json or {})

    async def _ensure_runtimes(
        self,
        environment,
        *,
        gateway: NodeCommandGateway,
        nodes,
        config,
    ) -> None:
        """Step 2: ensure the Ray runtime & version on every member node.

        All commands are issued before any is awaited so the agents work in
        parallel; each result is then checked (a missing runtime fails the
        environment instead of being silently ignored).
        """
        bundle = self._bundle_of(environment)
        bundle_payload = self._bundle_payload(bundle)

        if bundle_payload is not None:
            # 2a. The exact OCI digest must be present on every member node
            # before anything tries to run out of it.  The agent verifies the
            # pinned identity locally and, when absent, side-loads it from the
            # backend — never from a public registry.
            image_cmds = []
            for node in nodes:
                image_cmds.append((node, await self._issue(
                    gateway,
                    node_id=node.node_id,
                    command_type=NodeCommandType.ENSURE_RUNTIME_IMAGE.value,
                    payload={"runtime_bundle": bundle_payload, "ensure_container": True},
                    key=f"inference-env:{environment.id}:{environment.generation}:ensure-image:{node.node_id}",
                )))
            for node, cmd in image_cmds:
                result = await self._await_result(
                    gateway, cmd, what=f"ensure_runtime_image on node {node.node_id}"
                )
                if not result.get("verified"):
                    raise _LifecycleFailed(
                        f"Runtime image {bundle.container.image} "
                        f"({bundle.container.digest}) not verified on node {node.node_id}: "
                        f"{result.get('error') or 'digest mismatch'}"
                    )

        issued = []
        for node in nodes:
            payload: dict[str, Any] = {"version": config.ray_version}
            if bundle_payload is not None:
                payload["runtime_bundle"] = bundle_payload
            cmd = await self._issue(
                gateway,
                node_id=node.node_id,
                command_type=NodeCommandType.ENSURE_RAY_RUNTIME.value,
                payload=payload,
                key=f"inference-env:{environment.id}:{environment.generation}:ensure-runtime:{node.node_id}",
            )
            issued.append((node, cmd))
        for node, cmd in issued:
            result = await self._await_result(
                gateway, cmd, what=f"ensure_ray_runtime on node {node.node_id}"
            )
            if result.get("installed") is False:
                where = "in the runtime container" if bundle_payload is not None else "on the host"
                raise _LifecycleFailed(
                    f"Ray runtime {config.ray_version} not installed {where} "
                    f"on node {node.node_id}: {result.get('error') or 'not found'}"
                )

    @staticmethod
    def _bundle_of(environment) -> Any:
        """The runtime bundle pinned on the environment, or ``None``."""
        bundle_id = (environment.config_json or {}).get("runtime_bundle_id")
        if not bundle_id:
            return None
        from llm_port_backend.services.inference.bundles import default_bundle_registry

        bundle = default_bundle_registry.get_bundle(str(bundle_id))
        if bundle is None:
            log.warning(
                "Environment %s pins unknown runtime bundle %r; falling back to the host runtime",
                environment.id, bundle_id,
            )
        return bundle

    @classmethod
    def _bundle_env(cls, environment, bundle, env_vars: dict[str, str]) -> dict[str, str]:
        """Merge the bundle's certified platform tuning into ``env_vars``."""
        if bundle is None:
            return env_vars
        from llm_port_backend.services.inference.bundles import default_bundle_registry

        # Diagnostics (verbose NCCL tracing and friends) stay opt-in per
        # environment; they are not a runtime default.
        diagnostics = bool(
            (environment.config_json or {}).get("runtime_diagnostics", False)
        )
        return default_bundle_registry.inject_platform_tuning(
            bundle, env_vars=env_vars, diagnostics=diagnostics,
        )

    @classmethod
    def _bundle_payload(cls, bundle, env_vars: dict[str, str] | None = None) -> dict[str, Any] | None:
        """Render the bundle into the agent's container launch contract."""
        if bundle is None:
            return None
        from llm_port_backend.services.inference.bundles import default_bundle_registry

        return default_bundle_registry.container_launch_spec(
            bundle, name=_RUNTIME_CONTAINER_NAME, env=env_vars or {},
        )

    @staticmethod
    def _host_part(address: str | None) -> str | None:
        """Host of an ``ip[:port]`` address, or ``None``."""
        if not address:
            return None
        text = str(address).strip()
        if not text:
            return None
        # IPv6 literals arrive bracketed ("[::1]:6379"); anything else splits
        # on the last colon only when that colon introduces a port.
        if text.startswith("["):
            return text.split("]")[0].lstrip("[") or None
        head, sep, tail = text.rpartition(":")
        if sep and tail.isdigit():
            return head or None
        return text

    def _assert_not_rebinding(self, environment, requested_host: str | None) -> None:
        """Refuse to re-bind a live head to a different address.

        ``ray start --head`` is issued with ``tolerate_already_running``: on a
        head that is already up the CLI's "already running" exit is swallowed
        and the agent reports the *requested* address as started, so the
        control plane would record the new fabric while the cluster kept
        running on the old one.  A re-bind therefore has to go through a stop.
        """
        if not requested_host:
            return
        cluster = (environment.observed_status_json or {}).get("cluster") or {}
        if not cluster.get("alive"):
            return
        observed_host = self._host_part(cluster.get("head_address")) or self._host_part(
            cluster.get("cluster_address")
        )
        if observed_host and observed_host != requested_host:
            raise _LifecycleFailed(
                f"environment must be stopped to re-bind: head is live on {observed_host}, "
                f"requested {requested_host}"
            )

    @staticmethod
    def _get_resolved_fabric(environment) -> dict[str, Any] | None:
        """Read resolved fabric from observed_status_json, with fallback to config_json."""
        return (
            (environment.observed_status_json or {}).get("resolved_fabric")
            or (environment.config_json or {}).get("resolved_fabric")
        )

    async def _start_head(
        self, session, environment, head, credential_ref, config, gateway: NodeCommandGateway
    ) -> None:
        """Step 4: start the designated head node (idempotent on the agent)."""
        resolved_fabric = self._get_resolved_fabric(environment)
        node_bindings = (resolved_fabric or {}).get("node_bindings", {})
        head_binding = node_bindings.get(str(head.node_id))

        head_host = (head_binding or {}).get("ip") or await self._host_of(session, head.node_id)
        payload: dict[str, Any] = {
            "credential_ref": credential_ref,
            "version": config.ray_version,
            "port": config.head_port,
            "dashboard_port": config.dashboard_port,
            "dashboard_host": config.dashboard_host,
            "node_ip_address": head_host,
        }
        env_vars = dict(config.node_env_vars or {})
        if head_binding:
            if head_binding.get("interface"):
                env_vars.setdefault("NCCL_SOCKET_IFNAME", str(head_binding["interface"]))
            if head_binding.get("ip"):
                env_vars.setdefault("VLLM_HOST_IP", str(head_binding["ip"]))
            if head_binding.get("rdma_device"):
                env_vars.setdefault("NCCL_IB_HCA", str(head_binding["rdma_device"]))
                env_vars.setdefault("UCX_NET_DEVICES", f"{head_binding['rdma_device']}:1")

        bundle = self._bundle_of(environment)
        env_vars = self._bundle_env(environment, bundle, env_vars)
        bundle_payload = self._bundle_payload(bundle)
        if bundle_payload is not None:
            payload["runtime_bundle"] = bundle_payload

        if env_vars:
            payload["env"] = env_vars

        cmd = await self._issue(
            gateway,
            node_id=head.node_id,
            command_type=NodeCommandType.START_RAY_HEAD.value,
            payload=payload,
            key=f"inference-env:{environment.id}:{environment.generation}:start-head",
        )
        await self._await_result(gateway, cmd, what=f"start_ray_head on node {head.node_id}")

    async def _join_workers(
        self,
        session,
        environment,
        workers,
        head_address,
        credential_ref,
        config,
        gateway: NodeCommandGateway,
    ) -> None:
        """Step 5: join every worker (issued together, then awaited)."""
        resolved_fabric = self._get_resolved_fabric(environment)
        node_bindings = (resolved_fabric or {}).get("node_bindings", {})
        bundle = self._bundle_of(environment)
        bundle_payload = self._bundle_payload(bundle)

        issued = []
        for worker in workers:
            worker_binding = node_bindings.get(str(worker.node_id))
            host = (worker_binding or {}).get("ip") or await self._host_of(session, worker.node_id)
            payload: dict[str, Any] = {
                "head_address": head_address,
                "credential_ref": credential_ref,
                "version": config.ray_version,
                "node_ip_address": host,
            }
            env_vars = dict(config.node_env_vars or {})
            if worker_binding:
                if worker_binding.get("interface"):
                    env_vars.setdefault("NCCL_SOCKET_IFNAME", str(worker_binding["interface"]))
                if worker_binding.get("ip"):
                    env_vars.setdefault("VLLM_HOST_IP", str(worker_binding["ip"]))
                if worker_binding.get("rdma_device"):
                    env_vars.setdefault("NCCL_IB_HCA", str(worker_binding["rdma_device"]))
                    env_vars.setdefault("UCX_NET_DEVICES", f"{worker_binding['rdma_device']}:1")

            env_vars = self._bundle_env(environment, bundle, env_vars)
            if bundle_payload is not None:
                payload["runtime_bundle"] = bundle_payload

            if env_vars:
                payload["env"] = env_vars

            cmd = await self._issue(
                gateway,
                node_id=worker.node_id,
                command_type=NodeCommandType.JOIN_RAY_CLUSTER.value,
                payload=payload,
                key=f"inference-env:{environment.id}:{environment.generation}:join-worker:{worker.node_id}",
            )
            issued.append((worker, cmd))
        for worker, cmd in issued:
            await self._await_result(gateway, cmd, what=f"join_ray_cluster on node {worker.node_id}")

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

    async def _teardown(self, environment, head, workers, gateway: NodeCommandGateway) -> None:
        """Workers leave, then the head stops; each result is confirmed (F23).

        Raises :class:`_LifecycleFailed` / :class:`_LifecycleUnobserved` so
        the caller never records STOPPED for a teardown it did not observe.
        Both agent commands succeed when nothing is running, so a retry after
        a partial teardown is safe.
        """
        version = environment.ray_version or "2.58.0"
        bundle_payload = self._bundle_payload(self._bundle_of(environment))
        leave_payload: dict[str, Any] = {"version": version}
        stop_payload: dict[str, Any] = {"force": True, "version": version}
        if bundle_payload is not None:
            # Without this the agent would stop a host Ray that was never
            # started and leave the runtime container running.
            leave_payload["runtime_bundle"] = bundle_payload
            stop_payload["runtime_bundle"] = bundle_payload

        issued = []
        for worker in workers:
            cmd = await self._issue(
                gateway,
                node_id=worker.node_id,
                command_type=NodeCommandType.LEAVE_RAY_CLUSTER.value,
                payload=leave_payload,
                key=f"inference-env:{environment.id}:{environment.generation}:teardown-leave:{worker.node_id}",
            )
            issued.append((worker, cmd))
        for worker, cmd in issued:
            await self._await_result(gateway, cmd, what=f"leave_ray_cluster on node {worker.node_id}")

        # Head stops last.
        if head is not None:
            cmd = await self._issue(
                gateway,
                node_id=head.node_id,
                command_type=NodeCommandType.STOP_RAY.value,
                payload=stop_payload,
                key=f"inference-env:{environment.id}:{environment.generation}:teardown-stop:{head.node_id}",
            )
            await self._await_result(gateway, cmd, what=f"stop_ray on node {head.node_id}")

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
        mark_observed: bool = True,
    ) -> None:
        environment.status = status
        if mark_observed:
            environment.observed_generation = environment.generation
        # Merge, never replace: the observer owns ``observation`` / ``cluster``
        # / ``conditions``, but ``resolved_fabric`` and ``network`` are written
        # by the planner's apply-plan (Amendment 1) and must survive the
        # reconcile pass that apply-plan itself schedules.
        observed = dict(environment.observed_status_json or {})
        observed.update({
            "observation": {
                "status": status.value if hasattr(status, "value") else str(status),
                "reconciled": status is not EnvironmentStatus.PENDING,
                "reason": reason,
            },
            "cluster": cluster.model_dump(),
            "conditions": build_environment_conditions(cluster, expected_nodes),
        })
        environment.observed_status_json = observed
        if address:
            environment.address = address
        if version:
            environment.ray_version = version

