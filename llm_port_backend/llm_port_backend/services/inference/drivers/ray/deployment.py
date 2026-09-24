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
import re
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from llm_port_backend.db.dao.inference_dao import (
    EndpointDAO,
)
from llm_port_backend.db.models.inference import (
    DeploymentDesiredState,
    DeploymentPhase,
    EndpointStatus,
    EnvironmentStatus,
    InferenceDeployment,
    InferenceEnvironment,
    InferenceEnvironmentNode,
)
from llm_port_backend.db.models.llm import LLMModel
from llm_port_backend.db.models.node_control import InfraNode, NodeHealthStatus
from llm_port_backend.services.inference.artifacts import (
    ArtifactReadiness,
    ModelArtifactCoordinator,
)
from llm_port_backend.services.inference.bundles import (
    node_and_bundle_for,
    default_bundle_registry,
    translate_host_path_to_container,
)
from llm_port_backend.services.inference.drivers.ray.client import (
    RayClusterClient,
    RayCommandError,
)
from llm_port_backend.services.inference.drivers.ray.commands import NodeCommandGateway
from llm_port_backend.services.inference.drivers.ray.compiler import (
    ArtifactResolutionError,
    compile_deployment,
)
from llm_port_backend.services.inference.drivers.ray.schemas import RayEnvironmentConfig

if TYPE_CHECKING:  # pragma: no cover - import-time only
    from llm_port_backend.services.nodes.service import NodeControlService


def _gateway(node_control: "NodeControlService | NodeCommandGateway") -> NodeCommandGateway:
    if isinstance(node_control, NodeCommandGateway):
        return node_control
    return NodeCommandGateway(node_control)

log = logging.getLogger(__name__)

#: Bind every Serve proxy to all interfaces.  The backend cannot know which
#: node Ray will place a proxy on, so this is the only address that is valid
#: on all of them.
_WILDCARD_BIND = "0.0.0.0"  # noqa: S104 - see _serve_options
_WILDCARD_BINDS = frozenset({_WILDCARD_BIND, "::", ""})

# Must match the name the environment manager starts the container under.
_RUNTIME_CONTAINER_NAME = "llm-port-ray-runtime"

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


def _cluster_head(environment: Any) -> str | None:
    """Ray's id for the cluster's live head, from the last cluster observation.

    It changes whenever a head is started again -- a cluster re-formed after
    its head died, a head machine that rebooted -- and a new head starts with
    no Serve applications. Taken from Ray rather than from LLM.Port's own
    bookkeeping so a restart that happened any other way is noticed too.
    """
    observed = getattr(environment, "observed_status_json", None) or {}
    cluster = observed.get("cluster") or {}
    if not cluster.get("alive"):
        return None
    for node in cluster.get("nodes") or []:
        if not isinstance(node, dict) or not node.get("is_head"):
            continue
        if node.get("alive") is False or str(node.get("state", "ALIVE")).upper() not in {"ALIVE", "UP", "ACTIVE"}:
            continue
        return str(node.get("node_id") or "") or None
    return None


def _no_serve_instance(detail: str | None) -> bool:
    """Whether a Serve probe's error says the cluster has no Serve running.

    That is what a freshly started head answers: the cluster is there and
    reachable, and Serve has simply not been started on it -- an absent
    application, as opposed to a probe that could not see.
    """
    return "no serve instance" in (detail or "").lower()


def _reported_route_prefix(deployment: Any) -> str | None:
    """The route prefix the agent said it mounted the app on, if it said.

    Recorded in ``observed_status.observation.run`` by the apply step.  An
    older agent that reports none leaves this ``None`` and the caller falls
    back to the documented convention.
    """
    observed = getattr(deployment, "observed_status_json", None) or {}
    run = ((observed.get("observation") or {}).get("run") or {})
    prefix = run.get("route_prefix")
    if not isinstance(prefix, str) or not prefix.startswith("/"):
        return None
    # "/" means the app is at the root; the endpoint path is appended as-is.
    return "" if prefix == "/" else prefix.rstrip("/")


def _failure_detail(app: dict[str, Any] | None, *, limit: int = 600) -> str:
    """The reason an application failed, in the operator's status message.

    Ray puts the useful text on the *deployment* inside the application, not
    on the application itself, whose ``message`` is usually empty.  Reading
    only the outer one produced "application DEPLOY_FAILED:" with nothing
    after the colon -- a failure with no reason, for a problem whose reason
    was sitting one level down:

        ValueError: Free memory on device cuda:0 (109.83/121.69 GiB) on
        startup is less than desired GPU memory utilization (0.92, ...)
    """
    if not app:
        return ""
    outer = str(app.get("message") or "").strip()
    inner = ""
    for entry in (app.get("deployments") or {}).values():
        if not isinstance(entry, dict):
            continue
        text = str(entry.get("message") or "").strip()
        if text and (entry.get("status") or "").upper() in ("DEPLOY_FAILED", "UNHEALTHY"):
            inner = text
            break
        if text and not inner:
            inner = text
    detail = inner or outer
    cause = _traceback_cause(detail)
    if cause is not None:
        # A replica that failed to start: Ray's first line is only "failed to
        # start 3 times in a row ... Error:", then frames, and the reason is
        # the traceback's *last* exception line. Cut at the limit, the message
        # ended in "return self. ..." and never reached "Cannot find an
        # appropriate cached snapshot folder" -- the one line that mattered.
        headline = detail.splitlines()[0].strip()
        if headline.endswith("Error:"):
            headline = headline[: -len("Error:")].rstrip()
        detail = f"{headline} {cause}" if cause not in headline else headline
    if len(detail) > limit:
        detail = detail[:limit].rstrip() + " ..."
    return detail


