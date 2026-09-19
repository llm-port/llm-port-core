"""Ray deployment lifecycle orchestrator (Phase 3).

Drives an :class:`~llm_port_backend.db.models.inference.InferenceDeployment`
toward its desired state by compiling the stored spec into a Ray
``LLMServingArgs`` document (via :mod:`...drivers.ray.compiler`) and running
a *named* Ray Serve application on the environment's head node through the
node agent (via :class:`~...drivers.ray.client.RayClusterClient`).

Lifecycle (observed ``DeploymentPhase``):

    VALIDATING -> PLANNED -> APPLYING -> RUNNING
                                    \\-> FAILED
    DELETED (desired delete/stop converged)

Design constraints (verified against the Phase 2 code):

* **No Ray imports.** The backend stays Ray-free; the agent is the only
  component that imports ``ray.serve``.  This module only builds plain
  dictionaries and talks to the node agent through node commands.
* **Named-app semantics.** Every deployment owns a stable app name
  (``llmport-<deployment-id>``).  ``serve.run(app, name=...)`` deploys/updates
  exactly that application and leaves other named apps untouched; delete uses
  ``serve.delete(name)``.
* **Strict mutations, best-effort probes.**  RUN/DELETE_SERVE_APP go through
  the client's strict path (a terminal agent failure raises with the real
  cause and is recorded as a FAILED phase).  Status probes are best-effort:
  "not observed" never wedges the reconcile loop.
* **Idempotency by (generation, app).**  The RUN idempotency key embeds the
  row's ``generation``, so a spec change re-keys and re-dispatches (an
  in-flight command with a *different* key is never deduped away), while
  re-runs within the same generation are cheap no-ops (Serve updates the same
  named app; a terminal key is retired so a later retry can proceed).
* **Readiness convergence.**  After a successful run, the orchestrator polls
  the Serve status tier (``GET_RAY_SERVE_STATUS``) for the app's deployment to
  reach RUNNING with >=1 ready replica, then publishes the
  :class:`InferenceEndpoint`.  Readiness is NOT part of the same transaction
  as the mutation: if the probe is merely unobserved, the row stays pending
  (generation un-observed) and a later pass re-observes — the named app is
  already deployed, so this converges without re-running.

This module is imported lazily by :class:`~...drivers.ray.driver.RayDriver`
(like ``environment.py``) so importing the driver does not pull the
orchestrator's dependencies at registry time.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from llm_port_backend.db.dao.inference_dao import (
    EndpointDAO,
    ModelAvailabilityDAO,
)
from llm_port_backend.db.models.inference import (
    DeploymentDesiredState,
    DeploymentPhase,
    EndpointStatus,
    InferenceDeployment,
    InferenceEnvironment,
    InferenceEnvironmentNode,
    ModelAvailabilityStatus,
)
from llm_port_backend.db.models.llm import LLMModel
from llm_port_backend.db.models.node_control import InfraNode
from llm_port_backend.services.inference.drivers.ray.client import (
    RayClusterClient,
    RayCommandError,
)
from llm_port_backend.services.inference.drivers.ray.compiler import (
    ArtifactResolutionError,
    compile_deployment,
)

if TYPE_CHECKING:  # pragma: no cover - import-time only
    from llm_port_backend.services.nodes.service import NodeControlService

log = logging.getLogger(__name__)

# Readiness poll: how often to re-probe Serve, and the per-pass budget before
# we give up *this pass* and leave the row pending for the next pass.  The
# agent-side load (weights download + engine init) dominates; the reconcile
# loop's 30s cadence re-enters the row until readiness is observed.
_READINESS_POLL_SEC = 5.0
_READINESS_PASS_BUDGET_SEC = 60.0


def app_name_for(deployment: InferenceDeployment) -> str:
    """Stable, collision-free Ray Serve app name for a deployment.

    The full deployment UUID is used (not the human name, which may collide
    and is mutable) so rename never re-targets the live app.  Ray app names
    allow ``[a-zA-Z0-9_-]``; the UUID hex + hyphens satisfy that directly.
    """
    return f"llmport-{deployment.id}"


def _serve_app_entry(status: Any, name: str) -> dict[str, Any] | None:
    """Extract one app's entry from a parsed ``RayServeStatus`` apps map."""
    if status is None:
        return None
    apps = getattr(status, "apps", None) or {}
    app = apps.get(name)
    if not app or not isinstance(app, dict):
        return None
    return app


