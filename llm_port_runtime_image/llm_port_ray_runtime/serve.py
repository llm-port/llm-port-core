"""Ray Serve Python SDK probe and status normalization."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional
from llm_port_ray_runtime.core import RayCoreClient
from llm_port_ray_runtime.models import (
    RayApplicationStatus,
    RayDeploymentStatus,
    RayServeClusterStatus,
)

log = logging.getLogger(__name__)


class RayServeClient:
    """Probe Serve controller and applications using the Ray Serve Python SDK."""

    def __init__(self, core: Optional[RayCoreClient] = None) -> None:
        self.core = core or RayCoreClient()

    def run_app(
        self,
        app_name: str,
        llm_serving_args: Dict[str, Any],
        http_options: Optional[Dict[str, Any]] = None,
        route_prefix: str = "/",
    ) -> Dict[str, Any]:
        """Deploy a named Serve LLM application.

        Lives here rather than in the host Node Agent on purpose: this package
        ships *inside* the certified image, so the Serve API call is made with
        the exact Ray version the cluster runs and is covered by the image's
        certification.  The host agent only invokes this through the CLI's JSON
        contract and never imports Ray itself (4B host/runtime boundary).

        Idempotent per application name: re-running replaces only this
        application and leaves other named apps alone.

        **Submits; does not wait for the application to serve.** ``serve.run``
        looks like the obvious call and is the wrong one: it forwards
        ``_blocking=True`` to Ray's internal ``_run``, which calls
        ``_wait_for_application_running(name, timeout_s=-1)`` -- a negative
        timeout means wait forever. So it returns only once the app is RUNNING
        or raises once it is DEPLOY_FAILED.

        A failing application takes a long time to reach that verdict: Serve
        restarts a replica three times, and each attempt carries the engine's
        own start timeout. The whole control path above this is sized for a
        call that returns in seconds -- a 300s exec budget in the agent and a
        300s command budget in the backend -- so both expired long before Ray
        had an answer, the command stayed in flight, and the deployment row
        went on reporting whatever it last knew. The reason for the failure
        existed the entire time, in the RuntimeError this call would
        eventually have raised, with nobody left listening for it.

        ``run_many`` is the public API that lets us say so: the application is
        handed to the controller and convergence is read afterwards from the
        status tier, which is what the agent and backend already do.
        """
        self.core.ensure_attached()

        from ray import serve
        from ray.serve.llm import build_openai_app

        opts = dict(http_options or {})
        start_kwargs: Dict[str, Any] = {
            "host": opts.get("host", "0.0.0.0"),
            "port": int(opts.get("port", 8000)),
        }
        if opts.get("location"):
            start_kwargs["proxy_location"] = opts["location"]

        try:
            # No-op when Serve is already running; http options only apply to
            # the first start, which Ray itself enforces.
            serve.start(http_options={"host": start_kwargs["host"], "port": start_kwargs["port"]})
        except Exception as exc:  # pragma: no cover - Serve already running
            log.info("serve.start skipped (%s)", exc)

        app = build_openai_app(llm_serving_args)
        # ``wait_for_ingress_deployment_creation`` stays on: it is a bounded
        # controller round-trip that confirms the application was accepted, so
        # "submitted" still means something. Only the open-ended wait for it to
        # become healthy is dropped.
        serve.run_many(
            [serve.RunTarget(app, name=app_name, route_prefix=route_prefix)],
            wait_for_applications_running=False,
        )
        return {
            "deployed": True,
            "app_name": app_name,
            "route_prefix": route_prefix,
            "http_options": start_kwargs,
        }

    def delete_app(self, app_name: str) -> Dict[str, Any]:
        """Delete a named Serve application.

        Deleting an application that was never deployed is reported as a
        success: the desired absence already holds, and the caller re-runs this
        on every teardown pass.
        """
        try:
            self.core.ensure_attached()
        except Exception as exc:
            return {"deleted": False, "app_name": app_name, "error": f"Cannot attach to Ray: {exc}"}

        try:
            from ray import serve

            serve.delete(app_name)
            return {"deleted": True, "app_name": app_name}
        except KeyError:
            return {"deleted": True, "app_name": app_name, "detail": "application not present"}
        except Exception as exc:
            message = str(exc)
            if "does not exist" in message or "not found" in message.lower():
                return {"deleted": True, "app_name": app_name, "detail": message}
            return {"deleted": False, "app_name": app_name, "error": message}

    def status(self) -> RayServeClusterStatus:
        """Query Serve status via ray.serve.status()."""
        try:
            self.core.ensure_attached()
        except Exception as exc:
            return RayServeClusterStatus(available=False, error=f"Cannot attach to Ray: {exc}")

        try:
            from ray import serve

            serve_status = serve.status()
        except ImportError as exc:
            return RayServeClusterStatus(available=False, error=f"Serve not installed: {exc}")
        except Exception as exc:
            return RayServeClusterStatus(available=False, error=f"Serve controller error: {exc}")

        apps: Dict[str, RayApplicationStatus] = {}
        for app_name, app_overview in serve_status.applications.items():
            deps: Dict[str, RayDeploymentStatus] = {}
            for dep_name, dep_overview in getattr(app_overview, "deployments", {}).items():
                num_ready = 0
                num_pending = 0
                for r_state, count in getattr(dep_overview, "replica_states", {}).items():
                    state_str = str(getattr(r_state, "name", r_state))
                    if state_str in ("RUNNING", "READY"):
                        num_ready += count
                    else:
                        num_pending += count

                deps[dep_name] = RayDeploymentStatus(
                    name=dep_name,
                    status=str(getattr(dep_overview.status, "name", dep_overview.status)),
                    status_trigger=getattr(dep_overview, "status_trigger", None),
                    message=getattr(dep_overview, "message", "") or "",
                    num_replicas_ready=num_ready,
                    num_replicas_pending=num_pending,
                )

            status_str = str(getattr(app_overview.status, "name", app_overview.status))
            is_healthy = status_str in ("RUNNING", "HEALTHY")
            apps[app_name] = RayApplicationStatus(
                name=app_name,
                status=status_str,
                message=getattr(app_overview, "message", "") or "",
                last_deployed_time_s=getattr(app_overview, "last_deployed_time_s", None),
                healthy=is_healthy,
                deployments=deps,
            )

        return RayServeClusterStatus(
            available=True,
            controller_alive=True,
            applications=apps,
        )

