"""Backend-neutral contracts for the neutral inference domain.

These protocols are the seams through which future driver implementations
(Phase 2's ``RayDriver`` and friends) plug in.  They are deliberately small
and carry **no** Ray-specific types: concrete drivers import Ray only inside
their own modules and stay behind these contracts.

* :class:`InferenceDriver` — control-plane level capability discovery.
* :class:`DeploymentOrchestrator` — lifecycle of a single deployment.
* :class:`EnvironmentManager` — lifecycle of a whole environment.

The orchestration methods take a ``ctx`` (a ``ReconciliationContext`` defined
in ``reconciliation.py``) plus the relevant ORM record.  Phase 1 ships only
the contracts — no implementations are registered.
"""

from __future__ import annotations

from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.models.inference import (
    InferenceControlPlane,
    InferenceDeployment,
    InferenceEnvironment,
)
from llm_port_backend.services.inference.capabilities import CapabilityDocument
from llm_port_backend.services.inference.schemas import InferenceDeploymentSpecV1Alpha1


class InferenceDriver(Protocol):
    """Protocol shared by all backend control-plane drivers.

    A driver is identified by ``key`` (e.g. ``"ray"``) and is registered in
    :class:`DriverRegistry`.  Implementations must be importable without
    pulling their backend as a top-level dependency.
    """

    key: str

    async def probe(self, control_plane: InferenceControlPlane) -> dict:
        """
        Report connectivity/health for a control plane.

        :param control_plane: the target control plane record.
        :return: an arbitrary JSON-safe status document.
        """
        ...  # pragma: no cover

    async def capabilities(self, environment: InferenceEnvironment) -> CapabilityDocument:
        """
        Report the environment's capabilities.

        :param environment: the target environment record.
        :return: a structured capability document.
        """
        ...  # pragma: no cover


class DeploymentOrchestrator(Protocol):
    """Protocol for planning and applying a single deployment."""

    async def validate(self, session: AsyncSession, spec: InferenceDeploymentSpecV1Alpha1) -> None:
        """
        Validate a deployment spec against the current environment.

        :param session: active DB session (used for capability lookups only).
        :param spec: the candidate spec.
        :raises ValueError: if the spec is invalid for this environment.
        """
        ...  # pragma: no cover

    async def plan(self, session: AsyncSession, spec: InferenceDeploymentSpecV1Alpha1) -> dict:
        """
        Compile a spec into a backend-specific plan document.

        :param session: active DB session.
        :param spec: the deployment spec.
        :return: a JSON-safe plan dict (the Ray compiler's output).
        """
        ...  # pragma: no cover

    async def apply(self, session: AsyncSession, deployment: InferenceDeployment) -> None:
        """Apply the desired state for a deployment to the backend."""
        ...  # pragma: no cover

    async def observe(self, session: AsyncSession, deployment: InferenceDeployment) -> dict:
        """
        Observe the live state of a deployment.

        :return: a JSON-safe observation document.
        """
        ...  # pragma: no cover

    async def delete(self, session: AsyncSession, deployment: InferenceDeployment) -> None:
        """Tear down a deployment on the backend."""
        ...  # pragma: no cover


class EnvironmentManager(Protocol):
    """Protocol for reconciling an entire environment."""

    async def reconcile_environment(self, session: AsyncSession, environment: InferenceEnvironment) -> None:
        """Drive an environment toward its desired state."""
        ...  # pragma: no cover