def _app_deployment_readiness(app: dict[str, Any], *, want_active: bool) -> tuple[bool, str | None]:
    """Decide whether a named app's deployment has converged.

    Returns ``(converged, reason)``.  ``converged`` is True when:

    * ``want_active`` and the app is RUNNING with a deployment whose status is
      RUNNING/UP and which reports >=1 ready replica; or
    * not ``want_active`` and the app is absent (deleted) or RUNNING with
      zero ready replicas (scaled to zero).

    Ray's ``ApplicationStatus`` is the app-level signal; the per-deployment
    status + ``replica_states`` (flattened by the agent into
    ``num_replicas_ready``) is the replica-level signal.
    """
    deployments = app.get("deployments") or {}
    status = (app.get("status") or "").upper()
    message = app.get("message")

    if want_active:
        if status != "RUNNING":
            # DEPLOYING / RESTARTING / DOWN / FAILED / ...
            return False, f"application {status or 'UNKNOWN'}: {message}".strip()
        ready = 0
        dep_messages = []
        for dep in deployments.values():
            dep_status = (dep.get("status") or "").upper()
            ready += int(dep.get("num_replicas_ready") or 0)
            if dep_status not in ("RUNNING", "UP"):
                dep_messages.append(
                    f"{dep.get('name', '?')}={dep_status} {dep.get('message', '')}".strip()
                )
        if ready < 1:
            detail = "; ".join(dep for dep in dep_messages if dep) or "no ready replicas yet"
            return False, f"waiting for ready replicas: {detail}"
        return True, f"running ({ready} ready replica(s))"

    # want_active is False: converged when the app is gone or has no replicas.
    if not deployments:
        return True, f"application {status or 'absent'}"
    ready = sum(int(dep.get("num_replicas_ready") or 0) for dep in deployments.values())
    if ready == 0:
        return True, "scaled to zero"
    return False, f"application still {status or 'active'} with {ready} ready replica(s)"


@dataclass
class _DeploymentFacts:
    """Resolved inputs for one reconcile pass (all None on failure)."""

    app_name: str
    environment: InferenceEnvironment | None = None
    head_node_id: uuid.UUID | None = None
    model: LLMModel | None = None
    model_source: str | None = None
    hf_repo_id: str | None = None
    hf_revision: str | None = None
    availability_root_path: str | None = None
    spec_data: dict[str, Any] | None = None
    total_replicas: int = 0


@dataclass
class _ResolveError:
    detail: str
    failed: bool = True  # False => transient (wait for a later pass)


