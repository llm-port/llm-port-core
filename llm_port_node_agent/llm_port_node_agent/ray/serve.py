"""Ray Serve Python-API layer (spec section 2: Serve lifecycle/status).

All Serve operations go through the ``ray.serve`` Python API:
``serve.start()`` / ``serve.run()`` / ``serve.delete()`` / ``serve.shutdown()``
/ ``serve.status()``.  No Dashboard REST is used anywhere in this refactor.

Serve state is a **separate tier** from cluster health: a Serve
unavailability (import error, controller down, no app deployed) yields
``available=False`` on the ``serve`` sub-structure but NEVER fails the overall
``GET_RAY_STATUS`` (Tier A is independent of Tier A-serve).

The structured ``serve.status()`` shape in Ray 2.58 is
``ServeStatus(proxies, applications, target_capacity)`` where each
``ApplicationStatusOverview`` carries ``status`` (an ``ApplicationStatus``
enum), ``message``, ``last_deployed_time_s`` and a ``deployments`` dict of
``DeploymentStatusOverview`` (``status``/``status_trigger``/``replica_states``/
``message``).  ``replica_states`` is a mapping of ``ReplicaState`` -> count;
we flatten it into ``num_replicas_ready`` (``READY``) /
``num_replicas_pending`` (everything else) plus the per-state list.
"""

from __future__ import annotations

import logging
from typing import Any

from llm_port_node_agent.ray import errors, models
from llm_port_node_agent.ray.core import RayCoreClient

log = logging.getLogger(__name__)


def _enum_name(value: Any) -> str | None:
    """``Enum`` -> its ``.name``; plain strings pass through."""
    if value is None:
        return None
    name = getattr(value, "name", None)
    if isinstance(name, str):
        return name
    return str(value)


