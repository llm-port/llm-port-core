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

Once a cluster has come up at its current generation, later passes are health
checks instead (:meth:`RayEnvironmentManager._check`): one probe, and a
recovery -- head re-formed, lost workers rejoined -- only when two looks agree
that something is gone.

The manager mutates the session (command rows + observed state) but does not
commit; the owning loop commits after a pass.  Remote actions are never wrapped
in an additional DB transaction around the network calls themselves.
"""

from __future__ import annotations

import logging
import time
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
    NodeHealthStatus,
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

#: Port a member serves the runtime image to its peers on, over the fabric.
#: Outside Ray's default worker-port range (10002-19999) and clear of its
#: fixed ports, and only open while a transfer is under way.
_IMAGE_SEED_PORT = 8271
#: How long a machine may be offline before its cluster is told so, just after
#: this backend started: a restart disconnects every agent at once, and they
#: are back well inside this.
_OFFLINE_GRACE_SEC = 120
#: The same, once the backend has been up a while. A machine dropping then has
#: dropped on its own -- a reboot, a crash -- and the long grace hid it: the
#: DGX head rebooted and its cluster read "ready" for two and a half minutes.
#: This only covers an agent restarting, which is back in seconds.
_SINGLE_DROP_GRACE_SEC = 30
#: When this process started (monotonic), to tell the two apart.
_PROCESS_STARTED = time.monotonic()

#: How long a seeding member keeps serving before giving up on its peers.
_IMAGE_SEED_TIMEOUT_SEC = 3600

#: Probe answers meaning "not here yet" rather than "something is broken".
_IMAGE_ABSENT = frozenset({"runtime_image_missing", "runtime_image_mismatch"})

# One runtime container per node; the head/worker split is decided by which
# ``ray start`` the agent execs inside it.
_RUNTIME_CONTAINER_NAME = "llm-port-ray-runtime"


#: Failures that come back identically however often they are retried, for
#: as long as the inputs stay as they are. A pinned image that does not match
#: is still not matching on the ninetieth attempt.
_PERMANENT_FAILURES = frozenset({
    "runtime_image_mismatch",
    "server_image_mismatch",
    "no_certified_runtime",
})

#: Retry spacing for a cluster that keeps failing: doubling from a minute.
_RETRY_BASE_SEC = 60
#: Transient failures are tried again at least this often.
_RETRY_CAP_SEC = 30 * 60
#: Permanent ones are re-checked this often in case something outside the
#: fingerprint changed (the server's own copy of the image, say), and at once
#: when anything inside it does.
_PERMANENT_RECHECK_SEC = 60 * 60

#: Restarts LLM.Port makes on its own before it declares a cluster failed.
_MAX_RECOVERY_ATTEMPTS = 3
#: How long a failed health check waits for a second look before anything is
#: restarted. One probe that timed out on a busy head is not a dead cluster,
#: and restarting a serving cluster on its word would cause the very outage
#: the check is there to catch.
_CONFIRM_AFTER_SEC = 20
#: Spacing between restart attempts: this, doubled after each one.
_RECOVERY_BACKOFF_SEC = 30
#: Once the attempts are used up, how often it is still looked at -- and
#: restarted once more if still down. What stopped it may have been fixed
#: without anyone telling LLM.Port: a network link back, a machine repaired.
_GAVE_UP_RECHECK_SEC = 15 * 60
#: After a restart, how long the cluster has to show every member before the
#: attempt counts as not having worked, and how often it is looked at.
_SETTLE_SEC = 30.0
_SETTLE_POLL_SEC = 5.0


def _when(value: Any) -> Any:
    """An ISO timestamp from the observed state, or ``None``."""
    from datetime import datetime  # noqa: PLC0415

    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


class _LifecycleFailed(RuntimeError):
    """A lifecycle command reached a terminal failure on the agent.

    ``code`` is the agent's error code when there is one; ``detail`` is its
    message on its own, without the command-and-node prefix, which is the
    part worth showing an operator.
    """

    def __init__(self, message: str, *, code: str | None = None, detail: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.detail = detail or message

    @property
    def permanent(self) -> bool:
        return self.code in _PERMANENT_FAILURES


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
                await self._teardown(session, environment, head, workers, gateway)
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

        # A member with no agent connected cannot take a command, and asking
        # anyway reads as "the agent is slow" for as long as it stays away.
        # Say which machine is gone instead, and wait: it is checked again
        # the moment it reconnects.
        gone, reconnecting = await self._offline_members(session, nodes)
        if reconnecting and not gone:
            # Dropped moments ago -- a backend restart drops every agent at
            # once, and they are back within the minute. Declaring the cluster
            # failed meanwhile showed a healthy cluster as failed after every
            # restart. Leave it as it is and look again on the next pass.
            log.info(
                "Environment %s: %s reconnecting; checking again next pass",
                environment.id, ", ".join(sorted(reconnecting.values())),
            )
            return
        if gone:
            offline = {**gone, **reconnecting}
            names = ", ".join(sorted(offline.values()))
            verb = "is" if len(offline) == 1 else "are"
            head_gone = head.node_id in offline
            self._observe(
                environment,
                EnvironmentStatus.FAILED if head_gone else EnvironmentStatus.DEGRADED,
                # With only a worker away the head can still be asked, and the
                # models on it still report their surviving copies from the
                # last snapshot; with the head away nothing is known.
                RayClusterStatus(alive=False, observed=False) if head_gone else None,
                expected_nodes=len(nodes),
                reason=(
                    f"{names} {verb} offline: no agent connected. "
                    "The cluster is checked again when it reconnects."
                ),
            )
            return

        if self._converged(environment):
            # Nothing has been asked of this cluster since it last came up, so
            # this pass is a health check: the periodic one, or a machine
            # reconnecting. Look before touching anything, and act only on
            # what the look finds.
            await self._check(
                session,
                environment,
                head=head,
                workers=workers,
                nodes=nodes,
                gateway=gateway,
                config=config,
                credential_ref=credential_ref,
                head_address=head_address,
            )
            return

        fingerprint = await self._inputs_fingerprint(session, environment, nodes)
        waiting = self._held_back(environment, fingerprint)
        if waiting is not None:
            # Nothing has changed since the last failure and the backoff has
            # not run out: asking again would get the same answer. The status
            # and message the failure left stay as they are.
            log.info("Environment %s held back: %s", environment.id, waiting)
            return

        try:
            # Refuse a re-bind before issuing anything: the agent tolerates an
            # already-running head, so a second apply against a new address
            # would be reported as started while the cluster stayed put.
            self._assert_not_rebinding(environment, head_host)
            await self._ensure_runtimes(
                session, environment, gateway=gateway, nodes=nodes, config=config
            )
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
            message = self._record_failure(environment, exc, fingerprint)
            self._observe(
                environment,
                EnvironmentStatus.FAILED,
                RayClusterStatus(alive=False),
                expected_nodes=len(nodes),
                address=head_address,
                version=config.ray_version,
                reason=message,
            )
            return

        # Got through: whatever was holding this cluster back no longer is.
        self._clear_failure(environment)

        # 6. Verify cluster membership by probing the head.
        status = await self._verify_cluster(session, head, gateway)
        # 7. Refresh the environment capability snapshot.
        await self._refresh_capabilities(environment, config)
        # 8. Persist the observed state for the cluster we just reconciled.
        observed_status = map_cluster_to_environment_status(status, len(nodes))
        if observed_status is EnvironmentStatus.READY:
            # Up as asked. From here until the next change, passes over this
            # cluster are health checks rather than another start sequence.
            observed = dict(environment.observed_status_json or {})
            observed["converged_generation"] = environment.generation
            observed.pop("recovery", None)
            environment.observed_status_json = observed
        self._observe(
            environment,
            observed_status,
            status,
            expected_nodes=len(nodes),
            address=status.cluster_address or head_address,
            version=status.version or config.ray_version,
        )

    # ------------------------------------------------------------------
    # Health check and recovery of a cluster that was up
    # ------------------------------------------------------------------

    @staticmethod
    def _converged(environment) -> bool:
        """Whether the cluster came up at its current generation."""
        observed = environment.observed_status_json or {}
        return observed.get("converged_generation") == environment.generation

    async def _check(
        self,
        session,
        environment,
        *,
        head,
        workers,
        nodes,
        gateway: NodeCommandGateway,
        config,
        credential_ref,
        head_address,
    ) -> None:
        """Look at a cluster that was up, and bring it back if it is not.

        A healthy cluster was never looked at again once it came up, so a Ray
        head that died left the cluster reading "ready" and its models
        "running" for as long as nobody changed anything -- while every
        request failed. Now the reconciler looks every minute (and whenever a
        member reconnects), and when the look finds a problem:

        1. it looks again after ``_CONFIRM_AFTER_SEC`` before restarting
           anything, and says it is doing so;
        2. a lost head is re-formed: everything stopped, the head started
           fresh, every worker joined to it. A lost worker is rejoined;
        3. the cluster is given ``_SETTLE_SEC`` to show every member;
        4. up to ``_MAX_RECOVERY_ATTEMPTS`` attempts, spaced out, and then an
           explicit failure that says what was tried. After that it is only
           looked at every ``_GAVE_UP_RECHECK_SEC``; Try again starts over.

        The models are re-applied by the deployment reconciler, which sees the
        cluster's head change.
        """
        from datetime import UTC, datetime, timedelta  # noqa: PLC0415

        now = datetime.now(tz=UTC)
        recovery = dict((environment.observed_status_json or {}).get("recovery") or {})
        due = _when(recovery.get("next_look_at"))
        if due is not None and now < due:
            if recovery.get("gave_up"):
                environment.observed_generation = environment.generation
            return

        status = await self._verify_cluster(session, head, gateway)
        if not status.observed:
            # No answer is not an answer: nothing is restarted on it.
            log.info("Environment %s: health check got no answer from the head's agent", environment.id)
            environment.observed_generation = environment.generation
            return

        members = await self._member_rows(session, nodes)
        problem = self._diagnose(environment, status, head, nodes, members)
        if problem is None:
            self._finish_recovery(environment, recovery, now)
            self._observe(
                environment,
                map_cluster_to_environment_status(status, len(nodes)),
                status,
                expected_nodes=len(nodes),
                address=status.cluster_address or head_address,
                version=status.version or config.ray_version,
            )
            return

        kind, lost = problem
        what = self._describe(kind, lost, members)
        if not recovery:
            self._set_recovery(environment, {
                "kind": kind,
                "detail": what,
                "attempt": 0,
                "suspected_at": now.isoformat(),
                "next_look_at": (now + timedelta(seconds=_CONFIRM_AFTER_SEC)).isoformat(),
            })
            # The cluster snapshot stays as it was: one look is not enough to
            # tell the models' users that they are down.
            self._observe(
                environment,
                EnvironmentStatus.DEGRADED,
                None,
                expected_nodes=len(nodes),
                reason=f"{what}. Checking again before restarting anything.",
            )
            return

        attempt = int(recovery.get("attempt") or 0) + 1
        recovery.update({"kind": kind, "detail": what, "attempt": attempt})
        recovery.setdefault("suspected_at", now.isoformat())
        recovery.pop("next_look_at", None)
        self._set_recovery(environment, recovery)
        self._observe(
            environment,
            EnvironmentStatus.DEGRADED,
            status,
            expected_nodes=len(nodes),
            reason=self._restarting(kind, what, attempt),
        )
        # A restart takes minutes and the pass commits only at its end; say
        # what is happening now rather than after it is over.
        await self._commit_progress(session)

        error: str | None = None
        try:
            if kind == "head":
                await self._reform(
                    session, environment, head, workers, gateway=gateway, config=config,
                    credential_ref=credential_ref, head_address=head_address, attempt=attempt,
                )
            else:
                await self._rejoin(
                    session, environment, lost, gateway=gateway, config=config,
                    credential_ref=credential_ref, head_address=head_address, attempt=attempt,
                )
        except (_LifecycleFailed, _LifecycleUnobserved) as exc:
            error = getattr(exc, "detail", None) or str(exc)

        if error is None:
            status = await self._settle(session, environment, head, nodes, members, gateway)
            if status.observed and self._diagnose(environment, status, head, nodes, members) is None:
                self._finish_recovery(environment, recovery, datetime.now(tz=UTC))
                self._observe(
                    environment,
                    map_cluster_to_environment_status(status, len(nodes)),
                    status,
                    expected_nodes=len(nodes),
                    address=status.cluster_address or head_address,
                    version=status.version or config.ray_version,
                )
                return
            error = "it was restarted but did not come back"

        recovery["last_error"] = error
        if attempt >= _MAX_RECOVERY_ATTEMPTS:
            recovery["gave_up"] = True
            recovery["next_look_at"] = (
                datetime.now(tz=UTC) + timedelta(seconds=_GAVE_UP_RECHECK_SEC)
            ).isoformat()
            self._set_recovery(environment, recovery)
            self._observe(
                environment,
                EnvironmentStatus.FAILED,
                status,
                expected_nodes=len(nodes),
                reason=(
                    f"{what}. LLM.Port restarted it {attempt} times and it did not "
                    f"come back (last: {error}). It tries again every "
                    f"{_GAVE_UP_RECHECK_SEC // 60} minutes; use Try again once the "
                    f"cause is fixed."
                ),
            )
            return
        wait = _RECOVERY_BACKOFF_SEC * 2 ** (attempt - 1)
        recovery["next_look_at"] = (datetime.now(tz=UTC) + timedelta(seconds=wait)).isoformat()
        self._set_recovery(environment, recovery)
        self._observe(
            environment,
            EnvironmentStatus.DEGRADED,
            status,
            expected_nodes=len(nodes),
            reason=(
                f"{what}. Restart {attempt} of {_MAX_RECOVERY_ATTEMPTS} did not bring it "
                f"back ({error}); trying again in {wait} s."
            ),
        )

    def _diagnose(self, environment, status: RayClusterStatus, head, nodes, members):
        """What is wrong with a cluster that answered, or ``None``.

        ``("head", [head])`` when the head is gone -- Ray not answering at
        all, or the head machine missing from its own cluster -- and
        ``("members", [...])`` for workers the cluster no longer lists.
        """
        if not status.alive:
            return "head", [head]
        missing = self._missing_members(environment, status, nodes, members)
        if not missing:
            return None
        if any(node.node_id == head.node_id for node in missing):
            return "head", [head]
        return "members", missing

    def _missing_members(self, environment, status: RayClusterStatus, nodes, members) -> list:
        """Members with no live record in the cluster.

        Ray names a node by the address it was started on -- the fabric link
        here, not the address LLM.Port knows the machine by -- so both are
        tried. An agent too old to list nodes gives only a count, which
        cannot say who is missing: nothing is restarted on it.
        """
        if not status.nodes:
            return []
        alive: set[str] = set()
        for record in status.alive_node_records:
            for key in ("node_ip", "node_manager_address", "node_name"):
                if record.get(key):
                    alive.add(str(record[key]).strip())
        bindings = (self._get_resolved_fabric(environment) or {}).get("node_bindings") or {}
        missing = []
        for node in nodes:
            row = members.get(node.node_id)
            addresses = {
                (bindings.get(str(node.node_id)) or {}).get("ip"),
                self._clean_host(getattr(row, "host", None)),
            }
            if not any(a and str(a).strip() in alive for a in addresses):
                missing.append(node)
        return missing

    @staticmethod
    def _clean_host(host: Any) -> str | None:
        text = str(host or "").strip()
        if text.startswith("host="):
            text = text[len("host="):]
        return text or None

    @staticmethod
    async def _member_rows(session, nodes) -> dict:
        ids = [n.node_id for n in nodes]
        if not ids:
            return {}
        rows = await session.execute(select(InfraNode).where(InfraNode.id.in_(ids)))
        return {row.id: row for row in rows.scalars()}

    @staticmethod
    def _describe(kind: str, lost, members) -> str:
        def name(node) -> str:
            row = members.get(node.node_id)
            return (getattr(row, "agent_id", None) or getattr(row, "host", None) or str(node.node_id))

        if kind == "head":
            return f"Ray on {name(lost[0])}, the cluster's head, is not running"
        names = ", ".join(sorted(name(n) for n in lost))
        verb = "has" if len(lost) == 1 else "have"
        return f"{names} {verb} dropped out of the cluster"

    @staticmethod
    def _restarting(kind: str, what: str, attempt: int) -> str:
        of = (
            f"attempt {attempt} of {_MAX_RECOVERY_ATTEMPTS}"
            if attempt <= _MAX_RECOVERY_ATTEMPTS
            else f"attempt {attempt}"
        )
        if kind == "head":
            return (
                f"{what}. Restarting the cluster ({of}); models on it are down "
                f"until it is back."
            )
        return f"{what}. Rejoining ({of})."

    @staticmethod
    def _set_recovery(environment, recovery: dict[str, Any]) -> None:
        observed = dict(environment.observed_status_json or {})
        observed["recovery"] = recovery
        environment.observed_status_json = observed

    @staticmethod
    def _finish_recovery(environment, recovery: dict[str, Any], now) -> None:
        """Clear a recovery that worked; keep a record of it if one ran."""
        if not recovery:
            return
        observed = dict(environment.observed_status_json or {})
        observed.pop("recovery", None)
        if int(recovery.get("attempt") or 0) > 0:
            observed["last_recovery"] = {
                "kind": recovery.get("kind"),
                "detail": recovery.get("detail"),
                "noticed_at": recovery.get("suspected_at"),
                "recovered_at": now.isoformat(),
                "attempts": recovery.get("attempt"),
            }
            log.info(
                "Environment %s recovered after %s attempt(s): %s",
                environment.id, recovery.get("attempt"), recovery.get("detail"),
            )
        else:
            log.info("Environment %s: second look found it healthy (%s)", environment.id, recovery.get("detail"))
        environment.observed_status_json = observed

    @staticmethod
    async def _commit_progress(session) -> None:
        try:
            await session.commit()
        except Exception:  # noqa: BLE001 - progress text is not worth failing a recovery over
            log.debug("Could not commit recovery progress", exc_info=True)

    async def _settle(self, session, environment, head, nodes, members, gateway) -> RayClusterStatus:
        """Look until every member shows up or ``_SETTLE_SEC`` runs out."""
        import asyncio  # noqa: PLC0415

        loop = asyncio.get_event_loop()
        deadline = loop.time() + _SETTLE_SEC
        while True:
            status = await self._verify_cluster(session, head, gateway)
            healthy = status.observed and self._diagnose(environment, status, head, nodes, members) is None
            if healthy or loop.time() >= deadline:
                return status
            await asyncio.sleep(_SETTLE_POLL_SEC)

    async def _reform(
        self, session, environment, head, workers, *, gateway, config, credential_ref, head_address, attempt,
    ) -> None:
        """Re-form a cluster whose head is gone: stop everything, start it again.

        Stopping first is the point. The agent's start is idempotent against
        a node that already heads the cluster, which is right for a start but
        wrong here: a head whose GCS died can keep its raylet for a minute,
        and a worker keeps pointing at the old head for as long, so both
        would be reported as fine and then die. Stopping removes the runtime
        container, so the start that follows is from clean.
        """
        stops = []
        for worker in workers:
            stops.append((worker, await self._issue(
                gateway,
                node_id=worker.node_id,
                command_type=NodeCommandType.LEAVE_RAY_CLUSTER.value,
                payload=await self._stop_payload(session, environment, worker.node_id),
                key=self._recover_key(environment, attempt, "leave", worker.node_id),
            )))
        stops.append((head, await self._issue(
            gateway,
            node_id=head.node_id,
            command_type=NodeCommandType.STOP_RAY.value,
            payload={**await self._stop_payload(session, environment, head.node_id), "force": True},
            key=self._recover_key(environment, attempt, "stop", head.node_id),
        )))
        for node, cmd in stops:
            await self._await_result(gateway, cmd, what=f"stopping Ray on node {node.node_id}")

        await self._start_head(session, environment, head, credential_ref, config, gateway, attempt=attempt)
        await self._join_workers(
            session, environment, workers, head_address, credential_ref, config, gateway, attempt=attempt,
        )

    async def _rejoin(
        self, session, environment, lost, *, gateway, config, credential_ref, head_address, attempt,
    ) -> None:
        """Take lost workers out and join them again, from clean."""
        leaves = []
        for worker in lost:
            leaves.append((worker, await self._issue(
                gateway,
                node_id=worker.node_id,
                command_type=NodeCommandType.LEAVE_RAY_CLUSTER.value,
                payload=await self._stop_payload(session, environment, worker.node_id),
                key=self._recover_key(environment, attempt, "leave", worker.node_id),
            )))
        for node, cmd in leaves:
            await self._await_result(gateway, cmd, what=f"stopping Ray on node {node.node_id}")
        await self._join_workers(
            session, environment, lost, head_address, credential_ref, config, gateway, attempt=attempt,
        )

    async def _stop_payload(self, session, environment, node_id) -> dict[str, Any]:
        payload: dict[str, Any] = {"version": environment.runtime_version or "2.58.0"}
        bundle, row = await self._bundle_of(session, node_id)
        bundle_payload = self._bundle_payload(bundle, node=row)
        if bundle_payload is not None:
            payload["runtime_bundle"] = bundle_payload
        return payload

    @staticmethod
    def _recover_key(environment, attempt: int, step: str, node_id: Any) -> str:
        return f"inference-env:{environment.id}:{environment.generation}:recover{attempt}:{step}:{node_id}"



    # ------------------------------------------------------------------
    # Runtime image: from the server once, then machine to machine
    # ------------------------------------------------------------------

    async def _ensure_images(self, environment, *, gateway, nodes, bundles, payloads) -> None:
        """Make the pinned runtime image present on every member.

        Every member used to pull it from this server at once. On the DGX
        pair that was two 12 GB transfers sharing a 1 Gb/s management link,
        while the machines sat next to each other on a 200 Gb/s fabric. Now:

        1. ask every member whether it has the image, fetching nothing;
        2. if none does, one fetches it from the server;
        3. members that still lack it get it from a peer holding the same
           build, over the fabric address the plan bound them to;
        4. any member a peer could not serve falls back to the server.

        Every step is keyed on the generation, so a pass that runs out of
        time waiting resumes the same commands on the next pass.
        """
        gen = environment.generation

        def key(step: str, node_id: Any) -> str:
            return f"inference-env:{environment.id}:{gen}:{step}:{node_id}"

        def body(node, **extra: Any) -> dict[str, Any]:
            return {"runtime_bundle": payloads[node.node_id], "ensure_container": True, **extra}

        # 1. Who already has it.
        probes = []
        for node in nodes:
            probes.append((node, await self._issue(
                gateway,
                node_id=node.node_id,
                command_type=NodeCommandType.ENSURE_RUNTIME_IMAGE.value,
                payload=body(node, fetch=False),
                key=key("image-probe", node.node_id),
            )))
        have, need = [], []
        for node, cmd in probes:
            final = await self._await_final(gateway, cmd, what=f"image check on node {node.node_id}")
            if final.status == _SUCCEEDED and (final.result_json or {}).get("verified"):
                have.append(node)
            elif final.error_code in _IMAGE_ABSENT:
                need.append(node)
            else:
                detail = final.error_message or final.error_code or final.status
                raise _LifecycleFailed(
                    f"image check on node {node.node_id} failed: {detail}",
                    code=final.error_code,
                    detail=str(detail),
                )
        if not need:
            return

        bindings = (self._get_resolved_fabric(environment) or {}).get("node_bindings", {}) or {}

        def fabric_ip(node) -> str | None:
            return (bindings.get(str(node.node_id)) or {}).get("ip")

        def build_of(node) -> str | None:
            bundle = bundles[node.node_id][0]
            return getattr(getattr(bundle, "container", None), "digest", None)

        # 2. Nobody has it: one member fetches it from the server first.
        if not have:
            first = need.pop(0)
            await self._image_from_server(gateway, first, body(first), key("image-from-server", first.node_id))
            have.append(first)
        if not need:
            return

        # 3. Pair each member that lacks it with a peer holding the same build.
        seeders = [node for node in have if fabric_ip(node)]
        plan: dict[Any, list] = {}
        direct = []
        for node in need:
            peer = next(
                (s for s in seeders if build_of(s) == build_of(node) and fabric_ip(node)),
                None,
            )
            if peer is None:
                direct.append(node)
            else:
                plan.setdefault(peer.node_id, [peer, []])[1].append(node)

        fetching = []
        for peer, receivers in plan.values():
            token = self._seed_token(environment, peer.node_id)
            await self._issue(
                gateway,
                node_id=peer.node_id,
                command_type=NodeCommandType.SERVE_RUNTIME_IMAGE.value,
                payload={
                    "runtime_bundle": payloads[peer.node_id],
                    "bind_ip": fabric_ip(peer),
                    "port": _IMAGE_SEED_PORT,
                    "token": token,
                    "expected_peers": len(receivers),
                    "timeout_sec": _IMAGE_SEED_TIMEOUT_SEC,
                },
                key=key("image-seed", peer.node_id),
            )
            for node in receivers:
                fetching.append((node, await self._issue(
                    gateway,
                    node_id=node.node_id,
                    command_type=NodeCommandType.ENSURE_RUNTIME_IMAGE.value,
                    payload=body(node, source={
                        "peer_url": f"http://{fabric_ip(peer)}:{_IMAGE_SEED_PORT}/image",
                        "token": token,
                    }),
                    key=key("image-from-peer", node.node_id),
                )))

        # Members with no suitable peer go to the server, in parallel.
        server_cmds = []
        for node in direct:
            server_cmds.append((node, await self._issue(
                gateway,
                node_id=node.node_id,
                command_type=NodeCommandType.ENSURE_RUNTIME_IMAGE.value,
                payload=body(node),
                key=key("image-from-server", node.node_id),
            )))

        for node, cmd in fetching:
            final = await self._await_final(gateway, cmd, what=f"image from peer on node {node.node_id}")
            if final.status == _SUCCEEDED and (final.result_json or {}).get("verified"):
                continue
            # 4. A peer that could not serve is not the end of it.
            log.warning(
                "Node %s could not get the runtime image from its peer (%s); using the server",
                node.node_id, final.error_message or final.error_code,
            )
            server_cmds.append((node, await self._issue(
                gateway,
                node_id=node.node_id,
                command_type=NodeCommandType.ENSURE_RUNTIME_IMAGE.value,
                payload=body(node),
                key=key("image-from-server", node.node_id),
            )))

        for node, cmd in server_cmds:
            self._require_verified(
                await self._await_result(gateway, cmd, what=f"ensure_runtime_image on node {node.node_id}"),
                node,
                bundles,
            )

    async def _image_from_server(self, gateway, node, payload, idem_key) -> None:
        cmd = await self._issue(
            gateway,
            node_id=node.node_id,
            command_type=NodeCommandType.ENSURE_RUNTIME_IMAGE.value,
            payload=payload,
            key=idem_key,
        )
        result = await self._await_result(gateway, cmd, what=f"ensure_runtime_image on node {node.node_id}")
        if not result.get("verified"):
            raise _LifecycleFailed(
                f"Runtime image not verified on node {node.node_id}: "
                f"{result.get('error') or 'digest mismatch'}"
            )

    @staticmethod
    def _require_verified(result: dict[str, Any], node, bundles) -> None:
        if not result.get("verified"):
            bundle = bundles[node.node_id][0]
            raise _LifecycleFailed(
                f"Runtime image {bundle.container.image} "
                f"({bundle.container.digest}) not verified on node {node.node_id}: "
                f"{result.get('error') or 'digest mismatch'}"
            )

    @staticmethod
    def _seed_token(environment, node_id: Any) -> str:
        """The one-off secret a seeding member and its receivers share.

        Derived rather than random so a reconcile pass that resumes these
        commands arrives at the same value: a fresh random token on the
        second pass would disagree with the one already sent to the peer.
        Keyed on the server's master key, so it cannot be worked out from
        the ids it mixes in.
        """
        import hashlib  # noqa: PLC0415
        import hmac  # noqa: PLC0415

        from llm_port_backend.settings import settings  # noqa: PLC0415

        message = f"image-seed:{environment.id}:{environment.generation}:{node_id}".encode()
        return hmac.new(settings.settings_master_key.encode(), message, hashlib.sha256).hexdigest()

    async def _await_final(self, gateway: NodeCommandGateway, command: Any, *, what: str) -> Any:
        """Wait for *command* to finish and return it, failed or not."""
        final = await gateway.wait(command.id, budget_sec=_LIFECYCLE_WAIT_SEC)
        if final is None:
            raise _LifecycleUnobserved(f"{what}: no result within {_LIFECYCLE_WAIT_SEC:.0f}s")
        return final

    # ------------------------------------------------------------------
    # Failure memory: not asking the same question twice
    # ------------------------------------------------------------------

    async def _inputs_fingerprint(self, session, environment, nodes) -> str:
        """What a retry would depend on, as one comparable string.

        The generation (any operator change bumps it) and, per member, the
        image the catalogue pins for that machine. A rebuild changes the pin,
        an edit changes the generation; either means a retry could now come
        out differently, and either resets the backoff.
        """
        import hashlib  # noqa: PLC0415

        parts = [f"gen={environment.generation}"]
        try:
            bundles = await self._bundles_for(session, nodes)
        except _LifecycleFailed:
            parts.append("bundles=none")
        else:
            for node in sorted(nodes, key=lambda n: str(n.node_id)):
                bundle = bundles[node.node_id][0]
                container = getattr(bundle, "container", None)
                parts.append(
                    f"{node.node_id}={getattr(container, 'digest', None)}"
                    f"/{getattr(container, 'rootfs_digest', None)}"
                )
        return hashlib.sha256("|".join(parts).encode()).hexdigest()[:32]

    @staticmethod
    def _held_back(environment, fingerprint: str) -> str | None:
        """Why this pass should not issue anything, or ``None`` to proceed."""
        from datetime import UTC, datetime  # noqa: PLC0415

        retry = (environment.observed_status_json or {}).get("retry")
        if not isinstance(retry, dict):
            return None
        if retry.get("fingerprint") != fingerprint:
            return None  # something it depended on has changed
        due = retry.get("next_attempt_at")
        if not due:
            return None
        try:
            due_at = datetime.fromisoformat(due)
        except (TypeError, ValueError):
            return None
        if datetime.now(tz=UTC) >= due_at:
            return None
        return f"retrying after {due} ({retry.get('code') or 'failure'}, attempt {retry.get('attempts')})"

    @staticmethod
    def _record_failure(environment, exc: "_LifecycleFailed", fingerprint: str) -> str:
        """Remember the failure and when to try again; return what to show.

        Every failed cluster used to be revisited on every reconciler tick --
        303 identical attempts in three hours for a digest that could not
        match. Repeats now back off, and a failure that cannot change until
        its inputs do says so, so the operator knows it is waiting on them.
        """
        from datetime import UTC, datetime, timedelta  # noqa: PLC0415

        observed = dict(environment.observed_status_json or {})
        previous = observed.get("retry") if isinstance(observed.get("retry"), dict) else {}
        same = previous.get("fingerprint") == fingerprint and previous.get("code") == exc.code
        attempts = int(previous.get("attempts") or 0) + 1 if same else 1

        if exc.permanent:
            wait = _PERMANENT_RECHECK_SEC
        else:
            wait = min(_RETRY_BASE_SEC * 2 ** (attempts - 1), _RETRY_CAP_SEC)
        now = datetime.now(tz=UTC)
        observed["retry"] = {
            "code": exc.code,
            "permanent": exc.permanent,
            "fingerprint": fingerprint,
            "attempts": attempts,
            "last_failed_at": now.isoformat(),
            "next_attempt_at": (now + timedelta(seconds=wait)).isoformat(),
        }
        environment.observed_status_json = observed

        if exc.permanent:
            return (
                f"{exc.detail} This will not change by retrying, so LLM.Port is "
                f"not retrying it on its own until the runtime image or the "
                f"cluster changes; it re-checks every hour. Use Try again once "
                f"you have fixed it."
            )
        minutes = max(1, round(wait / 60))
        return (
            f"{exc.detail} Trying again in about {minutes} minute"
            f"{'' if minutes == 1 else 's'} (attempt {attempts})."
        )

    @staticmethod
    def _clear_failure(environment) -> None:
        observed = dict(environment.observed_status_json or {})
        if observed.pop("retry", None) is not None:
            environment.observed_status_json = observed

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    async def _offline_members(session, nodes) -> tuple[dict, dict]:
        """Members with no agent connected, as (gone, reconnecting).

        Each maps node id -> name. *Reconnecting* is offline but heard from
        within the grace: ``_OFFLINE_GRACE_SEC`` for the first minutes after
        this backend started -- every machine is reconnecting then -- and
        ``_SINGLE_DROP_GRACE_SEC`` after that.
        """
        from datetime import UTC, datetime, timedelta  # noqa: PLC0415

        ids = [n.node_id for n in nodes]
        if not ids:
            return {}, {}
        rows = await session.execute(select(InfraNode).where(InfraNode.id.in_(ids)))
        just_started = time.monotonic() - _PROCESS_STARTED < 2 * _OFFLINE_GRACE_SEC
        grace = _OFFLINE_GRACE_SEC if just_started else _SINGLE_DROP_GRACE_SEC
        cutoff = datetime.now(tz=UTC) - timedelta(seconds=grace)
        gone: dict = {}
        reconnecting: dict = {}
        for node in rows.scalars():
            if node.status != NodeHealthStatus.OFFLINE:
                continue
            name = node.agent_id or node.host or str(node.id)
            recent = node.last_seen is not None and node.last_seen >= cutoff
            (reconnecting if recent else gone)[node.id] = name
        return gone, reconnecting

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
            raise _LifecycleFailed(
                f"{what} failed: {detail}",
                code=final.error_code,
                detail=str(detail),
            )
        return dict(final.result_json or {})

    async def _bundles_for(self, session, nodes) -> "dict[Any, Any]":
        """The certified bundle for each member node, keyed by node id.

        Raises :class:`_LifecycleFailed` when a member has none. That is a
        refusal on purpose: the alternative is the agent falling back to a
        host Ray install, which on a node provisioned for the container
        runtime does not exist, so the cluster would fail later and blame the
        wrong thing.
        """
        from llm_port_backend.services.inference.bundles import (  # noqa: PLC0415
            node_and_bundle_for,
        )

        resolved: dict[Any, Any] = {}
        unsupported: list[str] = []
        for node in nodes:
            row, bundle = await node_and_bundle_for(
                session, node.node_id, driver="ray"
            )
            if bundle is None:
                unsupported.append(str(node.node_id))
            resolved[node.node_id] = (bundle, row)
        if unsupported:
            raise _LifecycleFailed(
                "No certified Ray runtime bundle covers "
                f"{'node' if len(unsupported) == 1 else 'nodes'} "
                f"{', '.join(unsupported)}. A node joins a cluster on the "
                "strength of its platform, so one with no bundle for its "
                "CPU architecture and accelerator cannot be a member.",
                code="no_certified_runtime",
            )
        return resolved

    async def _ensure_runtimes(
        self,
        session,
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

        The payload differs per node: two members of the same cluster can be
        different architectures, and each gets the image certified for its
        own.
        """
        bundles = await self._bundles_for(session, nodes)
        payloads = {
            node.node_id: self._bundle_payload(
                bundles[node.node_id][0], node=bundles[node.node_id][1]
            )
            for node in nodes
        }

        # 2a. The exact OCI digest must be present on every member node
        # before anything tries to run out of it.  The agent verifies the
        # pinned identity locally and, when absent, side-loads it from the
        # backend — never from a public registry.
        await self._ensure_images(
            environment, gateway=gateway, nodes=nodes, bundles=bundles, payloads=payloads
        )

        issued = []
        for node in nodes:
            payload: dict[str, Any] = {
                "version": config.ray_version,
                "runtime_bundle": payloads[node.node_id],
            }
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
                raise _LifecycleFailed(
                    f"Ray runtime {config.ray_version} not installed in the "
                    f"runtime container on node {node.node_id}: "
                    f"{result.get('error') or 'not found'}"
                )

    @staticmethod
    async def _bundle_of(session, node_id) -> "tuple[Any, Any]":
        """The bundle certified for one machine, and that machine's row.

        Keyed on the node rather than on the environment: architecture is a
        property of a machine, and a cluster is allowed to span two of them.
        The row comes back alongside because the launch spec is composed
        against it -- the image is the bundle's, the host paths are the
        node's -- and looking it up twice would be the only alternative.
        """
        from llm_port_backend.services.inference.bundles import (  # noqa: PLC0415
            node_and_bundle_for,
        )

        node, bundle = await node_and_bundle_for(session, node_id, driver="ray")
        return bundle, node

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
    def _bundle_payload(
        cls, bundle, env_vars: dict[str, str] | None = None, node: Any = None
    ) -> dict[str, Any] | None:
        """Render the bundle into the agent's container launch contract."""
        if bundle is None:
            return None
        from llm_port_backend.services.inference.bundles import default_bundle_registry

        return default_bundle_registry.container_launch_spec(
            bundle, name=_RUNTIME_CONTAINER_NAME, env=env_vars or {}, node=node,
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
        """The fabric apply-plan resolved, from the observation that owns it.

        No fallback to ``config_json``: it used to hold a copy, and reading
        one meant a stale binding could outlive the observation that replaced
        it -- the environment would then be started against an address nobody
        had resolved for it.
        """
        return (environment.observed_status_json or {}).get("resolved_fabric")

    async def _start_head(
        self,
        session,
        environment,
        head,
        credential_ref,
        config,
        gateway: NodeCommandGateway,
        *,
        attempt: int | None = None,
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

        bundle, head_row = await self._bundle_of(session, head.node_id)
        env_vars = self._bundle_env(environment, bundle, env_vars)
        bundle_payload = self._bundle_payload(bundle, node=head_row)
        if bundle_payload is not None:
            payload["runtime_bundle"] = bundle_payload

        if env_vars:
            payload["env"] = env_vars

        cmd = await self._issue(
            gateway,
            node_id=head.node_id,
            command_type=NodeCommandType.START_RAY_HEAD.value,
            payload=payload,
            key=(
                self._recover_key(environment, attempt, "start-head", head.node_id)
                if attempt
                else f"inference-env:{environment.id}:{environment.generation}:start-head"
            ),
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
        *,
        attempt: int | None = None,
    ) -> None:
        """Step 5: join every worker (issued together, then awaited)."""
        resolved_fabric = self._get_resolved_fabric(environment)
        node_bindings = (resolved_fabric or {}).get("node_bindings", {})

        issued = []
        for worker in workers:
            # Per worker: the head may be a DGX and the worker a workstation,
            # and the image each joins with has to be its own.
            bundle, worker_row = await self._bundle_of(session, worker.node_id)
            bundle_payload = self._bundle_payload(bundle, node=worker_row)
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
                key=(
                    self._recover_key(environment, attempt, "join", worker.node_id)
                    if attempt
                    else f"inference-env:{environment.id}:{environment.generation}:join-worker:{worker.node_id}"
                ),
            )
            issued.append((worker, cmd))
        for worker, cmd in issued:
            await self._await_result(gateway, cmd, what=f"join_ray_cluster on node {worker.node_id}")

    async def _verify_cluster(self, session, head, node_control) -> RayClusterStatus:
        """Step 6: probe the head for authoritative cluster membership.

        The pinned bundle travels with the probe so a containerized
        environment is observed through the in-container helper.  Without it
        the agent would answer from a host Ray SDK that a certified node does
        not have, and the cluster would read as dead.
        """
        client = RayClusterClient(node_control)
        head_bundle, head_row = await self._bundle_of(session, head.node_id)
        bundle_payload = self._bundle_payload(head_bundle, node=head_row)
        try:
            return await client.probe_cluster(
                head_node_id=head.node_id,
                runtime_bundle=bundle_payload,
                include_metrics=True,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("Ray cluster verify on %s failed: %s", head.node_id, e)
            # Not heard from, which is not the same as heard to be down: a
            # health check must not restart a cluster over its own error.
            return RayClusterStatus(alive=False, observed=False)

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

    async def _teardown(
        self, session, environment, head, workers, gateway: NodeCommandGateway
    ) -> None:
        """Workers leave, then the head stops; each result is confirmed (F23).

        Raises :class:`_LifecycleFailed` / :class:`_LifecycleUnobserved` so
        the caller never records STOPPED for a teardown it did not observe.
        Both agent commands succeed when nothing is running, so a retry after
        a partial teardown is safe.
        """
        version = environment.runtime_version or "2.58.0"

        issued = []
        for worker in workers:
            # Without the bundle the agent would stop a host Ray that was
            # never started and leave the runtime container running -- and
            # the bundle it has to name is the worker's own.
            leave_payload: dict[str, Any] = {"version": version}
            worker_bundle, worker_row = await self._bundle_of(session, worker.node_id)
            worker_payload = self._bundle_payload(worker_bundle, node=worker_row)
            if worker_payload is not None:
                leave_payload["runtime_bundle"] = worker_payload
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
            stop_payload: dict[str, Any] = {"force": True, "version": version}
            head_bundle, head_row = await self._bundle_of(session, head.node_id)
            head_payload = self._bundle_payload(head_bundle, node=head_row)
            if head_payload is not None:
                stop_payload["runtime_bundle"] = head_payload
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
        cluster: RayClusterStatus | None,
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
        observed["observation"] = {
            "status": status.value if hasattr(status, "value") else str(status),
            "reconciled": status is not EnvironmentStatus.PENDING,
            "reason": reason,
        }
        if cluster is not None:
            # ``None`` keeps the last snapshot: a status change on a hunch
            # (a health check that has to look twice) is not a measurement.
            observed["cluster"] = cluster.model_dump()
            observed["conditions"] = build_environment_conditions(cluster, expected_nodes)
        environment.observed_status_json = observed
        # The cluster page shows ``status_message`` when a cluster needs
        # attention and falls back to "Some machines are not reporting" when
        # it is empty. It was never written, so a cluster refused for a
        # mismatched image digest -- with the machine reporting perfectly
        # well -- told the operator the one thing that was not wrong.
        if status in (EnvironmentStatus.FAILED, EnvironmentStatus.DEGRADED):
            environment.status_message = reason
        elif status in (EnvironmentStatus.READY, EnvironmentStatus.STOPPED):
            environment.status_message = None
        if address:
            environment.address = address
        if version:
            environment.runtime_version = version

