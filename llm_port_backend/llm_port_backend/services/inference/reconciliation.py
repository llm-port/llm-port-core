"""Reconciliation seams for the neutral inference domain (Phase 2: live drivers).

This module is the stable place where driver-backed reconciliation plugs in:

* the :func:`*_observation` builders produce the *honest no-op* payloads
  recorded when a driver cannot act (unregistered driver, or no reachable
  target); they are persisted into the ``*_observed_status_json`` columns;
* :class:`ReconciliationContext` bundles everything a single reconciliation
  pass needs — the session, the three domain services, and a lazily-built
  :class:`~llm_port_backend.services.nodes.service.NodeControlService`;
* the :func:`reconcile_control_plane` / :func:`reconcile_environment` /
  :func:`reconcile_deployment` functions are the per-item entry points.

Each per-item function resolves the driver through
:class:`~llm_port_backend.services.inference.registry.DriverRegistry`.  When
the driver is registered **and** the target can be reached, the driver is
driven for real.  Otherwise an honest no-op observation (``reconciled=False``)
is recorded at the current generation.

A driver's returned report is persisted into the row's
``observed_status_json["observation"]`` and ``observed_generation`` so the
``list_pending_observation`` query stops re-selecting an unchanged row.  The
functions here do **not** commit; the owning background loop (or the
request-scoped caller) commits after the function returns.
"""

from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable

log = logging.getLogger(__name__)

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.models.inference import (
    InferenceControlPlane,
    InferenceEnvironment,
    InferenceEnvironmentNode,
)
from llm_port_backend.db.models.node_control import InfraNode
from llm_port_backend.services.inference.registry import registry

if TYPE_CHECKING:  # pragma: no cover - import-time only, avoids a runtime cycle
    from llm_port_backend.db.models.inference import InferenceDeployment
    from llm_port_backend.services.inference.service import (
        ControlPlaneService,
        DeploymentService,
        EnvironmentService,
    )
    from llm_port_backend.services.nodes.service import NodeControlService


def _accepts_param(func: Callable, name: str) -> bool:
    """Return True if ``func`` can be called with a keyword named ``name``.

    The neutral :class:`~llm_port_backend.services.inference.contracts`
    protocols declare minimal signatures (e.g. ``probe(control_plane)``), but
    concrete Phase 2 drivers extend them with ``session`` / ``node_control``
    kwargs so the reconciliation layer can hand them live resources.  This
    helper lets the dispatcher pass those extras only to implementations that
    declare them, keeping protocol-conformant test doubles *and* full drivers
    working through one call site.  A trailing ``**kwargs`` also counts.
    """
    try:
        params = inspect.signature(func).parameters
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return False
    if name in params:
        return True
    return any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())


def _node_control_or_none(context: Any) -> Any:
    """Best-effort node command gateway or control service from the context, else ``None``.

    The real :class:`ReconciliationContext` builds one lazily; lightweight
    test contexts (``SimpleNamespace`` stubs) carry no such attribute.  Probe
    and manager dispatch receive a gateway/control service when it can
    actually be produced — otherwise the driver takes its honest no-op path.
    """
    try:
        if getattr(context, "_node_control", None) is not None:
            return context._node_control
        if hasattr(context, "command_gateway") and context.command_gateway is not None:
            return context.command_gateway
        return context.node_control
    except Exception:  # noqa: BLE001 - AttributeError on stub contexts is expected
        return None


# ---------------------------------------------------------------------------
# Observation builders (honest no-ops used when no driver can act)
# ---------------------------------------------------------------------------


def control_plane_observation(driver: str, reason: str | None = None) -> dict[str, Any]:
    """Honest no-op observation for a control plane that could not be probed.

    Used when the driver is unregistered, or (for Ray) when the control plane
    has no bound head node to dispatch a probe to.
    """
    return {
        "reconciled": False,
        "probed": False,
        "driver": driver,
        "reason": reason or "no driver registered (Phase 1)",
    }


def environment_observation() -> dict[str, Any]:
    """Honest no-op observation for an environment that was never reconciled."""
    return {
        "reconciled": False,
        "actions": [],
        "reason": "no live actions in Phase 1",
    }


def deployment_observation() -> dict[str, Any]:
    """Honest no-op observation for a deployment that was never reconciled."""
    return {
        "reconciled": False,
        "applied": False,
        "reason": "no live actions in Phase 1",
    }