_EXCEPTION_LINE = re.compile(r"^\s*((?:[A-Za-z_]\w*\.)*[A-Za-z_]\w*(?:Error|Exception|Exit|Timeout)\w*)(?:\([^)]*\))?: (\S.*)$")


def _traceback_cause(text: str) -> str | None:
    """The last exception line of a traceback in *text*, or None when there is none."""
    if "Traceback" not in text and '\n  File "' not in text:
        return None
    for line in reversed(text.splitlines()):
        match = _EXCEPTION_LINE.match(line)
        if match:
            return f"{match.group(1)}: {match.group(2).strip()}"
    return None


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
            detail = message or _failure_detail(app) or ""
            if status == "DEPLOYING" and not detail:
                return False, "Starting the first copy."
            return False, f"Ray reports the application {status or 'UNKNOWN'}{': ' + detail if detail else ''}."
        if ready < 1:
            dep_messages = [
                f"{name}={(dep.get('status') or '').upper()} {dep.get('message', '')}".strip()
                for name, dep in _model_server_deployments(app).items()
            ]
            detail = "; ".join(m for m in dep_messages if m) or "no ready replicas yet"
            return False, f"waiting for ready replicas: {detail}"
        return True, f"Serving on {_plural(ready, 'copy')}."

    # want_active is False: converged when the app is gone or has no replicas.
    if not (app.get("deployments") or {}):
        return True, f"application {status or 'absent'}"
    if ready == 0:
        return True, "scaled to zero"
    return False, f"application still {status or 'active'} with {ready} ready replica(s)"


def _copies_wanted(spec_data: dict[str, Any] | None) -> tuple[int, bool]:
    """(copies asked for, autoscaled?) from a deployment spec."""
    scale = (spec_data or {}).get("scale") or {}
    autoscale = scale.get("autoscale")
    if autoscale:
        return int(autoscale.get("min_replicas") or 1), True
    return int(scale.get("replicas") or 1), False


def _gpus_per_copy(spec_data: dict[str, Any] | None) -> float:
    replica = (((spec_data or {}).get("resources") or {}).get("replica")) or {}
    try:
        return float(replica.get("gpus", 1) or 0)
    except (TypeError, ValueError):
        return 1.0


def _settled(app: dict[str, Any], ready: int, wanted: int, *, autoscaled: bool) -> bool:
    """Has the app reached the number of copies asked for?"""
    if (app.get("status") or "").upper() != "RUNNING":
        return False
    return ready >= wanted if autoscaled else ready == wanted


def _plural(n: int | float, word: str) -> str:
    if n == 1:
        return f"{n} {word}"
    return f"{n} {word[:-1]}ies" if word.endswith("y") else f"{n} {word}s"


