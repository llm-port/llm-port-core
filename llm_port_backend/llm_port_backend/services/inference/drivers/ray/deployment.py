r"""Ray deployment lifecycle orchestrator (Phase 3).

Drives an :class:`~llm_port_backend.db.models.inference.InferenceDeployment`
toward its desired state by compiling the stored spec into a Ray
``LLMServingArgs`` document (via :mod:`...drivers.ray.compiler`) and running
a *named* Ray Serve application on the environment's head node through the
node agent (via :class:`~...drivers.ray.client.RayClusterClient`).

Lifecycle (observed ``DeploymentPhase``):

    PENDING -> PREPARING -> APPLYING -> RUNNING
                                    \-> DEGRADED
                                    \-> FAILED
    STOPPED (desired stop converged)
    DELETED (desired delete converged)

Design constraints (verified against the Phase 2/3 code):

* **No Ray imports.** The backend stays Ray-free; the agent is the only
  component that imports ``ray.serve``.  This module only builds plain
  dictionaries and talks to the node agent through node commands.
* **Named-app semantics.** Every deployment owns a stable app name
  (``llmport-<deployment-id>``).  Serve deploys/updates exactly that
  application and leaves other named apps untouched; delete uses
  ``serve.delete(name)``.
* **Strict mutations, best-effort probes.**  RUN/DELETE_SERVE_APP go through
  the client's strict path (a terminal agent failure raises with the real
  cause and is recorded as a FAILED phase).  Status probes are best-effort:
  "not observed" never wedges the reconcile loop.
* **Act vs. Observe gating.**  The orchestrator checks whether the desired
  config hash and generation match the applied state. If already applied and
  healthy, it skips re-deployment and performs status observation only, avoiding
  wasteful rollout cycles.
* **Readiness convergence.**  After a successful run, the orchestrator polls
  the Serve status tier (``GET_RAY_SERVE_STATUS``) for the app's deployment to
  reach RUNNING with >=1 ready replica, then publishes the
  :class:`InferenceEndpoint`.  Readiness is NOT part of the same transaction
  as the mutation: if the probe is merely unobserved, the row stays pending
  and a later pass re-observes.

This module is imported lazily by :class:`~...drivers.ray.driver.RayDriver`
(like ``environment.py``) so importing the driver does not pull the
orchestrator's dependencies at registry time.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
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
    EnvironmentStatus,
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
from llm_port_backend.services.inference.drivers.ray.schemas import RayEnvironmentConfig

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


def _model_server_deployments(app: dict[str, Any]) -> dict[str, Any]:
    """The app's model-serving deployments (the ``OpenAiIngress`` excluded).

    ``build_openai_app`` creates one ``LLMServer:<model>`` deployment per
    model plus one ingress deployment; only the former are model replicas.
    """
    deployments = app.get("deployments") or {}
    servers = {k: v for k, v in deployments.items() if str(k).startswith("LLMServer")}
    if not servers:  # naming changed upstream: fall back to "not the ingress"
        servers = {k: v for k, v in deployments.items() if "Ingress" not in str(k)}
    return servers


def _replica_counts(app: dict[str, Any]) -> tuple[int, int]:
    """(ready, total) model replicas of *app*."""
    ready = total = 0
    for dep in _model_server_deployments(app).values():
        dep_ready = int(dep.get("num_replicas_ready") or 0)
        ready += dep_ready
        total += dep_ready + int(dep.get("num_replicas_pending") or 0)
    return ready, total


def _app_deployment_readiness(app: dict[str, Any], *, want_active: bool) -> tuple[bool, str | None]:
    """Decide whether a named app's deployment has converged.

    Returns ``(converged, reason)``.  ``converged`` is True when:

    * ``want_active`` and the app is RUNNING with >=1 ready model replica; or
    * not ``want_active`` and the app is absent (deleted) or RUNNING with
      zero ready model replicas (scaled to zero).

    Ray's ``ApplicationStatus`` is the app-level signal; the per-deployment
    status + ``replica_states`` (flattened by the agent into
    ``num_replicas_ready``) is the replica-level signal.  The ingress
    deployment is never counted as a model replica.
    """
    status = (app.get("status") or "").upper()
    message = app.get("message")
    ready, _total = _replica_counts(app)

    if want_active:
        if status != "RUNNING":
            # DEPLOYING / DEPLOY_FAILED / UNHEALTHY / DELETING / ...
            return False, f"application {status or 'UNKNOWN'}: {message}".strip()
        if ready < 1:
            dep_messages = [
                f"{name}={(dep.get('status') or '').upper()} {dep.get('message', '')}".strip()
                for name, dep in _model_server_deployments(app).items()
            ]
            detail = "; ".join(m for m in dep_messages if m) or "no ready replicas yet"
            return False, f"waiting for ready replicas: {detail}"
        return True, f"running ({ready} ready replica(s))"

    # want_active is False: converged when the app is gone or has no replicas.
    if not (app.get("deployments") or {}):
        return True, f"application {status or 'absent'}"
    if ready == 0:
        return True, "scaled to zero"
    return False, f"application still {status or 'active'} with {ready} ready replica(s)"


@dataclass
class _DeploymentFacts:
    """Resolved inputs for one reconcile pass (all None on failure)."""

    app_name: str
    environment: InferenceEnvironment | None = None
    head_node_id: uuid.UUID | None = None
    head_host: str | None = None
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
            # Honest no-op: nothing was observed live, so the row must stay
            # in the pending-observation queue (do NOT stamp observed_generation).
            self._observe(
                deployment, DeploymentPhase.PENDING,
                "node control service unavailable; no live action taken", False,
                observed={"reconciled": False, "reason": "no node control"},
                mark_observed=False,
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
                # Transient PENDING (e.g. no head yet) must stay in the
                # pending-observation queue; only terminal FAILED is observed.
                mark_observed=failed,
            )
            return

        app_name = facts.app_name

        # Desired stopped/deleted: converge by deleting the named app.
        if deployment.desired_state in (
            DeploymentDesiredState.STOPPED.value,
            DeploymentDesiredState.DELETED.value,
        ):
            is_stop = (deployment.desired_state == DeploymentDesiredState.STOPPED.value)
            converged_phase = DeploymentPhase.STOPPED if is_stop else DeploymentPhase.DELETED
            action_name = "stop" if is_stop else "delete"
            # Resolve failure is fine here: without a head there is
            # nothing remote to delete; mark stopped/deleted observed.
            if facts.head_node_id is None:
                self._observe(
                    deployment, converged_phase,
                    f"no head node bound; nothing to {action_name}", True,
                    observed={"reconciled": True, "action": f"no-op-{action_name}"},
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
            phase = converged_phase if ok else DeploymentPhase.FAILED
            self._observe(
                deployment, phase,
                f"serve.delete({app_name}) {'ok' if ok else 'failed'}",
                ok,
                observed={"reconciled": ok, "action": action_name, "app": app_name},
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

        # 3b. Gate on environment readiness (F40): a deployment never acts on
        # a cluster that is not up — that would fail RUN_SERVE_APP and park
        # the deployment in FAILED.  Stay PENDING (unobserved) and re-check.
        env_status = getattr(facts.environment, "status", None)
        if env_status != EnvironmentStatus.READY:
            self._observe(
                deployment, DeploymentPhase.PENDING,
                f"environment not ready ({env_status}); waiting", False,
                observed={"reconciled": False, "reason": "environment not ready"},
                mark_observed=False,
            )
            return

        client = self._client_for(node_control)

        # 4. Apply only when needed (act vs. observe): the config changed, the
        # last apply failed, or the app is missing from the cluster (e.g. the
        # cluster restarted).  Re-running an unchanged app restarts replicas.
        config_hash = hashlib.sha256(
            json.dumps(llm_serving_args, sort_keys=True).encode()
        ).hexdigest()
        applied_hash = (deployment.observed_status_json or {}).get("applied_config_hash")
        need_apply = (applied_hash != config_hash) or (deployment.phase == DeploymentPhase.FAILED.value)
        if not need_apply:
            serve_status = await self._probe_serve(client, facts.head_node_id, app_name)
            if serve_status is not None and serve_status.alive:
                current = _serve_app_entry(serve_status, app_name)
                if current is None or (current.get("status") or "").upper() == "DEPLOY_FAILED":
                    need_apply = True
            # An unobserved probe (alive=False) is not proof of absence: do
            # not redeploy on it — readiness below keeps the row pending.

        run_result: dict[str, Any] = {}
        if need_apply:
            try:
                run_result = await client.run_serve_app(
                    head_node_id=facts.head_node_id,
                    app_name=app_name,
                    llm_serving_args=llm_serving_args,
                    serve_options=self._serve_options(facts),
                    idem_prefix=f"inference-dep:run:{deployment.id}:{deployment.generation}",
                )
            except RayCommandError as exc:
                if exc.error_code == "command_timeout":
                    # Outcome unknown, not a failure: the next pass resumes the
                    # same in-flight command through its idempotency key.
                    self._observe(
                        deployment, DeploymentPhase.APPLYING,
                        f"serve.run({app_name}) submitted; result not observed yet", False,
                        observed={"reconciled": False, "reason": "apply-unobserved"},
                        mark_observed=False,
                    )
                    return
                self._observe(
                    deployment, DeploymentPhase.FAILED,
                    f"serve.run({app_name}) failed: {exc.detail}", False,
                    observed={"reconciled": False, "reason": "apply", "error_code": exc.error_code},
                )
                return
        else:
            run_result = (deployment.observed_status_json or {}).get("observation", {}).get(
                "run", {"app": app_name, "deployed": True, "cached": True}
            )
            log.info(
                "Deployment %s config_hash %s already applied; observing only",
                deployment.id, config_hash[:8],
            )

        # 5. Observe readiness (best-effort; unobserved stays pending).
        observed, ready, total = await self._poll_readiness(
            client, head_node_id=facts.head_node_id, app_name=app_name,
        )

        app_status = ((observed or {}).get("status") or "").upper()
        if app_status == "DEPLOY_FAILED":
            # Terminal on Ray's side (replica startup exhausted its retries):
            # surface Ray's message.  Out of the queue until the spec changes
            # or a reconcile is requested (phase FAILED forces a re-apply).
            self._observe(
                deployment, DeploymentPhase.FAILED,
                f"application DEPLOY_FAILED: {(observed or {}).get('message') or ''}".strip(), False,
                observed={"reconciled": False, "reason": "deploy-failed", "app": app_name},
                config_hash=config_hash,
            )
            return
        if app_status == "UNHEALTHY":
            self._observe(
                deployment, DeploymentPhase.DEGRADED,
                f"application UNHEALTHY: {(observed or {}).get('message') or ''}".strip(), False,
                observed={"reconciled": False, "reason": "unhealthy", "app": app_name},
                mark_observed=False,
                ready_replicas=ready,
                total_replicas=total,
                config_hash=config_hash,
            )
            return

        if not observed:
            # Mutation succeeded but we can't see the app yet: leave the row
            # lagging (do NOT mark observed) so a later pass re-observes.  The
            # applied config hash is recorded, so that pass only observes —
            # re-running an unchanged app would restart its replicas.
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
                config_hash=config_hash,
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
                config_hash=config_hash,
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
            config_hash=config_hash,
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
        facts.head_host = await self._host_of(session, head)

        model = await self._model_of(session, deployment.model_id)
        if model is None:
            return _ResolveError("model not found", failed=True)
        facts.model = model
        facts.model_source = getattr(model, "source", "huggingface") or "huggingface"
        facts.hf_repo_id = model.hf_repo_id
        facts.hf_revision = model.hf_revision

        availability_root = await self._availability_root(
            session, model.id, facts.head_node_id, environment=environment
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
        self,
        session,
        model_id: uuid.UUID,
        head_node_id: uuid.UUID,
        *,
        environment: InferenceEnvironment | None = None,
    ) -> str | None:
        """Return the per-node synced artifact root if the artifact is READY
        on the head node and all environment members; else ``None`` (falls back to remote HF) (F29).
        """
        try:
            row = await ModelAvailabilityDAO(session).get(model_id, head_node_id)
        except Exception:
            row = None
        if row is None or row.status != ModelAvailabilityStatus.READY.value or not row.root_path:
            return None

        if environment is not None:
            try:
                res = await session.execute(
                    select(InferenceEnvironmentNode).where(
                        InferenceEnvironmentNode.environment_id == environment.id
                    )
                )
                for member in res.scalars().all():
                    if member.node_id == head_node_id:
                        continue
                    m_row = await ModelAvailabilityDAO(session).get(model_id, member.node_id)
                    if m_row is None or m_row.status != ModelAvailabilityStatus.READY.value:
                        log.info(
                            "Model %s not ready on member %s; falling back to remote source (F29)",
                            model_id,
                            member.node_id,
                        )
                        return None
            except Exception as e:
                log.warning("Failed checking model availability across members: %s", e)

        return row.root_path

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
        ready = total = 0
        while True:
            status = await self._probe_serve(client, head_node_id, app_name)
            entry = _serve_app_entry(status, app_name)
            if entry is not None:
                last = entry
                ready, total = _replica_counts(entry)
                app_status = (entry.get("status") or "").upper()
                if app_status == "RUNNING" and ready >= 1:
                    return entry, ready, total
                if app_status in ("DEPLOY_FAILED", "UNHEALTHY"):
                    return entry, ready, total  # decided by the caller; no point polling

            if asyncio.get_event_loop().time() > deadline:
                return last, ready, total
            await asyncio.sleep(_READINESS_POLL_SEC)

    async def _probe_serve(
        self, client: RayClusterClient, head_node_id: uuid.UUID, app_name: str
    ) -> Any:
        """Best-effort Serve status probe; ``None`` when the probe itself errors."""
        try:
            return await client.probe_serve(head_node_id=head_node_id)
        except Exception as exc:  # noqa: BLE001 - probe never wedges the loop
            log.warning("probe_serve for %s failed: %s", app_name, exc)
            return None

    def _environment_config(self, facts: _DeploymentFacts) -> RayEnvironmentConfig:
        raw = (facts.environment.config_json or {}) if facts.environment else {}
        try:
            return RayEnvironmentConfig.model_validate(raw)
        except Exception:  # noqa: BLE001 - a bad env config must not break deploys
            return RayEnvironmentConfig()

    def _serve_options(self, facts: _DeploymentFacts) -> dict[str, Any]:
        """Serve HTTP proxy placement for the environment (F13).

        ``HeadOnly`` binds the single proxy to the head's cluster IP — the
        address the endpoint is published under.  ``EveryNode`` applies one
        ``host`` to every node's proxy, so it must be a wildcard.
        """
        cfg = self._environment_config(facts)
        location = cfg.serve_proxy_location or "HeadOnly"
        if cfg.serve_http_host:
            host = cfg.serve_http_host
        elif location == "HeadOnly" and facts.head_host:
            host = facts.head_host
        else:
            host = "0.0.0.0"  # noqa: S104 - wildcard required for per-node proxies
        return {
            "proxy_location": location,
            "http_options": {"host": host, "port": int(cfg.serve_http_port)},
        }

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
        # The OpenAI ingress on the head's Serve proxy is the single published
        # upstream; the app is mounted at route prefix ``/<app_name>`` (the
        # agent sets it), so the base URL is <proxy>/<app_name><path>.
        http = self._serve_options(facts)["http_options"]
        host = http["host"]
        if host in ("0.0.0.0", "::"):  # noqa: S104 - wildcard bind: publish the head's address
            host = facts.head_host or (facts.environment.address if facts.environment else None) or "head"
        address = f"http://{host}:{http['port']}/{app_name}"

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
            "base_url": f"{address}{path}",
        }
        if target is None:
            await endpoint_dao.create(
                deployment.id,
                name="openai",
                address=address,
                path=path,
                status=EndpointStatus.PUBLISHED,
            )
            target = next(
                e for e in await endpoint_dao.list_for_deployment(deployment.id) if e.name == "openai"
            )
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
        config_hash: str | None = None,
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
        if config_hash:
            observed_status["applied_config_hash"] = config_hash
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