@dataclass
class ReconciliationContext:
    """Everything a single reconciliation pass needs.

    Holds the request/session-scoped :class:`AsyncSession` plus the three
    domain services acting on it.  Construct via :meth:`for_session`.
    """

    session: AsyncSession
    control_planes: ControlPlaneService
    environments: EnvironmentService
    deployments: DeploymentService
    session_factory: Any | None = None
    gateway_sync: Any | None = None
    _node_control: NodeControlService | None = None
    _command_gateway: Any | None = None

    @property
    def node_control(self) -> NodeControlService:
        """Lazily built, session-scoped node control service."""
        if self._node_control is None:
            self._node_control = _build_node_control_service(self.session)
        return self._node_control

    @property
    def command_gateway(self) -> Any:
        """Lazily built command gateway using session_factory (or session fallback)."""
        if self._command_gateway is None:
            from llm_port_backend.services.inference.drivers.ray.commands import (  # noqa: PLC0415
                NodeCommandGateway,
            )

            target = self.session_factory if self.session_factory is not None else self.session
            self._command_gateway = NodeCommandGateway(target)
        return self._command_gateway

    @classmethod
    def for_session(
        cls,
        session: AsyncSession,
        session_factory: Any | None = None,
        gateway_sync: Any | None = None,
    ) -> ReconciliationContext:
        # Lazy import: ``service`` imports the observation builders from *this*
        # module, so importing the services at module top would be a cycle.
        from llm_port_backend.services.inference.service import (  # noqa: PLC0415
            ControlPlaneService,
            DeploymentService,
            EnvironmentService,
        )

        return cls(
            session=session,
            control_planes=ControlPlaneService(session),
            environments=EnvironmentService(session),
            deployments=DeploymentService(session),
            session_factory=session_factory,
            gateway_sync=gateway_sync,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_node_control_service(session: AsyncSession) -> Any:
    """Lazily construct a session-scoped :class:`NodeControlService`.

    Mirrors the construction used by the node-command reaper loop in
    :mod:`llm_port_backend.web.lifespan` so the reconciler and the reaper
    agree on timeouts, enrollment TTL and the auth pepper.  Imported lazily to
    avoid any import cycle with the services package.
    """
    from llm_port_backend.db.dao.node_control_dao import (  # noqa: PLC0415
        NodeControlDAO,
    )
    from llm_port_backend.settings import settings  # noqa: PLC0415
    from llm_port_backend.services.nodes.service import (  # noqa: PLC0415
        NodeControlService,
    )

    return NodeControlService(
        dao=NodeControlDAO(session),
        pepper=settings.settings_master_key,
        enrollment_ttl_minutes=settings.node_enrollment_ttl_minutes,
        default_command_timeout_sec=settings.node_command_default_timeout_sec,
    )


def _mark_observed(row: Any, observation: dict[str, Any]) -> None:
    """Persist an observation so the row stops being "pending observation".

    The observation is stored under ``observed_status_json["observation"]`` and
    the row is stamped observed at its current generation.  The caller commits.

    Guarded with ``getattr`` so that lightweight test doubles that lack the
    ORM ``generation``/``observed_*`` attributes (they exercise only the
    report-shape contract) do not break the seam.
    """
    generation = getattr(row, "generation", None)
    if generation is not None:
        row.observed_generation = generation
    status = dict(getattr(row, "observed_status_json", None) or {})
    status["observation"] = observation
    try:
        row.observed_status_json = status
    except AttributeError:  # pragma: no cover - stub row without the column
        pass


# ---------------------------------------------------------------------------
# Per-item reconcilers
# ---------------------------------------------------------------------------


async def reconcile_control_plane(
    context: ReconciliationContext, control_plane: InferenceControlPlane
) -> dict[str, Any]:
    """Drive one control plane toward its desired state.

    If the control plane's driver is registered **and** it has a bound head
    node to probe, dispatch a real probe through the node control service.
    Otherwise record an honest no-op observation.  The observation (whether a
    real probe result or a no-op) is persisted to the row; the caller commits.
    """
    driver_cls = registry.get(control_plane.driver)
    node_control = _node_control_or_none(context)
    if driver_cls is not None:
        driver = driver_cls()
        # Pass live resources only to implementations that declare them — the
        # neutral protocol declares ``probe(control_plane)`` with no extras, so
        # protocol-conformant doubles that only take the control plane keep
        # working untouched.
        probe_kwargs: dict[str, Any] = {}
        if _accepts_param(driver.probe, "session"):
            probe_kwargs["session"] = context.session
        if _accepts_param(driver.probe, "node_control"):
            probe_kwargs["node_control"] = node_control
        try:
            observation = await driver.probe(control_plane, **probe_kwargs)
        except Exception as exc:  # noqa: BLE001 - one bad probe must not kill the loop
            observation = control_plane_observation(control_plane.driver, reason=f"probe error: {exc}")
        # A live probe completed (the driver reports ``reconciled: True``):
        # persist its report on the row.  Otherwise (the driver could not
        # reach a head, or was a no-op stub) fall through to the honest no-op,
        # which the domain service records at the current generation.
        if observation.get("reconciled"):
            _mark_observed(control_plane, observation)
            return {
                "id": str(control_plane.id),
                "driver": control_plane.driver,
                "reconciled": True,
                "reason": observation.get("reason"),
            }
        # The driver could not reach a live node (no node control, no bound
        # head) → fall through to the honest no-op, which the domain service
        # records at the current generation.

    await context.control_planes.reconcile(control_plane.id)
    return {
        "id": str(control_plane.id),
        "driver": control_plane.driver,
        "reconciled": False,
        "reason": "no live control plane to probe",
    }


async def reconcile_environment(
    context: ReconciliationContext, environment: InferenceEnvironment
) -> dict[str, Any]:
    """Drive one environment toward its desired state.

    Phase 2: resolve the bound control plane's driver and dispatch
    ``EnvironmentManager.reconcile_environment`` when a driver is registered.
    The driver owns the load → external action → fresh-write discipline and
    persists its own observed state.  When no driver is registered, record an
    honest no-op observation.

    This function does not commit; the background loop commits after it
    returns.  Per-resource errors are isolated by the caller.
    """
    driver_cls: Any = None
    cp_id = getattr(environment, "control_plane_id", None)
    if cp_id is not None:
        cp = await context.session.get(InferenceControlPlane, cp_id)
        if cp is not None:
            driver_cls = registry.get(cp.driver)

    if driver_cls is None:
        # No registered driver → delegate to the domain service, which records
        # an honest no-op observation at the current generation.
        await context.environments.reconcile(environment.id)
        return {
            "id": str(environment.id),
            "reconciled": False,
            "reason": "no driver registered",
        }

    driver = driver_cls()
    mgr = getattr(driver, "environment_manager", None)
    if mgr is None:
        await context.environments.reconcile(environment.id)
        return {
            "id": str(environment.id),
            "reconciled": False,
            "reason": "driver exposes no environment manager",
        }

    # The manager protocol declares ``reconcile_environment(session,
    # environment)``; Phase 2 managers extend it with ``node_control``.  Pass
    # the live node control service only when the manager accepts it.
    mgr_kwargs: dict[str, Any] = {}
    if _accepts_param(mgr.reconcile_environment, "node_control"):
        mgr_kwargs["node_control"] = _node_control_or_none(context)
    before = _status_text(environment.status)
    await mgr.reconcile_environment(context.session, environment, **mgr_kwargs)
    if _status_text(environment.status) != before:
        # The cluster went down, or came back: its models have to find out.
        await _requeue_deployments(context, environment)

    # Copy what the cluster says about each machine onto the machine's own
    # row.  Without this the membership table and the topology picture have
    # nothing to colour by, and every node reads "not reporting" on a cluster
    # that is serving.
    await _sync_member_status(context, environment)

    # Hand this cluster's metrics endpoints to Prometheus.  The reconcile just
    # refreshed them, and they are per node -- a cluster that gained a machine
    # gains a scrape target here rather than whenever someone notices.
    await _sync_prometheus_targets(context, environment)

    return {
        "id": str(environment.id),
        "reconciled": True,
        "reason": "dispatched to driver",
    }


def _status_text(status: Any) -> str:
    return str(getattr(status, "value", status) or "")


async def _requeue_deployments(
    context: ReconciliationContext, environment: InferenceEnvironment
) -> None:
    """Queue every live deployment on *environment* for a fresh look.

    A running deployment is only revisited when something about it changes,
    and its cluster going down is not a change to the deployment's row. So a
    cluster could be restarted from scratch -- its Serve applications gone
    with the old head -- while its deployments went on reading "running"
    and nothing re-applied them.
    """
    from llm_port_backend.db.models.inference import (  # noqa: PLC0415
        DeploymentDesiredState,
        InferenceDeployment,
    )
    from llm_port_backend.services.inference.service import (  # noqa: PLC0415
        _queue_for_reconcile,
    )
    from llm_port_backend.services.inference.wakeup import (  # noqa: PLC0415
        wake_reconciler_after_commit,
    )

    try:
        rows = await context.session.execute(
            select(InferenceDeployment).where(
                InferenceDeployment.environment_id == environment.id,
                InferenceDeployment.desired_state.notin_([
                    DeploymentDesiredState.DELETED.value,
                    DeploymentDesiredState.STOPPED.value,
                ]),
            )
        )
        deployments = list(rows.scalars())
    except Exception:  # noqa: BLE001 - never fails the cluster's own pass
        log.exception("Could not list the deployments of environment %s", environment.id)
        return
    for deployment in deployments:
        _queue_for_reconcile(deployment)
    if deployments:
        wake_reconciler_after_commit(context.session)


async def _sync_member_status(
    context: ReconciliationContext, environment: InferenceEnvironment
) -> None:
    """Write each member's liveness from the cluster observation onto its row.

    ``member_status`` had no writer at all.  It was renamed from ``ray_status``
    when the column was generalised across backends, and the rename moved the
    column without moving anything into it, so every membership row stayed
    ``NULL`` however healthy the cluster was.  The visible symptom was the
    topology diagram: its ring colour is chosen from this field, and a null
    means "not reporting", so a cluster with two live nodes serving traffic
    drew two grey circles and dashed edges.

    Matching Ray's nodes to ours is the same problem the scrape targets have.
    Ray identifies a node by its own 56-hex id and addresses it by whatever
    address it was started on -- the fabric link here, which is not the
    address we know the machine by.  The fabric plan is the bridge; the
    management address is tried too, because which one Ray reports depends on
    how the cluster was brought up.
    """
    try:
        stored = (environment.observed_status_json or {}).get("cluster") or {}
        ray_nodes = stored.get("nodes") or []
        if not ray_nodes:
            # Nothing observed yet.  Leaving the rows alone is right: a probe
            # that has not happened is not evidence that a node is down.
            return

        session = context.session
        bindings = (
            ((environment.observed_status_json or {}).get("resolved_fabric") or {}).get(
                "node_bindings"
            )
            or {}
        )

        # Every address a Ray node might be reported under -> alive?
        alive_by_address: dict[str, bool] = {}
        for entry in ray_nodes:
            if not isinstance(entry, dict):
                continue
            alive = bool(entry.get("alive"))
            for key in ("node_ip", "node_manager_address", "node_name"):
                value = entry.get(key)
                if value:
                    alive_by_address[str(value).strip()] = alive

        members = (
            await session.execute(
                select(InferenceEnvironmentNode).where(
                    InferenceEnvironmentNode.environment_id == environment.id
                )
            )
        ).scalars().all()

        now = datetime.now(timezone.utc)
        for member in members:
            addresses: list[str] = []
            binding = bindings.get(str(member.node_id)) or {}
            if isinstance(binding, dict) and binding.get("ip"):
                addresses.append(str(binding["ip"]).strip())
            node = await session.get(InfraNode, member.node_id)
            if node is not None and node.host:
                addresses.append(str(node.host).strip())

            alive = next(
                (alive_by_address[a] for a in addresses if a in alive_by_address),
                None,
            )
            if alive is None:
                # The cluster was observed and does not know this machine.
                # That is a real answer -- it has not joined -- and distinct
                # from never having looked.
                member.member_status = "unknown"
                continue

            member.member_status = "alive" if alive else "dead"
            if alive and member.joined_at is None:
                # First time we have seen it in the cluster.  Approximate, but
                # a date is more use than a blank, and it only ever set once.
                member.joined_at = now
    except Exception:  # noqa: BLE001 - a display field never fails a reconcile
        log.exception(
            "Could not record member status for environment %s", environment.id
        )


async def _sync_prometheus_targets(
    context: ReconciliationContext, environment: InferenceEnvironment
) -> None:
    """Register the environment's Ray metrics endpoints for file_sd discovery.

    Best-effort: a monitoring problem must never fail a reconcile, because the
    cluster is fine either way and a failed reconcile would stop real work.
    """
    try:
        from llm_port_backend.services.llm.monitoring import (  # noqa: PLC0415
            get_monitoring_provisioner,
        )

        # The shared accessor, not a fresh instance: it returns None when
        # monitoring is disabled, and it owns the debounced reload state.
        provisioner = get_monitoring_provisioner()
        if provisioner is None:
            return

        # Stopped, failed, or on its way down: nothing is serving metrics, and
        # the ports it had will not be reused. Keeping them left Prometheus
        # dialling dead addresses for as long as the file lived.
        if environment.desired_state != "running" or str(environment.status) in (
            "stopped",
            "failed",
        ):
            await provisioner.remove_ray_targets(environment.id)
            return

        stored = (environment.observed_status_json or {}).get("cluster") or {}
        raw_targets = ((stored.get("metrics") or {}).get("targets")) or []
        if not raw_targets:
            return

        driver_cls = registry.get("ray")
        driver = driver_cls() if driver_cls is not None else None
        if driver is None or not hasattr(driver, "_scrape_targets"):
            return

        # Reuse the driver's translation: Ray advertises fabric addresses, and
        # a scrape target has to be one Prometheus can actually dial.
        resolved = await driver._scrape_targets(
            context.session, stored.get("metrics"), environment
        )
        await provisioner.sync_ray_targets(
            environment_id=environment.id,
            environment_name=environment.name,
            targets=[{"address": t.address, "port": t.port} for t in resolved],
        )
        # A cluster with scrape targets and no dashboard is a cluster whose
        # metrics exist and cannot be looked at.  Rendered here, from the
        # same name the targets are labelled with, so the two cannot drift.
        await provisioner.provision_environment(
            environment_id=environment.id,
            environment_name=environment.name,
        )
    except Exception:  # noqa: BLE001 - monitoring never fails a reconcile
        log.exception(
            "Could not register Prometheus targets for environment %s", environment.id
        )


async def reconcile_deployment(
    context: ReconciliationContext, deployment: InferenceDeployment
) -> dict[str, Any]:
    """Drive one deployment toward its desired state.

    Phase 3: resolve the bound control plane's driver and dispatch
    ``RayDeploymentManager.reconcile_deployment`` when a driver is registered.
    The manager owns the load → compile → external action → fresh-write
    discipline and persists its own observed state.  When no driver (or no
    deployment manager) is registered, record an honest no-op observation.

    This function does not commit; the background loop commits after it
    returns.  Per-resource errors are isolated by the caller.
    """
    driver_cls: Any = None
    env_id = getattr(deployment, "environment_id", None)
    cp_id = None
    if env_id is not None:
        env = await context.session.get(InferenceEnvironment, env_id)
        if env is not None:
            cp_id = env.control_plane_id
    if cp_id is not None:
        cp = await context.session.get(InferenceControlPlane, cp_id)
        if cp is not None:
            driver_cls = registry.get(cp.driver)

    if driver_cls is None:
        # No registered driver → delegate to the domain service, which records
        # an honest no-op observation at the current generation.
        await context.deployments.reconcile(deployment.id)
        return {
            "id": str(deployment.id),
            "reconciled": False,
            "reason": "no driver registered",
        }

    driver = driver_cls()
    mgr = getattr(driver, "deployment_manager", None)
    if mgr is None:
        await context.deployments.reconcile(deployment.id)
        return {
            "id": str(deployment.id),
            "reconciled": False,
            "reason": "driver exposes no deployment manager",
        }

    # The manager protocol declares ``reconcile_deployment(session,
    # deployment)``; the Ray manager extends it with ``node_control``.  Pass
    # the live node control service only when the manager accepts it.
    mgr_kwargs: dict[str, Any] = {}
    if _accepts_param(mgr.reconcile_deployment, "node_control"):
        mgr_kwargs["node_control"] = _node_control_or_none(context)
    await mgr.reconcile_deployment(context.session, deployment, **mgr_kwargs)

    # Generic inference publication reconciliation (Phase 5).
    #
    # Run whether or not a gateway is configured: the coordinator also owns
    # the provider row on the providers screen, which is the backend's own
    # record and has to exist either way. It skips the routing half itself
    # when there is no gateway.
    try:
        from llm_port_backend.services.inference.publication import (  # noqa: PLC0415
            InferencePublicationCoordinator,
        )

        pub = InferencePublicationCoordinator(
            context.session,
            gateway_sync=getattr(context, "gateway_sync", None),
        )
        await pub.reconcile_deployment_publication(deployment)
    except Exception:
        # Publication failure must not fail the deployment reconciliation pass
        log.exception(
            "Failed to reconcile publication for deployment %s",
            deployment.id,
        )

    return {
        "id": str(deployment.id),
        "reconciled": True,
        "reason": "dispatched to driver",
    }
