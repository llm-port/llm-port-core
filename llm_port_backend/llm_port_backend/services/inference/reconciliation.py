"""Reconciliation seams for the neutral inference domain (Phase 1: skeleton only).

Phase 1 ships **no live actions**.  This module is the stable place where Phase
2's driver-backed reconciliation plugs in:

* the :func:`*_observation` helpers describe what a reconciler would have
  *observed* when it actually ran against a backend; they are the payloads
  persisted into the ``*_observed_status_json`` columns by the Phase 1
  (no-op) reconciliation in :mod:`service`;
* :class:`ReconciliationContext` bundles everything a single reconciliation
  pass needs (the session plus the three domain services);
* the :func:`reconcile_control_plane` / :func:`reconcile_environment` /
  :func:`reconcile_deployment` functions are the per-item entry points.

In Phase 1 each of those resolves the driver through the (empty)
:class:`~llm_port_backend.services.inference.registry.DriverRegistry` and,
finding none, records an honest no-op observation and returns a ``reconciled``
report of :data:`False`.  When a driver is registered (Phase 2+) the same
functions instantiate it and drive a real probe/observe/delete — no call-site
changes are required.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.services.inference.registry import registry

if TYPE_CHECKING:  # pragma: no cover - import-time only, avoids a runtime cycle
    from llm_port_backend.db.models.inference import (
        InferenceControlPlane,
        InferenceDeployment,
        InferenceEnvironment,
    )
    from llm_port_backend.services.inference.service import (
        ControlPlaneService,
        DeploymentService,
        EnvironmentService,
    )


def control_plane_observation(driver: str) -> dict[str, Any]:
    """Phase 1 no-op observation for a control-plane probe.

    Phase 2 will return the driver's ``probe`` result (health, versions, ...)
    instead of this stub.
    """
    return {
        "reconciled": False,
        "probed": False,
        "driver": driver,
        "reason": "no driver registered (Phase 1)",
    }


def environment_observation() -> dict[str, Any]:
    """Phase 1 no-op observation for an environment reconcile.

    Phase 2 will return the backend's environment state (roles, head address,
    membership, ...) instead of this stub.
    """
    return {
        "reconciled": False,
        "actions": [],
        "reason": "no driver registered (Phase 1)",
    }


def deployment_observation() -> dict[str, Any]:
    """Phase 1 no-op observation for a deployment reconcile.

    Phase 2 will return the orchestrator's observed deployment state (phase,
    ready/total replicas, endpoint status, ...) instead of this stub.
    """
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

    @classmethod
    def for_session(cls, session: AsyncSession) -> ReconciliationContext:
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
        )


# ---------------------------------------------------------------------------
# Phase 1 skeleton reconcilers (no live actions)
# ---------------------------------------------------------------------------


async def reconcile_control_plane(
    context: ReconciliationContext, control_plane: InferenceControlPlane
) -> dict[str, Any]:
    """Drive one control plane toward its desired state.

    Phase 1: no driver is registered, so record a no-op observation and report
    ``reconciled=False``.
    Phase 2: resolve ``driver_cls = registry.get(control_plane.driver)`` and
    call ``await driver.probe(control_plane)``, then persist the result.
    """
    if registry.get(control_plane.driver) is not None:
        raise NotImplementedError("driver-registered control-plane probe lands in Phase 2")
    await context.control_planes.reconcile(control_plane.id)
    return {
        "id": str(control_plane.id),
        "driver": control_plane.driver,
        "reconciled": False,
        "reason": "no driver registered (Phase 1)",
    }


async def reconcile_environment(
    context: ReconciliationContext, environment: InferenceEnvironment
) -> dict[str, Any]:
    """Drive one environment toward its desired state.

    Phase 1: no live actions; records a no-op observation (``reconciled=False``).
    Phase 2: dispatch to the environment's bound driver's
    ``EnvironmentManager.reconcile_environment`` and persist the state change.
    """
    await context.environments.reconcile(environment.id)
    return {
        "id": str(environment.id),
        "reconciled": False,
        "reason": "no live actions in Phase 1",
    }


async def reconcile_deployment(
    context: ReconciliationContext, deployment: InferenceDeployment
) -> dict[str, Any]:
    """Drive one deployment toward its desired state.

    Phase 1: no live actions; records a no-op observation at the current
    generation so the row is no longer "pending observation."
    Phase 2: call the ``DeploymentOrchestrator`` ``validate``/``plan``/
    ``apply``/``observe``/``delete`` for the desired state and persist the
    resulting phase, replica counts, and endpoint status.
    """
    await context.deployments.reconcile(deployment.id)
    return {
        "id": str(deployment.id),
        "reconciled": False,
        "reason": "no live actions in Phase 1",
    }