class RayServeManager:
    """Serve lifecycle + status via the Python API.

    Args:
        core: :class:`RayCoreClient` used for the shared attach; Serve
            operations require a live attach first.
    """

    def __init__(self, *, core: RayCoreClient | None = None) -> None:
        self._core = core or RayCoreClient()

    # ------------------------------------------------------------------
    # status
    # ------------------------------------------------------------------

    def status(self) -> models.RayServeStatusTier:
        """Return Serve status as a DTO tier.

        Never raises: any failure is folded into
        ``RayServeStatusTier(available=False, detail=...)`` so the caller can
        attach it to the cluster status without affecting Tier A health.
        """
        serve = self._serve_module()
        if serve is None:
            return models.RayServeStatusTier(
                available=False, detail="ray.serve not importable in this agent"
            )
        try:
            self._core.ensure_attached()
            raw = serve.status()
        except errors.RayAttachError as exc:
            return models.RayServeStatusTier(
                available=False, detail=f"not attached to a cluster: {exc}"
            )
        except Exception as exc:
            return models.RayServeStatusTier(
                available=False, detail=f"serve.status() failed: {exc}"
            )
        return self._normalize(raw)

    def _serve_module(self) -> Any:
        try:
            from ray import serve  # noqa: PLC0415  (lazy: extra import cost)

            return serve
        except Exception:
            return None

    def _normalize(self, raw: Any) -> models.RayServeStatusTier:
        apps: dict[str, models.RayApplicationStatus] = {}
        applications = getattr(raw, "applications", {}) or {}
        for app_name, app in applications.items():
            deployments: dict[str, models.RayDeploymentStatus] = {}
            for dep_name, dep in (getattr(app, "deployments", {}) or {}).items():
                deployments[str(dep_name)] = self._normalize_deployment(dep_name, dep)
            apps[str(app_name)] = models.RayApplicationStatus(
                name=str(app_name),
                status=_enum_name(getattr(app, "status", None)) or "",
                message=str(getattr(app, "message", "") or ""),
                last_deployed_time_s=getattr(app, "last_deployed_time_s", None),
                deployments=deployments,
            )
        has_apps = bool(apps)
        active = has_apps and all(
            (a.status == "RUNNING" and all(d.status in ("RUNNING", "UP") for d in a.deployments.values()))
            for a in apps.values()
        )
        return models.RayServeStatusTier(
            available=True,
            active=active,
            apps=apps,
        )

    @staticmethod
    def _normalize_deployment(name: str, dep: Any) -> models.RayDeploymentStatus:
        replica_states: list[models.RayReplicaState] = []
        ready = 0
        pending = 0
        raw_rs = getattr(dep, "replica_states", {}) or {}
        for state, count in raw_rs.items():
            name = _enum_name(state) or str(state)
            replica_states.append(models.RayReplicaState(state=name, count=int(count)))
            if name == "READY":
                ready += int(count)
            else:
                pending += int(count)
        return models.RayDeploymentStatus(
            name=name,
            status=_enum_name(getattr(dep, "status", None)) or "",
            status_trigger=_enum_name(getattr(dep, "status_trigger", None)),
            message=str(getattr(dep, "message", "") or ""),
            replica_states=replica_states,
            num_replicas_ready=ready,
            num_replicas_pending=pending,
        )

    # ------------------------------------------------------------------
    # lifecycle (Python API first; no Dashboard REST)
    # ------------------------------------------------------------------

    def start(self) -> dict[str, Any]:
        """Start the Serve controller without deploying an application."""
        serve = self._require_serve()
        try:
            self._core.ensure_attached()
            serve.start()
            return {"started": True}
        except errors.RayAttachError:
            raise
        except Exception as exc:
            raise errors.RayServeError(f"serve.start() failed: {exc}") from exc

    def shutdown(self) -> dict[str, Any]:
        """Shut down Serve on the local cluster (keeps the cluster running)."""
        serve = self._require_serve()
        try:
            self._core.ensure_attached()
            serve.shutdown()
            return {"shutdown": True}
        except errors.RayAttachError:
            raise
        except Exception as exc:
            raise errors.RayServeError(f"serve.shutdown() failed: {exc}") from exc

    def delete_app(self, name: str) -> dict[str, Any]:
        """Delete a named application."""
        serve = self._require_serve()
        try:
            self._core.ensure_attached()
            serve.delete(name)
            return {"deleted": name}
        except errors.RayAttachError:
            raise
        except Exception as exc:
            raise errors.RayServeError(f"serve.delete({name!r}) failed: {exc}") from exc

    def run_app(self, name: str, llm_serving_args: dict[str, Any]) -> dict[str, Any]:
        """Build and deploy a named LLM Serve application.

        The application is built *in-process* with
        ``ray.serve.llm.build_openai_app`` — the agent is the only component
        that imports the (GPU-oriented) LLM stack.  ``llm_serving_args`` is the
        plain ``LLMServingArgs`` document shipped in the command payload.

        Deployment uses ``serve.run(app, name=...)`` with an *explicit* name,
        which is Ray's verified per-application update path: it deploys or
        updates exactly this application and leaves any other named
        applications on the cluster untouched (an unnamed ``serve.run``
        would delete every other app — never used here).

        ``serve.run`` is called non-blocking (``blocking=False``): the
        controller deploy is asynchronous and the caller observes convergence
        through ``status()`` / ``GET_RAY_SERVE_STATUS``.  Build failures
        (invalid engine args, unknown accelerator, bad model source) raise
        synchronously here and surface as a command failure.
        """
        serve = self._require_serve()
        try:
            self._core.ensure_attached()
        except errors.RayAttachError:
            raise
        try:
            from ray.serve.llm import build_openai_app  # noqa: PLC0415  (lazy)

            app = build_openai_app(llm_serving_args)
        except errors.RayAttachError:
            raise
        except Exception as exc:
            raise errors.RayServeError(f"build_openai_app failed: {exc}") from exc

        try:
            serve.run(app, name=name, blocking=False)
            return {"app": name, "deployed": True}
        except errors.RayAttachError:
            raise
        except Exception as exc:
            raise errors.RayServeError(f"serve.run({name!r}) failed: {exc}") from exc

    def _require_serve(self) -> Any:
        serve = self._serve_module()
        if serve is None:
            raise errors.RayServeError("ray.serve is not importable in this agent")
        return serve
