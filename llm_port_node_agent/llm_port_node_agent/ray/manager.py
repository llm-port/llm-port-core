"""High-level coordinator for Ray lifecycle on a physical node (facade).

Spec section 2 target layout, implemented:

* :class:`~llm_port_node_agent.ray.runtime.RayRuntime` — bootstrap/process,
  the ONLY CLI layer (``ray start`` / ``ray stop``).
* :class:`~llm_port_node_agent.ray.core.RayCoreClient` — live cluster state
  via Ray Core Python APIs (GCS-direct; no Dashboard).
* :class:`~llm_port_node_agent.ray.serve.RayServeManager` — Serve
  lifecycle/status via the ``ray.serve`` Python API.
* :class:`~llm_port_node_agent.ray.metrics.RayMetricsDiscovery` — Tier C
  Prometheus scrape targets discovered from ``ray.nodes()``.
* :class:`~llm_port_node_agent.ray.state.RayStateDiagnostics` — Tier B
  optional ``ray.util.state`` (Dashboard-dependent; never gates health).

``RayManager`` keeps the exact same public surface the dispatcher already
calls (``ensure_runtime`` / ``start_head`` / ``join_cluster`` /
``leave_cluster`` / ``stop_ray`` / ``get_status``), plus ``get_serve_status``.
``get_status`` answers ``GET_RAY_STATUS`` with the enriched
:class:`~llm_port_node_agent.ray.models.RayEnvironmentStatus` (flat fields
preserved for the existing backend parser; tiers additive and non-gating).
"""

import asyncio
import logging
import os
import stat
from pathlib import Path
from typing import Any

import httpx

from llm_port_node_agent.event_buffer import EventBuffer
from llm_port_node_agent.ray import errors
from llm_port_node_agent.ray.core import RayCoreClient
from llm_port_node_agent.ray.metrics import RayMetricsDiscovery
from llm_port_node_agent.ray.runtime import RayRuntime
from llm_port_node_agent.ray.schemas import (
    EnsureRayRuntimePayload,
    JoinRayClusterPayload,
    StartRayHeadPayload,
    StopRayPayload,
    GetRayServeStatusPayload,
    GetRayStatusPayload,
)
from llm_port_node_agent.ray.serve import RayServeManager
from llm_port_node_agent.ray.state import RayStateDiagnostics
from llm_port_node_agent.state_store import StateStore

log = logging.getLogger(__name__)

_SECRET_ENDPOINT = "/api/admin/system/nodes/secrets/"
_DEFAULT_VERSION = "2.58.0"