def _scaling_message(
    *,
    ready: int,
    wanted: int,
    autoscaled: bool,
    gpus_per_copy: float,
    cluster_gpus: float,
    app: dict[str, Any] | None,
) -> str:
    """What a deployment that is serving, but not yet at its count, says.

    It used to say "app application DEPLOYING:" -- the application's status
    with its empty message, prefixed twice -- while the page showed "Starting"
    for a model that was serving throughout. Asked for more copies than the
    cluster has accelerators, it said the same thing forever.
    """
    if autoscaled:
        return f"Serving on {_plural(ready, 'copy')}; scaling toward at least {wanted}."
    if ready > wanted:
        return f"Serving on {_plural(ready, 'copy')}; stopping {ready - wanted} to reach {wanted}."
    missing = wanted - ready
    if missing <= 0:
        # The count is reached; Ray is still settling (a copy it stopped, or
        # its own status not yet back to RUNNING).
        return f"Serving on {_plural(ready, 'copy')}; finishing the change."
    if gpus_per_copy > 0 and cluster_gpus > 0 and wanted * gpus_per_copy > cluster_gpus:
        fits = int(cluster_gpus // gpus_per_copy)
        each = _plural(int(gpus_per_copy) if gpus_per_copy.is_integer() else gpus_per_copy, "accelerator")
        return (
            f"Serving on {ready} of {wanted} copies. {wanted - fits} cannot start: each copy "
            f"needs {each}, and this cluster has {int(cluster_gpus)}, so {fits} fit. "
            f"Scale to {fits}, or add a machine to the cluster."
        )
    message = f"Serving on {ready} of {wanted} copies; {missing} more starting."
    stuck = [
        str(dep.get("message") or "").strip()
        for dep in _model_server_deployments(app or {}).values()
        if "to be scheduled" in str(dep.get("message") or "")
    ]
    if stuck:
        message += (
            " Ray has not found room for it yet -- another deployment may be using the "
            "accelerators it needs."
        )
    return message


def _is_downloading(model: Any) -> bool:
    """Whether this server is downloading *model* now."""
    status = getattr(model, "status", None)
    return str(getattr(status, "value", status) or "").lower() == "downloading"


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
    artifact_readiness: ArtifactReadiness | None = None
    artifact_root_path: str | None = None
    spec_data: dict[str, Any] | None = None
    total_replicas: int = 0
    #: The runtime bundle certified for the *head* node, and its rendered
    #: launch contract.  Both are per-machine facts: on a cluster spanning
    #: two platforms the image differs by node even though the container
    #: name does not.  ``None`` means the head runs Ray on the host.
    runtime_bundle: Any = None
    runtime_bundle_payload: dict[str, Any] | None = None
    #: The composed mount table -- the bundle's, plus the head node's own
    #: paths.  What the container will actually be started with.
    runtime_mounts: list[Any] = field(default_factory=list)


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
                    ready_replicas=0,
                    total_replicas=0,
                )
                return
            try:
                result = await self._client_for(node_control).delete_serve_app(
                    head_node_id=facts.head_node_id,
                    app_name=app_name,
                    runtime_bundle=facts.runtime_bundle_payload,
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
                # Nothing is serving once the application is gone. Without
                # this the row kept the counts it had while it was running,
                # so a stopped deployment's page read "Copies (ready /
                # wanted) 1 / 1" over a cluster with no Serve application on
                # it at all -- and the screen an operator checks to confirm a
                # stop told them it had not happened.
                **({"ready_replicas": 0, "total_replicas": 0} if ok else {}),
            )
            if ok:
                await self._retire_endpoints(session, deployment.id)
            return

        # ------------------------------------------------------------------
        # Desired ACTIVE
        # ------------------------------------------------------------------

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
            if deployment.phase in (DeploymentPhase.RUNNING.value, DeploymentPhase.DEGRADED.value):
                # It was serving. Nothing is applied to a cluster that is not
                # up, but what is still serving is worth saying -- and what is
                # not has to stop being routed to.
                await self._observe_on_unready_cluster(session, deployment, facts, node_control)
                return
            self._observe(
                deployment, DeploymentPhase.PENDING,
                f"environment not ready ({env_status}); waiting", False,
                observed={"reconciled": False, "reason": "environment not ready"},
                mark_observed=False,
            )
            return

        # 3a. Already applied at this generation: this pass is a look, not a
        # deploy. Nothing below is recomputed for it -- the compiled config
        # depends on where the model is, and "where" wobbles: a pass that ran
        # while the agents were reconnecting read the model as not on the
        # machines, compiled the remote source instead of the local copy, saw
        # a new config hash, and re-applied -- restarting a model that was
        # serving perfectly well. It is re-applied only when it is gone.
        observed_json = deployment.observed_status_json or {}
        applied_hash = observed_json.get("applied_config_hash")
        if (
            applied_hash
            and observed_json.get("applied_generation") == deployment.generation
            and deployment.phase in (DeploymentPhase.RUNNING.value, DeploymentPhase.DEGRADED.value)
        ):
            client = self._client_for(node_control)
            if not await self._gone(client, deployment, facts):
                cached = observed_json.get("observation", {}).get(
                    "run", {"app": app_name, "deployed": True, "cached": True}
                )
                await self._observe_serving(
                    session, deployment, facts, client,
                    config_hash=applied_hash, run_result=cached, observing=True,
                )
                return

        # 3b'. Not while a machine is away. Where the model lives is read from
        # the members that are connected, so with one reconnecting it reads
        # as absent there: the config compiled to the remote source instead
        # of the local copy and was applied -- twice on the DGX pair, across
        # two backend restarts. A reconnect takes seconds; wait for it.
        away = await self._members_away(session, facts.environment)
        if away:
            phase = (
                DeploymentPhase(deployment.phase)
                if deployment.phase in (DeploymentPhase.RUNNING.value, DeploymentPhase.DEGRADED.value)
                else DeploymentPhase.PENDING
            )
            self._observe(
                deployment, phase,
                f"Waiting for {', '.join(away)} to reconnect before applying.", False,
                observed={"reconciled": False, "reason": "member offline"},
                mark_observed=False,
            )
            return

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

        # 3c. Preparation gate (WI-5): Gate on artifact readiness across environment nodes
        readiness = facts.artifact_readiness
        artifacts_cfg = (facts.environment.config_json or {}).get("artifacts") or {}

        # The runtime bundle is air-gapped by construction: the Phase 4B
        # contract forbids this path from touching Hugging Face or NGC, and
        # the certified image carries ``HF_HUB_OFFLINE=1`` to enforce it.  So
        # "fall back to remote source" was never something the architecture
        # offers -- it only deferred the failure into Ray, which reported it
        # four minutes later as
        #
        #     Failed to create vLLM engine config: Cannot find an appropriate
        #     cached snapshot folder for the specified revision
        #
        # while the precise reason ("model_sync payload with files is
        # required") had been sitting in artifact readiness the whole time.
        #
        # So a remote fetch is now opt-in rather than assumed.  ``offline_only``
        # is still honoured for anything that sets it.
        allow_remote_fetch = bool(artifacts_cfg.get("allow_remote_fetch", False))
        offline_only = bool(artifacts_cfg.get("offline_only", False))

        # A deployment that loads from a path on the machines says the files
        # are there: there is nothing to copy, and the coordinator must not
        # start copying a model over the one a running engine is reading
        # (a taken-over deployment is one, see takeover.py).
        local_path = ((facts.spec_data or {}).get("artifacts") or {}).get("source") == "local_path"
        if readiness is not None and not readiness.all_ready and not local_path:
            # Evidence that a sync was *attempted and failed*.  That is the
            # case which used to slip through: readiness already knew exactly
            # why ("model_sync payload with files is required"), and we
            # deployed anyway.
            #
            # Deliberately narrower than "not all_ready".  A model with no
            # artifact record at all is not evidence of anything -- the node
            # may already hold it in its own Hugging Face cache, which is a
            # supported way to run -- so that case still proceeds and lets the
            # engine be the judge.
            has_hard_blockers = bool(readiness.failed_node_ids)
            # A missing server-side manifest is weaker -- it means we have not
            # recorded one, not that the model is absent -- so it only decides
            # the question when the operator has declared the environment
            # offline-only.
            no_manifest = offline_only and readiness.manifest_sha256 is None
            cannot_reach_offline = (
                has_hard_blockers and not allow_remote_fetch
            ) or no_manifest
            if cannot_reach_offline and has_hard_blockers and not no_manifest:
                # A failed copy is not a broken machine: the stream carrying it
                # may simply have dropped. Let the coordinator retry the ones
                # past their backoff before calling the deployment failed --
                # stopping here for good left a model hosted from the
                # marketplace failed until someone pressed "sync again".
                retry = await ModelArtifactCoordinator(session, gateway=_gateway(node_control)).ensure(
                    model=facts.model, environment=facts.environment, fetch_to_server=False,
                )
                still_failed = [n for n in retry.failed_node_ids if not await self._retry_issued(session, facts, n)]
                # The failed nodes' own blockers were already there; anything
                # ``ensure`` added (no copy to send, the re-issue failing) is new.
                new_blockers = [b for b in retry.blockers if b not in readiness.blockers]
                if not still_failed and not new_blockers:
                    facts.artifact_readiness = retry
                    self._observe(
                        deployment,
                        DeploymentPhase.PREPARING,
                        "Copying the model to the machines again after a failed attempt.",
                        False,
                        observed={
                            "reconciled": False,
                            "reason": "preparing_artifacts",
                            "retried_node_ids": retry.failed_node_ids,
                        },
                        mark_observed=False,
                    )
                    return
            if cannot_reach_offline:
                blockers = list(readiness.blockers)
                if readiness.manifest_sha256 is None and not blockers:
                    blockers.append(
                        f"No local manifest or cache directory found on server for model {facts.hf_repo_id or (facts.model.id if facts.model else 'unknown')}"
                    )
                blocker_msg = "; ".join(blockers) if blockers else f"Model sync failed on nodes: {', '.join(readiness.failed_node_ids)}"
                self._observe(
                    deployment,
                    DeploymentPhase.FAILED,
                    # Say what is wrong in the operator's terms and name the
                    # node, rather than making them read a Ray traceback.
                    f"The model is not on every machine yet, and this runtime "
                    f"cannot download it: {blocker_msg}",
                    False,
                    observed={
                        "reconciled": False,
                        "reason": "artifact_blocked",
                        "blockers": blockers,
                        "failed_node_ids": readiness.failed_node_ids,
                    },
                    mark_observed=True,
                )
                return

            # Always give the coordinator a chance to act.  It self-throttles:
            # in-flight syncs are left alone and a failed node is only retried
            # after a backoff.  Short-circuiting on the first failure meant a
            # transient error permanently disabled local artifacts for this
            # deployment, because this branch then never called ``ensure``
            # again.
            gateway = _gateway(node_control)
            coordinator = ModelArtifactCoordinator(session, gateway=gateway)
            # A model this server is downloading right now is on its way here,
            # not something to fetch elsewhere: wait for it, then copy it to
            # the machines. Deciding on ``offline_only`` alone let a model
            # hosted from the marketplace -- downloaded as it is deployed --
            # "fall back to remote source" at once, and Ray failed it three
            # times with "Cannot find an appropriate cached snapshot folder"
            # (the runtime is air-gapped, above).
            server_downloading = _is_downloading(facts.model)
            server_only = offline_only or (server_downloading and not allow_remote_fetch)
            # Keep what ``ensure`` decided. It is the call that knows whether a
            # sync could actually be issued, and it refuses with a sentence
            # naming the model and the directory it looked in -- "this server
            # has no local copy to send". Discarding it and reporting the
            # readiness from before the attempt left a deployment sitting on
            # "Copying the model" for as long as anyone was willing to watch,
            # with nothing ever copying and nothing saying so.
            readiness = await coordinator.ensure(
                model=facts.model,
                environment=facts.environment,
                fetch_to_server=server_only,
            )
            facts.artifact_readiness = readiness

            cannot_reach = bool(readiness.failed_node_ids or readiness.blockers)

            if server_only and readiness.blockers:
                # Server-only: nothing else is going to fetch this model, so
                # the coordinator's refusal is the end of the road and the
                # operator needs to read it. With remote fetch allowed the
                # same refusal is not fatal -- the node downloads the model
                # itself -- and it falls through to that path below.
                self._observe(
                    deployment,
                    DeploymentPhase.FAILED,
                    f"The model cannot be sent to the cluster and this "
                    f"environment may not download it: "
                    f"{'; '.join(readiness.blockers)}",
                    False,
                    observed={
                        "reconciled": False,
                        "reason": "artifact_unavailable",
                        "blockers": readiness.blockers,
                    },
                    mark_observed=True,
                )
                return

            if not server_only and cannot_reach:
                log.info(
                    "Local artifact sync cannot be reached (%s); falling back to remote source for deployment %s",
                    readiness.blockers or readiness.failed_node_ids,
                    deployment.id,
                )
            else:

                obs_data: dict[str, Any] = {
                    "reconciled": False,
                    "reason": "preparing_artifacts",
                    "ready_node_ids": readiness.ready_node_ids,
                    "pending_node_ids": readiness.pending_node_ids,
                }
                if not server_only:
                    obs_data["remote_fallback_available"] = True

                if readiness.waiting_on:
                    obs_data["reason"] = "model_downloading_to_server"
                self._observe(
                    deployment,
                    DeploymentPhase.PREPARING,
                    # Say what is actually being waited on. "Artifacts
                    # syncing" while the server was still downloading the
                    # model described a copy that had not started.
                    readiness.waiting_on
                    or "Artifacts syncing across environment members; waiting for readiness",
                    False,
                    observed=obs_data,
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
        # PREPARING is deliberately NOT a trigger here.  When artifact
        # preparation changes what gets deployed it changes the resolved model
        # path, which changes ``config_hash`` and triggers an apply on its own.
        # Treating the phase itself as a trigger meant any excursion through
        # PREPARING on a *running* deployment (a node joining the environment,
        # a STALE digest) re-ran ``serve.run`` with an identical config and
        # restarted every replica for nothing.
        current_head = _cluster_head(facts.environment)
        applied_head = (deployment.observed_status_json or {}).get("applied_head")
        need_apply = (
            (applied_hash != config_hash)
            or (deployment.phase == DeploymentPhase.FAILED.value)
            # The cluster's head was started again since this was applied --
            # re-formed after it died, or its machine rebooted -- and a new
            # head starts with no Serve applications at all.
            or bool(applied_head and current_head and applied_head != current_head)
        )
        if not need_apply:
            serve_status = await self._probe_serve(
                client, facts.head_node_id, app_name, facts.runtime_bundle_payload
            )
            if serve_status is not None and serve_status.alive:
                current = _serve_app_entry(serve_status, app_name)
                if current is None or (current.get("status") or "").upper() == "DEPLOY_FAILED":
                    need_apply = True
            elif serve_status is not None and _no_serve_instance(serve_status.detail):
                # The cluster answered, and it has no Serve at all: the app is
                # absent, not unobserved.
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
                    runtime_bundle=facts.runtime_bundle_payload,
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

        if need_apply and deployment.phase != DeploymentPhase.RUNNING.value:
            # Waiting for the copies takes minutes and the pass commits only at
            # its end. After a cluster restart the page went on reading
            # "Checking again before restarting anything" for the whole wait
            # while the model was in fact being applied again. (A serving
            # model being scaled keeps reading Running: it is.)
            restarted = bool(applied_head and current_head and applied_head != current_head)
            self._observe(
                deployment, DeploymentPhase.APPLYING,
                "The cluster was restarted: applying the model again; its copies are starting."
                if restarted else "Applied; waiting for the copies to start.",
                False,
                observed={"reconciled": False, "action": "run", "app": app_name, "run": run_result},
                mark_observed=False,
                **({"ready_replicas": 0} if restarted else {}),
            )
            await self._commit_progress(session)

        if current_head and (need_apply or not applied_head):
            # Which head this was applied on, so a head started since is
            # noticed. Recorded on an observation too, for deployments applied
            # before this was kept.
            self._remember_head(deployment, current_head)

        await self._observe_serving(
            session, deployment, facts, client,
            config_hash=config_hash, run_result=run_result,
        )

    async def _observe_serving(
        self,
        session,
        deployment: InferenceDeployment,
        facts: "_DeploymentFacts",
        client: RayClusterClient,
        *,
        config_hash: str,
        run_result: dict[str, Any],
        observing: bool = False,
    ) -> None:
        """Read how the applied app is doing and record it (steps 5 and 6).

        *observing* is a health check of an app applied earlier, as opposed
        to the follow-up of an apply this pass made.
        """
        app_name = facts.app_name

        # 5. Observe readiness (best-effort; unobserved stays pending).
        observed, ready, total = await self._poll_readiness(
            client,
            head_node_id=facts.head_node_id,
            app_name=app_name,
            runtime_bundle=facts.runtime_bundle_payload,
        )

        app_status = ((observed or {}).get("status") or "").upper()
        if app_status == "DEPLOY_FAILED":
            # Terminal on Ray's side (replica startup exhausted its retries):
            # surface Ray's message.  Out of the queue until the spec changes
            # or a reconcile is requested (phase FAILED forces a re-apply).
            self._observe(
                deployment, DeploymentPhase.FAILED,
                f"application DEPLOY_FAILED: {_failure_detail(observed)}".strip(), False,
                observed={"reconciled": False, "reason": "deploy-failed", "app": app_name},
                config_hash=config_hash,
            )
            return
        if app_status == "UNHEALTHY":
            self._observe(
                deployment, DeploymentPhase.DEGRADED,
                f"application UNHEALTHY: {_failure_detail(observed)}".strip(), False,
                observed={"reconciled": False, "reason": "unhealthy", "app": app_name},
                mark_observed=False,
                ready_replicas=ready,
                total_replicas=_copies_wanted(facts.spec_data)[0],
                config_hash=config_hash,
            )
            return

        if not observed and observing:
            # A look that got no answer. The model is as it was, as far as
            # anyone knows: "serve.run accepted" would claim an apply nobody
            # made. The next health check looks again.
            self._observe(
                deployment, DeploymentPhase(deployment.phase),
                "Could not check the model just now; it is looked at again within a minute.", False,
                observed={"reconciled": False, "action": "observe", "app": app_name, "run": run_result},
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

        wanted, autoscaled = _copies_wanted(facts.spec_data)
        if ready >= 1 and not _settled(observed, ready, wanted, autoscaled=autoscaled):
            # Serving, and still changing size: a scale up or down in
            # progress, or copies that cannot be placed. It stays Serving --
            # it is -- and stays queued so the count is followed to the end.
            cluster = ((facts.environment.observed_status_json or {}).get("cluster") or {}) if facts.environment else {}
            message = _scaling_message(
                ready=ready,
                wanted=wanted,
                autoscaled=autoscaled,
                gpus_per_copy=_gpus_per_copy(facts.spec_data),
                cluster_gpus=float(cluster.get("total_gpus") or 0),
                app=observed,
            )
            self._observe(
                deployment, DeploymentPhase.RUNNING, message, False,
                observed={
                    "reconciled": False,
                    "action": "scaling",
                    "app": app_name,
                    "app_status": (observed or {}).get("status"),
                    "ready_replicas": ready,
                },
                mark_observed=False,
                ready_replicas=ready,
                total_replicas=wanted,
                config_hash=config_hash,
            )
            await self._publish_endpoint(
                session, deployment, facts, app_name, ready_replicas=ready,
            )
            return

        converged, reason = _app_deployment_readiness(observed, want_active=True)
        if not converged:
            self._observe(
                deployment, DeploymentPhase.APPLYING,
                reason, False,
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
            total_replicas=wanted,
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
        head_row, facts.runtime_bundle = await node_and_bundle_for(
            session, head, driver="ray"
        )
        if facts.runtime_bundle is not None:
            # The mount table as the agent will see it: the bundle's own
            # mounts plus the head's paths.  ``_resolve`` keeps it because the
            # model-path translation below reads it, and translating against
            # the bundle alone would miss every mount the node supplied.
            facts.runtime_mounts = default_bundle_registry.mounts_for_node(
                facts.runtime_bundle, head_row
            )
            facts.runtime_bundle_payload = (
                default_bundle_registry.container_launch_spec(
                    facts.runtime_bundle,
                    name=_RUNTIME_CONTAINER_NAME,
                    node=head_row,
                )
            )

        model = await self._model_of(session, deployment.model_id)
        if model is None:
            return _ResolveError("model not found", failed=True)
        facts.model = model
        facts.model_source = getattr(model, "source", "huggingface") or "huggingface"
        facts.hf_repo_id = model.hf_repo_id
        facts.hf_revision = model.hf_revision

        # Evaluate model artifact readiness across environment member nodes (WI-3, WI-5)
        coordinator = ModelArtifactCoordinator(session)
        readiness = await coordinator.evaluate(model=model, environment=environment)
        facts.artifact_readiness = readiness

        # One compiled ``model_source`` is used by every replica on every node,
        # so the nodes have to agree on where the artifact lives.  They can
        # legitimately disagree - ``model_store_root`` is per-agent
        # configurable - and silently compiling the head's path would leave
        # every other node loading from somewhere that does not exist.
        distinct_roots = {v for v in readiness.root_paths.values() if v}
        if len(distinct_roots) > 1:
            readiness.blockers.append(
                "Nodes report different artifact roots "
                f"({', '.join(sorted(distinct_roots))}); a single model path cannot be compiled"
            )
            readiness.all_ready = False
            host_root_path = None
        else:
            host_root_path = readiness.root_paths.get(str(head)) or next(
                iter(distinct_roots), None
            )
        facts.artifact_root_path = host_root_path

        # Apply D-3 container mount path translation if all_ready
        if readiness.all_ready and host_root_path:
            # The path the *head* sees.  Serve resolves the model inside the
            # head's runtime container, so the mount table that matters is the
            # one from the head's own bundle -- not from a bundle pinned on
            # the cluster, which on a mixed cluster belonged to nobody.
            bundle = facts.runtime_bundle
            if bundle is not None and facts.runtime_mounts:
                translated = translate_host_path_to_container(
                    host_root_path, facts.runtime_mounts
                )
                if translated:
                    facts.availability_root_path = translated
                else:
                    facts.availability_root_path = None
                    readiness.blockers.append(
                        f"Host artifact root {host_root_path} is not mapped under "
                        f"any container mount in bundle {bundle.bundle_id}"
                    )
                    readiness.all_ready = False
            else:
                facts.availability_root_path = host_root_path
        else:
            facts.availability_root_path = None

        return facts

    @staticmethod
    async def _members_away(session, environment) -> list[str]:
        """Names of the cluster's machines with no agent connected."""
        rows = await session.execute(
            select(InfraNode)
            .join(InferenceEnvironmentNode, InferenceEnvironmentNode.node_id == InfraNode.id)
            .where(InferenceEnvironmentNode.environment_id == environment.id)
        )
        return sorted(
            node.agent_id or node.host or str(node.id)
            for node in rows.scalars()
            if str(node.status or "").lower() == NodeHealthStatus.OFFLINE.value
        )

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
        self,
        client: RayClusterClient,
        *,
        head_node_id: uuid.UUID,
        app_name: str,
        runtime_bundle: dict[str, Any] | None = None,
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
            status = await self._probe_serve(client, head_node_id, app_name, runtime_bundle)
            entry = _serve_app_entry(status, app_name)
            if entry is not None:
                last = entry
                ready, total = _replica_counts(entry)
                app_status = (entry.get("status") or "").upper()
                if app_status in ("DEPLOY_FAILED", "UNHEALTHY"):
                    return entry, ready, total  # decided by the caller; no point polling
                if ready >= 1:
                    # Serving. While copies are added Ray reports DEPLOYING, and
                    # waiting for RUNNING held the whole pass for its full
                    # budget while a copy was answering requests. The caller
                    # keeps the row queued until the count is reached.
                    return entry, ready, total

            if asyncio.get_event_loop().time() > deadline:
                return last, ready, total
            await asyncio.sleep(_READINESS_POLL_SEC)

    async def _probe_serve(
        self,
        client: RayClusterClient,
        head_node_id: uuid.UUID,
        app_name: str,
        runtime_bundle: dict[str, Any] | None = None,
    ) -> Any:
        """Best-effort Serve status probe; ``None`` when the probe itself errors."""
        try:
            return await client.probe_serve(
                head_node_id=head_node_id,
                runtime_bundle=runtime_bundle,
            )
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

        Where a proxy *binds* and where clients *reach* it are two different
        questions, and this used to answer both with one value: under
        ``HeadOnly`` it passed the head node's management IP as the bind
        address.  That is wrong in two independent ways.

        First, a socket can only bind an address that exists on the machine it
        is running on, and the backend does not choose that machine -- Ray
        does.  Hand it a specific IP and every proxy that lands anywhere else
        dies with ``EADDRNOTAVAIL``, retries, and dies again; Serve reports
        only "Failed to update the deployments", so the real cause is three
        log files away.

        Second, on a cluster bound to a separate fabric the management IP is
        not the address Ray nodes carry at all, so the bind can fail even on
        the node we meant.

        So the bind is always the wildcard, and the routable address clients
        use is resolved separately in :meth:`_publish_endpoint`.  A wildcard
        bind on the head still answers on the head's address; it simply also
        survives Ray placing the proxy somewhere we did not predict.
        """
        cfg = self._environment_config(facts)
        location = cfg.serve_proxy_location or "HeadOnly"
        # Kept as an escape hatch for an operator pinning a specific
        # interface, but never derived -- deriving it is what broke.
        host = cfg.serve_http_host or _WILDCARD_BIND
        if cfg.serve_http_host:
            log.warning(
                "Environment pins serve_http_host=%s; the Serve proxy will fail "
                "to start on any node without that address.",
                cfg.serve_http_host,
            )
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
        if host in _WILDCARD_BINDS:  # wildcard bind: publish the head's address
            host = facts.head_host or (facts.environment.address if facts.environment else None) or "head"
        # Take the prefix the agent reported rather than assuming it.  This
        # published "/<app>" while the container path was mounting at "/",
        # so the address in the UI returned 404 for a model that was serving
        # perfectly well one path up.
        prefix = _reported_route_prefix(deployment) or f"/{app_name}"
        address = f"http://{host}:{http['port']}{prefix}"

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

    async def _gone(self, client: RayClusterClient, deployment: InferenceDeployment, facts: "_DeploymentFacts") -> bool:
        """Whether an applied app has to be applied again.

        Only when it is not there to serve: its cluster's head was started
        again since it was applied (a new head has no Serve applications), or
        Serve answers without it, or answers that it is not running at all.
        A probe that could not look is not a reason: the app is left alone.
        """
        observed = deployment.observed_status_json or {}
        current_head = _cluster_head(facts.environment)
        applied_head = observed.get("applied_head")
        if applied_head and current_head and applied_head != current_head:
            return True
        status = await self._probe_serve(
            client, facts.head_node_id, facts.app_name, facts.runtime_bundle_payload
        )
        if status is not None and status.alive:
            entry = _serve_app_entry(status, facts.app_name)
            if entry is None or (entry.get("status") or "").upper() == "DEPLOY_FAILED":
                return True
        elif status is not None and _no_serve_instance(status.detail):
            return True
        if current_head and not applied_head:
            self._remember_head(deployment, current_head)
        return False

    @staticmethod
    async def _commit_progress(session) -> None:
        try:
            await session.commit()
        except Exception:  # noqa: BLE001 - progress text is not worth failing an apply over
            log.debug("Could not commit deployment progress", exc_info=True)

    @staticmethod
    def _remember_head(deployment: InferenceDeployment, head: str) -> None:
        observed = dict(deployment.observed_status_json or {})
        observed["applied_head"] = head
        deployment.observed_status_json = observed

    async def _observe_on_unready_cluster(
        self, session, deployment: InferenceDeployment, facts: "_DeploymentFacts", node_control,
    ) -> None:
        """Report a deployment that was serving, on a cluster that is not ready.

        It used to go to PENDING with the counts it had, so a model whose
        cluster had lost its head kept reading two ready copies and kept being
        routed to. What the cluster last said decides it now:

        * the head is down -- nothing serves, whatever the count was: zero,
          which stops the gateway routing to it;
        * the head answers (a worker dropped out) -- ask Serve and report
          that: one copy of two still serves, and stays routable;
        * the cluster could not be looked at (its head machine is offline) --
          nothing is known, so the counts stay and the message says so.

        Nothing is applied: the deployment reconciler does that once the
        cluster is ready again, and re-applies if the head changed meanwhile.
        """
        environment = facts.environment
        about = getattr(environment, "status_message", None) or (
            f"The cluster is {getattr(environment, 'status', 'not ready')}."
        )
        cluster = ((getattr(environment, "observed_status_json", None) or {}).get("cluster")) or {}
        wanted = _copies_wanted(facts.spec_data)[0]
        observed = {"reconciled": False, "reason": "cluster not ready", "app": facts.app_name}

        # Observed, each of these: DEGRADED keeps the row in the queue on its
        # own, and leaving the generation behind read "checking now" on the
        # page for as long as the cluster was down.
        if cluster.get("observed", True) and not cluster.get("alive"):
            self._observe(
                deployment, DeploymentPhase.DEGRADED,
                f"Not serving. {about}", False,
                observed=observed,
                ready_replicas=0, total_replicas=wanted,
            )
            return

        # A head the cluster already suspects is not asked again from here:
        # against a dead one each question takes 30 s, and it is the cluster's
        # own look that has to get through.
        recovery = (getattr(environment, "observed_status_json", None) or {}).get("recovery") or {}
        head_suspect = recovery.get("kind") == "head"
        entry = None
        if cluster.get("alive") and not head_suspect:
            status = await self._probe_serve(
                self._client_for(node_control), facts.head_node_id, facts.app_name,
                facts.runtime_bundle_payload,
            )
            if status is not None and status.alive:
                entry = _serve_app_entry(status, facts.app_name)
        if entry is None:
            self._observe(
                deployment, DeploymentPhase.DEGRADED,
                f"Cannot check the model right now. {about}", False,
                observed=observed,
            )
            return
        ready, _total = _replica_counts(entry)
        serving = (
            f"Serving on {ready} of {_plural(wanted, 'copy')}." if ready else "Not serving."
        )
        self._observe(
            deployment, DeploymentPhase.DEGRADED,
            f"{serving} {about}", False,
            observed={**observed, "app_status": entry.get("status"), "ready_replicas": ready},
            ready_replicas=ready, total_replicas=wanted,
        )

    @staticmethod
    async def _retry_issued(session: Any, facts: _DeploymentFacts, node_id: str) -> bool:
        """Whether the coordinator has just re-issued the copy that failed on *node_id*.

        ``ensure`` answers with the readiness it read before acting, so a node
        it retried still reads as failed there; its row says otherwise.
        """
        from llm_port_backend.db.dao.inference_dao import ModelAvailabilityDAO  # noqa: PLC0415
        from llm_port_backend.db.models.inference import ModelAvailabilityStatus  # noqa: PLC0415

        try:
            row = await ModelAvailabilityDAO(session).get(facts.model.id, uuid.UUID(node_id))
        except ValueError:
            return False
        return row is not None and row.status != ModelAvailabilityStatus.FAILED.value

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
            # The generation that config belongs to: once it matches, later
            # passes only look (see step 3a).
            observed_status["applied_generation"] = deployment.generation
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