class RayDeploymentManager:
    """Drives an InferenceDeployment toward its desired state via node commands."""

    def __init__(self) -> None:
        # Stateless: the NodeControlService is handed in per pass so a single
        # instance can be reused across sessions/deployments (mirrors
        # ``RayEnvironmentManager``).
        pass

    # ------------------------------------------------------------------
    # Public entry point (reconcile seam)
    # ------------------------------------------------------------------

    async def reconcile_deployment(
        self,
        session: Any,
        deployment: InferenceDeployment,
        *,
        node_control: "NodeControlService | None" = None,
    ) -> None:
        """Drive *deployment* toward its desired state.

        Mutates *session* (command rows + observed state) but does not commit —
        the caller owns the transaction boundary.  Every terminal outcome is
        recorded via ``set_observed`` so the row is honest and the
        pending-observation predicate behaves.
        """
        log.info(
            "Reconciling Ray deployment %s desired=%s phase=%s",
            deployment.id, deployment.desired_state, deployment.phase,
        )
        if node_control is None:
            self._observe(
                deployment, DeploymentPhase.PENDING,
                "node control service unavailable; no live action taken", False,
                observed={"reconciled": False, "reason": "no node control"},
            )
            return

        facts = await self._resolve(session, deployment)

        # Unresolvable inputs are terminal for the active path.
        if isinstance(facts, _ResolveError):
            failed = facts.failed
            phase = DeploymentPhase.FAILED if failed else DeploymentPhase.PENDING
            self._observe(
                deployment, phase, facts.detail, False,
                observed={"reconciled": not failed, "reason": facts.detail},
            )
            return

        app_name = facts.app_name

        # Desired stopped/deleted: converge by deleting the named app.
        if deployment.desired_state in (
            DeploymentDesiredState.STOPPED.value,
            DeploymentDesiredState.DELETED.value,
        ):
            # Resolve failure is fine here: without a head there is
            # nothing remote to delete; mark stopped/deleted observed.
            if facts.head_node_id is None:
                self._observe(
                    deployment, DeploymentPhase.DELETED,
                    "no head node bound; nothing to delete", True,
                    observed={"reconciled": True, "action": "no-op-delete"},
                )
                return
            try:
                result = await self._client_for(node_control).delete_serve_app(
                    head_node_id=facts.head_node_id,
                    app_name=app_name,
                )
                ok = bool(result.get("deleted"))
            except RayCommandError as exc:
                # Deleting a not-yet-deployed app is a common first pass
                # after a never-applied active->deleted transition; treat as
                # converged (the desired absence already holds locally).
                log.info("delete_serve_app(%s) on %s: %s", app_name, facts.head_node_id, exc.detail)
                ok = True
            phase = DeploymentPhase.DELETED if ok else DeploymentPhase.FAILED
            self._observe(
                deployment, phase,
                f"serve.delete({app_name}) {'ok' if ok else 'failed'}",
                ok,
                observed={"reconciled": ok, "action": "delete", "app": app_name},
            )
            if ok:
                await self._retire_endpoints(session, deployment.id)
            return

        # ------------------------------------------------------------------
        # Desired ACTIVE
        # ------------------------------------------------------------------

        # 2. Plan (compile the spec into a LLMServingArgs document).
        try:
            llm_serving_args = self._compile(facts)
        except ArtifactResolutionError as exc:
            self._observe(
                deployment, DeploymentPhase.FAILED, f"artifact resolution failed: {exc}",
                False, observed={"reconciled": False, "reason": "resolve-artifact"},
            )
            return
        except ValueError as exc:
            self._observe(
                deployment, DeploymentPhase.FAILED, f"unsupported spec for Ray: {exc}",
                False, observed={"reconciled": False, "reason": "compile"},
            )
            return
        except Exception as exc:  # noqa: BLE001 - any compile failure is a FAILED phase
            self._observe(
                deployment, DeploymentPhase.FAILED, f"compile failed: {exc}",
                False, observed={"reconciled": False, "reason": "compile"},
            )
            return

        # 3. Gate on the environment (head must exist).
        if facts.head_node_id is None:
            self._observe(
                deployment, DeploymentPhase.PENDING,
                "no head node bound to environment; waiting", False,
                observed={"reconciled": False, "reason": "no head"},
            )
            return

        client = self._client_for(node_control)

        # 4. Apply (strict run of the named app).
        try:
            run_result = await client.run_serve_app(
                head_node_id=facts.head_node_id,
                app_name=app_name,
                llm_serving_args=llm_serving_args,
                idem_prefix=f"inference-dep:run:{deployment.id}:{deployment.generation}",
            )
        except RayCommandError as exc:
            self._observe(
                deployment, DeploymentPhase.FAILED,
                f"serve.run({app_name}) failed: {exc.detail}", False,
                observed={"reconciled": False, "reason": "apply", "error_code": exc.error_code},
            )
            return

        # 5. Observe readiness (best-effort; unobserved stays pending).
        observed, ready, total = await self._poll_readiness(
            client, head_node_id=facts.head_node_id, app_name=app_name,
        )

        if not observed:
            # Mutation succeeded but we can't see the app yet: leave the row
            # lagging (do NOT mark observed) so a later pass re-observes.  The
            # named app is already deployed; re-running it within the same
            # generation is a cheap idempotent no-op update (same idempotency
            # key, terminal key retired, serve.run of identical args).
            self._observe(
                deployment, DeploymentPhase.APPLYING,
                "serve.run accepted; waiting for readiness observation", False,
                observed={
                    "reconciled": False,
                    "action": "run",
                    "app": app_name,
                    "run": run_result,
                },
                mark_observed=False,
            )
            return

        converged, reason = _app_deployment_readiness(observed, want_active=True)
        if not converged:
            self._observe(
                deployment, DeploymentPhase.APPLYING,
                f"app {reason}", False,
                observed={
                    "reconciled": False,
                    "action": "waiting",
                    "app": app_name,
                    "app_status": (observed or {}).get("status"),
                },
                mark_observed=False,
            )
            return

        # 6. Converged: mark RUNNING, observed, and publish the endpoint.
        self._observe(
            deployment, DeploymentPhase.RUNNING, reason, True,
            observed={
                "reconciled": True,
                "action": "run",
                "app": app_name,
                "run": run_result,
                "ready_replicas": ready,
            },
            ready_replicas=ready,
            total_replicas=total,
        )
        await self._publish_endpoint(
            session, deployment, facts, app_name, ready_replicas=ready,
        )

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    async def _resolve(self, session, deployment: InferenceDeployment) -> "_DeploymentFacts | _ResolveError":
        facts = _DeploymentFacts(app_name=app_name_for(deployment))
        try:
            facts.spec_data = dict(deployment.spec_json or {})
            facts.total_replicas = self._plan_total_replicas(facts.spec_data)
        except Exception as exc:  # noqa: BLE001 - malformed spec is a terminal 4xx-ish failure
            return _ResolveError(f"invalid deployment spec: {exc}", failed=True)

        environment = await self._environment_of(session, deployment.environment_id)
        if environment is None:
            return _ResolveError("environment not found", failed=True)
        facts.environment = environment

        head = await self._head_node_of(session, environment)
        if head is None:
            # Transient: the environment may not have converged yet.
            return _ResolveError(
                "environment has no head node", failed=False
            )
        facts.head_node_id = head

        model = await self._model_of(session, deployment.model_id)
        if model is None:
            return _ResolveError("model not found", failed=True)
        facts.model = model
        facts.model_source = getattr(model, "source", "huggingface") or "huggingface"
        facts.hf_repo_id = model.hf_repo_id
        facts.hf_revision = model.hf_revision

        availability_root = await self._availability_root(
            session, model.id, facts.head_node_id
        )
        facts.availability_root_path = availability_root
        return facts

    async def _environment_of(self, session, environment_id: uuid.UUID) -> InferenceEnvironment | None:
        try:
            return await session.get(InferenceEnvironment, environment_id)
        except Exception:  # pragma: no cover - defensive
            return None

    async def _head_node_of(
        self, session, environment: InferenceEnvironment
    ) -> uuid.UUID | None:
        """Resolve the head node of *environment* (mirrors environment manager)."""
        result = await session.execute(
            select(InferenceEnvironmentNode).where(
                InferenceEnvironmentNode.environment_id == environment.id
            )
        )
        members = list(result.scalars().all())
        if environment.head_node_id is not None:
            preferred = next(
                (n for n in members if n.node_id == environment.head_node_id), None
            )
            if preferred is not None:
                return preferred.node_id
        for member in members:
            role = (member.role or "").lower()
            if role == "head":
                return member.node_id
        # Fall back to the explicit column even without a membership row.
        return environment.head_node_id

    async def _host_of(self, session, node_id: uuid.UUID) -> str | None:
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

    async def _model_of(self, session, model_id: uuid.UUID) -> LLMModel | None:
        try:
            return await session.get(LLMModel, model_id)
        except Exception:  # pragma: no cover
            return None

    async def _availability_root(
        self, session, model_id: uuid.UUID, head_node_id: uuid.UUID
    ) -> str | None:
        """Return the per-node synced artifact root if the artifact is READY
        on the head node; else ``None`` (the engine falls back to a download).
        """
        try:
            row = await ModelAvailabilityDAO(session).get(model_id, head_node_id)
        except Exception:
            row = None
        if row is None:
            return None
        if row.status == ModelAvailabilityStatus.READY.value and row.root_path:
            return row.root_path
        return None

    # ------------------------------------------------------------------
    # Plan (compile)
    # ------------------------------------------------------------------

    def _plan_total_replicas(self, spec_data: dict[str, Any]) -> int:
        scale = (spec_data or {}).get("scale") or {}
        autoscale = scale.get("autoscale")
        if autoscale:
            return int(autoscale.get("min_replicas") or 1)
        return int(scale.get("replicas") or 1)

    def _compile(self, facts: _DeploymentFacts) -> dict[str, Any]:
        """Compile the spec into ``LLMServingArgs``.

        :raises: :class:`ArtifactResolutionError` / ``ValueError`` — translated
            to a FAILED phase by the caller.
        """
        return compile_deployment(
            spec_data=facts.spec_data,
            model_display_name=facts.model.display_name,
            model_source=facts.model_source,
            hf_repo_id=facts.hf_repo_id,
            hf_revision=facts.hf_revision,
            availability_root_path=facts.availability_root_path,
            desired_state="active",
        )

    # ------------------------------------------------------------------
    # Readiness observation (best-effort)
    # ------------------------------------------------------------------

    async def _poll_readiness(
        self, client: RayClusterClient, *, head_node_id: uuid.UUID, app_name: str
    ) -> "tuple[dict[str, Any] | None, int, int]":
        """Poll the Serve status tier for *app_name*; return (entry, ready, total).

        ``entry`` is the per-app dict (or ``None`` if never observed).  The
        per-pass budget bounds how long a single reconcile pass blocks on the
        probe; on expiry the caller leaves the row pending for a later pass.
        """
        deadline = asyncio.get_event_loop().time() + _READINESS_PASS_BUDGET_SEC
        last: dict[str, Any] | None = None
        while True:
            try:
                status = await client.probe_serve(head_node_id=head_node_id)
            except Exception as exc:  # noqa: BLE001 - probe never wedges the loop
                log.warning("probe_serve for %s failed: %s", app_name, exc)
                status = None
            entry = _serve_app_entry(status, app_name)
            if entry is not None:
                last = entry

            # Converged? (RUNNING with >=1 ready, or the app is already fine)
            ready = 0
            total = 0
            if entry is not None:
                for dep in (entry.get("deployments") or {}).values():
                    ready += int(dep.get("num_replicas_ready") or 0)
                    pending = int(dep.get("num_replicas_pending") or 0)
                    total += ready + pending
                app_status = (entry.get("status") or "").upper()
                if app_status == "RUNNING":
                    return entry, ready, total

            if asyncio.get_event_loop().time() > deadline:
                return last, ready, total
            await asyncio.sleep(_READINESS_POLL_SEC)

    # ------------------------------------------------------------------
    # Endpoints
    # ------------------------------------------------------------------

    async def _publish_endpoint(
        self,
        session,
        deployment: InferenceDeployment,
        facts: _DeploymentFacts,
        app_name: str,
        *,
        ready_replicas: int,
    ) -> None:
        """Publish (or refresh) the logical OpenAI endpoint for a RUNNING app."""
        endpoint_dao: EndpointDAO = EndpointDAO(session)
        path = str(((facts.spec_data or {}).get("service") or {}).get("path") or "/v1")
        # Best-effort: the OpenAI ingress on the head node's Serve proxy is the
        # single published upstream.  We record the head host + app name so the
        # gateway can route; the exact proxy URL is an environment concern.
        host = (
            await self._host_of(session, facts.head_node_id)
            if facts.head_node_id is not None
            else None
        ) or (facts.environment.address if facts.environment else None) or "head"
        address = f"{host}/{app_name}"

        existing = await endpoint_dao.list_for_deployment(deployment.id)
        target = next(
            (e for e in existing if e.name == "openai"), None
        )
        published = {
            "app": app_name,
            "driver": "ray",
            "model": facts.model.display_name,
            "ready_replicas": ready_replicas,
            "path": path,
        }
        if target is None:
            await endpoint_dao.create(
                deployment.id,
                name="openai",
                address=address,
                path=path,
                status=EndpointStatus.PUBLISHED,
            )
            target = (await endpoint_dao.list_for_deployment(deployment.id))[0]
        await endpoint_dao.update(
            target.id,
            address=address,
            status=EndpointStatus.PUBLISHED,
            status_message=f"running on {app_name} ({ready_replicas} ready)",
            published=published,
        )

    async def _retire_endpoints(self, session, deployment_id: uuid.UUID) -> None:
        """Mark the deployment's endpoints RETIRED on a converged delete/stop."""
        endpoint_dao: EndpointDAO = EndpointDAO(session)
        for endpoint in await endpoint_dao.list_for_deployment(deployment_id):
            await endpoint_dao.update(
                endpoint.id,
                status=EndpointStatus.RETIRED,
                status_message="deployment converged to deleted/stopped",
            )

    # ------------------------------------------------------------------
    # Observation
    # ------------------------------------------------------------------

    def _client_for(self, node_control: "NodeControlService") -> RayClusterClient:
        return RayClusterClient(node_control)

    def _observe(
        self,
        deployment: InferenceDeployment,
        phase: DeploymentPhase,
        phase_message: str,
        reconciled: bool,
        *,
        observed: dict[str, Any] | None = None,
        mark_observed: bool = True,
        ready_replicas: int | None = None,
        total_replicas: int | None = None,
    ) -> None:
        """Record the pass's observation on the row (attribute mutation only).

        Mirrors ``RayEnvironmentManager._observe``: the manager owns the row's
        observed state and the caller commits.  ``mark_observed=False`` keeps
        ``observed_generation`` behind ``generation`` so the row stays in the
        pending queue for a later pass (mutation succeeded but readiness not
        yet observed — it converges on the next pass).
        """
        payload = dict(observed or {})
        payload.setdefault("reconciled", bool(reconciled))
        payload.setdefault("driver", "ray")
        observed_status = dict(deployment.observed_status_json or {})
        observed_status["observation"] = payload
        if mark_observed:
            deployment.observed_generation = deployment.generation
        deployment.phase = phase.value
        deployment.phase_message = phase_message
        deployment.observed_status_json = observed_status
        if ready_replicas is not None:
            deployment.ready_replicas = ready_replicas
        if total_replicas is not None:
            deployment.total_replicas = total_replicas


__all__ = [
    "RayDeploymentManager",
    "app_name_for",
]