class RayManager:
    """Coordinates Ray lifecycle commands from the backend."""

    def __init__(
        self,
        *,
        state_store: StateStore,
        events: EventBuffer,
        ray_base_path: str = "/opt/llm-port/ray",
        token_dir: Path | str = "/var/run/llm-port/ray",
        backend_url: str = "http://127.0.0.1:8000",
        token_ttl_sec: int = 300,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        self._state = state_store
        self._events = events
        # Process/bootstrap layer.  Aliased as ``_process`` (the historical
        # attribute name) so existing code/tests that swap ``manager._process``
        # with a mock keep working; it is now a :class:`RayRuntime`.
        self._process = RayRuntime(ray_base_path=ray_base_path)
        self._runtime = self._process
        self._core = RayCoreClient()
        self._serve = RayServeManager(core=self._core)
        self._metrics = RayMetricsDiscovery(core=self._core)
        self._state_diag = RayStateDiagnostics(core=self._core)
        self._token_dir = Path(token_dir)
        self._token_file = self._token_dir / "cluster.token"
        self._backend_url = backend_url.rstrip("/")
        self._token_ttl_sec = token_ttl_sec
        # Owned http client (created lazily) vs injected (caller owns close).
        self._http_injected = http
        self._http: httpx.AsyncClient | None = http
        self._token_ref: str | None = None
        self._token_written_at: float = 0.0

    async def close(self) -> None:
        """Release the owned HTTP client, if any."""
        if self._http is not None and self._http_injected is None:
            await self._http.aclose()
            self._http = None

    def _http_client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(base_url=self._backend_url, timeout=30.0)
        return self._http

    # ------------------------------------------------------------------
    # Token delivery
    # ------------------------------------------------------------------

    def _token_is_fresh(self, credential_ref: str) -> bool:
        import time

        if self._token_ref != credential_ref:
            return False
        try:
            if not self._token_file.exists() or not self._token_file.read_bytes().strip():
                return False
        except OSError:
            return False
        return (time.monotonic() - self._token_written_at) < self._token_ttl_sec

    async def _write_token_securely(self, payload: dict[str, Any]) -> None:
        """Fetch the decrypted cluster token from the backend and write it to a 0600 file.

        Raises:
            ValueError: payload lacks a ``credential_ref``.
            RuntimeError: no node credential recorded, or the backend
                refused to serve the token.
        """
        import time

        credential_ref = payload.get("credential_ref")
        if not credential_ref:
            raise ValueError("Missing credential_ref in payload")

        credential_ref = str(credential_ref)
        if self._token_is_fresh(credential_ref):
            return

        credential = self._state.state.credential
        if not credential:
            raise RuntimeError("Node credential not recorded; cannot fetch cluster token")

        client = self._http_client()
        try:
            response = await client.get(
                _SECRET_ENDPOINT + credential_ref,
                headers={"Authorization": f"Bearer {credential}"},
            )
        except Exception as exc:
            raise RuntimeError(f"Cluster token fetch failed: {exc}") from exc
        if response.status_code != 200:
            raise RuntimeError(f"Cluster token fetch failed (HTTP {response.status_code})")
        data = response.json()
        token = (data or {}).get("token")
        if not token:
            raise RuntimeError("Cluster token fetch returned an empty token")

        self._token_dir.mkdir(parents=True, exist_ok=True)
        # The file is the whole cluster's auth token: owner read/write only.
        fd = os.open(
            str(self._token_file),
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            stat.S_IRUSR | stat.S_IWUSR,
        )
        try:
            os.write(fd, token.encode("utf-8"))
        finally:
            os.close(fd)
        self._token_ref = credential_ref
        self._token_written_at = time.monotonic()

    def start_env(self) -> dict[str, str]:
        """Env for ``ray start``: token auth on, token supplied via file path."""
        env = {"RAY_AUTH_MODE": "token"}
        env["RAY_AUTH_TOKEN_PATH"] = str(self._token_file)
        return env

    async def ensure_runtime(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Check whether the Ray CLI for the requested version is present.

        The agent packages the matching SDK, so the interesting facts are the
        CLI location plus the packaged SDK version (the same wheel, so parity
        by construction).  A missing CLI is reported as ``installed=False``:
        bootstrap must use a verified binary, never whatever ``ray`` happens
        to be on PATH first.
        """
        spec = EnsureRayRuntimePayload.model_validate(payload)
        ray_bin = self._runtime.ray_binary_path(spec.version)
        installed = ray_bin.exists()
        return {
            "installed": installed,
            "version": spec.version,
            "path": str(ray_bin.parent.parent),
            "cli_path": str(ray_bin),
            "sdk_version": _sdk_version(),
        }

    async def start_head(self, payload: dict[str, Any], emit_progress: Any) -> dict[str, Any]:
        """Start a Ray head node (CLI bootstrap).

        The Dashboard is optional (payload ``include_dashboard``): when
        False the node boots GCS + raylet only, which is all the SDK status
        path needs.
        """
        spec = StartRayHeadPayload.model_validate(payload)
        await emit_progress(
            {"phase": "starting_head", "message": f"Starting Ray head on port {spec.port}"}
        )

        # Ensure a live, 0600 cluster token file exists (fetch + write).
        await self._write_token_securely(payload)

        try:
            result = await self._runtime.start_head(
                version=spec.version,
                port=spec.port,
                dashboard_port=spec.dashboard_port,
                dashboard_host=spec.dashboard_host,
                num_cpus=spec.num_cpus,
                num_gpus=spec.num_gpus,
                include_dashboard=spec.include_dashboard,
                env=self.start_env(),
            )
        except errors.RayRuntimeError:
            last = self._runtime.last_error
            raise RuntimeError(
                f"Ray head start failed (version {spec.version}): "
                f"{last.stderr if last else 'launch error'}"
            ) from None

        return {
            "cluster_address": f"{spec.dashboard_host}:{spec.port}",
            "dashboard_url": result.get("dashboard_url"),
        }

    async def join_cluster(self, payload: dict[str, Any], emit_progress: Any) -> dict[str, Any]:
        """Join a Ray cluster as a worker (CLI bootstrap)."""
        spec = JoinRayClusterPayload.model_validate(payload)
        await emit_progress(
            {"phase": "joining_cluster", "message": f"Joining Ray cluster at {spec.head_address}"}
        )

        # Workers must hold the same token so the head's GCS accepts them.
        await self._write_token_securely(payload)

        try:
            result = await self._runtime.join_cluster(
                version=spec.version,
                head_address=spec.head_address,
                node_ip_address=spec.node_ip_address,
                num_cpus=spec.num_cpus,
                num_gpus=spec.num_gpus,
                env=self.start_env(),
            )
        except errors.RayRuntimeError:
            last = self._runtime.last_error
            raise RuntimeError(
                f"Ray join failed (version {spec.version}): "
                f"{last.stderr if last else 'launch error'}"
            ) from None

        return {"joined": True, "head_address": spec.head_address}

    async def leave_cluster(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Stop Ray processes on this node (leave the cluster)."""
        version = payload.get("version") or _DEFAULT_VERSION
        try:
            self._core.disconnect()  # a stale attach must not outlive the cluster
        except Exception:  # pragma: no cover - best effort
            pass
        await self._runtime.stop(version=version)
        return {"left": True}

    async def stop_ray(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Force stop Ray processes."""
        spec = StopRayPayload.model_validate(payload)
        try:
            self._core.disconnect()
        except Exception:  # pragma: no cover - best effort
            pass
        result = await self._runtime.stop(version=spec.version, force=spec.force)

        if self._token_file.exists():
            self._token_file.unlink()
        self._token_ref = None
        self._token_written_at = 0.0

        return {"stopped": result.get("stopped", False) or result.get("best_effort", False)}

    # ------------------------------------------------------------------
    # Status (SDK-first, Dashboard-independent)
    # ------------------------------------------------------------------

    async def get_status(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Cluster status via the Ray Core Python APIs (GCS-direct).

        Works with the Dashboard component disabled.  The result is the
        enriched :class:`RayEnvironmentStatus` dict: flat fields preserved
        for the existing backend parser, with ``serve`` / ``metrics`` /
        ``state`` tiers additive and non-gating (an unhealthy tier never
        flips overall cluster health).
        """
        spec = GetRayStatusPayload.model_validate(payload)

        # Attach + probe are short synchronous GCS round-trips; run them off
        # the event loop so a stuck GCS connection cannot wedge the agent.
        status = await asyncio.to_thread(
            self._core.probe, expected_version=spec.expected_version
        )

        # Additive Serve tier (never fails overall health).
        if spec.include_serve:
            serve = await asyncio.to_thread(self._serve.status)
            status.serve = serve
            status.capabilities.serve = serve and serve.available

        # Additive Tier C metrics targets (discovered from ``ray.nodes()``).
        if spec.include_metrics:
            status.metrics = await asyncio.to_thread(self._metrics.discover)
            status.capabilities.metrics = status.metrics.enabled

        # Optional Tier B state diagnostics (Dashboard-dependent).
        if spec.include_state:
            status.state = await asyncio.to_thread(self._state_diag.availability)
            status.capabilities.state = status.state is not None and status.state.available

        return status.model_dump(mode="json")

    async def get_serve_status(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Serve-tier status command (additive; Dashboard-independent).

        The Serve control plane is a regular Ray job in the cluster — its
        status comes from the Python API, so this works with the dashboard
        disabled too.
        """
        spec = GetRayServeStatusPayload.model_validate(payload)
        serve = await asyncio.to_thread(self._serve.status)
        apps: dict[str, Any] = {}
        if serve is not None and serve.available:
            apps = serve.apps
        if spec.app_name is not None:
            apps = {spec.app_name: apps[spec.app_name]} if spec.app_name in apps else {}
        dump = serve.model_dump(mode="json") if serve is not None else {}
        return {
            "alive": bool(serve is not None and serve.available),
            "serve": dict(dump) | {"apps": apps},
        }


def _sdk_version() -> str | None:
    """Version of the Ray SDK the agent packages (``ray.__version__``)."""
    try:
        import ray  # noqa: PLC0415  - packaged with the agent

        return getattr(ray, "__version__", None)
    except Exception:
        return None

