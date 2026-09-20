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
from llm_port_node_agent.ray.container import (
    RayContainerRuntime,
    RuntimeBundleSpec,
)
from llm_port_node_agent.ray.core import RayCoreClient
from llm_port_node_agent.ray.metrics import RayMetricsDiscovery
from llm_port_node_agent.ray.runtime import RayRuntime
from llm_port_node_agent.ray.schemas import (
    DeleteServeAppPayload,
    EnsureRayRuntimePayload,
    JoinRayClusterPayload,
    RunServeAppPayload,
    StartRayHeadPayload,
    StopRayPayload,
    GetRayServeStatusPayload,
    GetRayStatusPayload,
)
from llm_port_node_agent.ray.serve import RayServeManager
from llm_port_node_agent.runtimes import ContainerRuntimeError
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
        token_env = os.getenv("LLM_PORT_NODE_AGENT_RAY_TOKEN_DIR")
        if token_env:
            token_dir = token_env
        else:
            try:
                Path(token_dir).mkdir(parents=True, exist_ok=True)
            except OSError:
                token_dir = "/tmp/llm-port/ray"
        self._token_dir = Path(token_dir)
        self._token_file = self._token_dir / "cluster.token"
        self._container = RayContainerRuntime(token_path=str(self._token_file))
        # The agent's own SDK attach must present the cluster token.  Ray
        # resolves its auth mode in native code when the SDK is first loaded,
        # so setting these just before ``ray.init`` is ignored (verified live:
        # InvalidAuthToken) — they must be in the process env before anything
        # imports ``ray``, i.e. now.  The file itself only has to exist by
        # attach time (START_RAY_HEAD / JOIN_RAY_CLUSTER write it).
        os.environ.setdefault("RAY_AUTH_MODE", "token")
        os.environ.setdefault("RAY_AUTH_TOKEN_PATH", str(self._token_file))
        self._core = RayCoreClient(token_path=self._token_file)
        self._serve = RayServeManager(core=self._core)
        self._metrics = RayMetricsDiscovery(core=self._core)
        self._state_diag = RayStateDiagnostics(core=self._core)
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

    # Auth is the agent's to decide; a bundle or environment must not be able
    # to point ``ray start`` at a different token or turn auth off.
    _RESERVED_ENV_KEYS = frozenset({"RAY_AUTH_MODE", "RAY_AUTH_TOKEN_PATH", "CUDA_VISIBLE_DEVICES"})
    _ALLOWED_ENV_PREFIXES = ("VLLM_", "HF_", "NCCL_", "CUDA_", "RAY_", "UCX_")

    def start_env(self, extra_env: dict[str, str] | None = None) -> dict[str, str]:
        """Env for ``ray start``: token auth on, token supplied via file path.

        ``RAY_*`` is allowed through (minus the two auth keys) because the
        bundle's certified platform tuning lives there — the GB10 workaround is
        ``RAY_memory_monitor_refresh_ms=0``, and filtering the whole prefix out
        meant the one setting certification proved necessary never reached
        ``ray start``.
        """
        env = {"RAY_AUTH_MODE": "token"}
        env["RAY_AUTH_TOKEN_PATH"] = str(self._token_file)
        if extra_env:
            for k, v in extra_env.items():
                if k.startswith(self._ALLOWED_ENV_PREFIXES) and k not in self._RESERVED_ENV_KEYS:
                    env[k] = str(v)
        return env

    async def _use_container(self, payload: dict[str, Any]) -> bool:
        """Should this command be served from the runtime container?

        The backend decides, by putting ``runtime_bundle`` on the command; the
        running-container check is only a sanity assertion on top of that.
        Inferring the mode from local state instead would let a leftover
        container from a previous environment hijack a host-based one, and
        would silently fall back to the host SDK - which on a certified node
        has no Ray at all - whenever the container happened to be restarting.
        """
        bundle = self._bundle_spec(payload)
        if bundle is None:
            return False
        if await self._container.is_running(bundle.name):
            return True
        raise RuntimeError(
            f"Runtime container {bundle.name!r} is not running; "
            "cannot serve this command from the pinned runtime."
        )

    @staticmethod
    def _bundle_spec(payload: dict[str, Any]) -> RuntimeBundleSpec | None:
        """Parse the optional runtime bundle carried by a lifecycle command.

        Its presence is what selects the containerized bootstrap path; without
        it the manager keeps using the host Ray distribution (the Phase 2/3
        behaviour, still valid on nodes that have one).
        """
        raw = payload.get("runtime_bundle")
        if not isinstance(raw, dict) or not raw:
            return None
        return RuntimeBundleSpec.from_payload(raw)

    async def ensure_runtime_image(self, payload: dict[str, Any], emit_progress: Any = None) -> dict[str, Any]:
        """``ENSURE_RUNTIME_IMAGE``: make the pinned OCI image present, verified.

        Never reaches a public registry: the image is either already on the
        node under its pinned identity, or it is streamed from the backend's
        own image endpoint and loaded locally.
        """
        spec = self._bundle_spec(payload)
        if spec is None:
            raise RuntimeError("ensure_runtime_image requires a runtime_bundle payload")
        result = await self._container.ensure_image(
            spec, loader=self._image_loader(), emit_progress=emit_progress,
        )
        if payload.get("ensure_container"):
            result.update(await self._container.ensure_container(spec))
        return result

    def _image_loader(self) -> Any:
        """Loader that streams the pinned image from the backend (air-gap path)."""

        async def _load(spec: RuntimeBundleSpec) -> None:
            from llm_port_node_agent.image_loader import load_image_from_backend
            from llm_port_node_agent.runtimes import detect_runtime

            credential = self._state.state.credential
            if not credential:
                raise RuntimeError(
                    "Node credential not recorded; cannot stream the runtime image"
                )
            await load_image_from_backend(
                client=self._http_client(),
                credential=credential,
                image=spec.image,
                runtime=detect_runtime(preferred=spec.runtime_handler),
            )

        return _load

    async def ensure_runtime(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Check that a usable Ray runtime is available for the requested version.

        With a runtime bundle the check runs against the **container**: the
        image must be present under its pinned digest and its ``ray`` CLI must
        answer.  That is the Phase 4B contract — a node with no host Ray
        package still reports ``installed=True``.

        Without a bundle this falls back to the host CLI: the agent packages
        the matching SDK, so the interesting facts are the CLI location plus
        the packaged SDK version (same wheel, parity by construction).  A
        missing CLI is reported as ``installed=False``: bootstrap must use a
        verified binary, never whatever ``ray`` happens to be on PATH first.
        """
        bundle = self._bundle_spec(payload)
        if bundle is not None:
            return await self._ensure_runtime_container(payload, bundle)

        spec = EnsureRayRuntimePayload.model_validate(payload)
        ray_bin = self._runtime.ray_binary_path(spec.version)
        installed = ray_bin.exists()
        # Detect the Serve-LLM stack from installed distribution metadata
        # instead of importing it: importing vLLM/torch in the agent process
        # takes seconds on every ENSURE (blocking the command loop) and loads
        # CUDA libraries the agent never uses.
        vllm_version = _dist_version("vllm")
        torch_version = _dist_version("torch")
        serve_llm_available = bool(vllm_version and _dist_version("pyarrow"))

        return {
            "installed": installed,
            "version": spec.version,
            "path": str(ray_bin.parent.parent),
            "cli_path": str(ray_bin),
            "sdk_version": _sdk_version(),
            "serve_llm_available": serve_llm_available,
            "vllm_version": vllm_version,
            "torch_version": torch_version,
        }

    async def _ensure_runtime_container(
        self, payload: dict[str, Any], bundle: RuntimeBundleSpec
    ) -> dict[str, Any]:
        """Satisfy ``ENSURE_RAY_RUNTIME`` from the pinned container."""
        spec = EnsureRayRuntimePayload.model_validate(
            {k: v for k, v in payload.items() if k in {"version"}}
        )
        try:
            image = await self._container.ensure_image(bundle, loader=self._image_loader())
            await self._container.ensure_container(bundle)
            version = await self._container.installed_version(bundle)
        except (ContainerRuntimeError, ValueError) as exc:
            return {
                "installed": False,
                "version": spec.version,
                "runtime": "container",
                "container": bundle.name,
                "image": bundle.image,
                "error": str(exc),
            }
        return {
            "installed": version is not None,
            "version": version or spec.version,
            "requested_version": spec.version,
            "version_match": version == spec.version if version else False,
            "runtime": "container",
            "container": bundle.name,
            "image": bundle.image,
            "image_id": image.get("image_id"),
            "digest_verified": bool(image.get("verified")),
            # The certified image ships the full Serve-LLM stack; that is the
            # bundle's contract, not something to re-derive from host metadata.
            "serve_llm_available": True,
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

        bundle = self._bundle_spec(payload)
        if bundle is not None:
            await self._container.ensure_image(bundle, loader=self._image_loader())
            await self._container.ensure_container(bundle)
            try:
                result = await self._container.start_head(
                    bundle,
                    port=spec.port,
                    dashboard_port=spec.dashboard_port,
                    dashboard_host=spec.dashboard_host,
                    node_ip_address=spec.node_ip_address,
                    num_cpus=spec.num_cpus,
                    num_gpus=spec.num_gpus,
                    include_dashboard=spec.include_dashboard,
                    env=self.start_env(spec.env),
                )
            except ContainerRuntimeError as exc:
                raise RuntimeError(f"Ray head start failed in container: {exc}") from None
            return {
                "cluster_address": result.get("cluster_address"),
                "head_address": result.get("head_address"),
                "dashboard_url": result.get("dashboard_url"),
                "runtime": "container",
                "container": bundle.name,
            }

        try:
            result = await self._runtime.start_head(
                version=spec.version,
                port=spec.port,
                dashboard_port=spec.dashboard_port,
                dashboard_host=spec.dashboard_host,
                node_ip_address=spec.node_ip_address,
                num_cpus=spec.num_cpus,
                num_gpus=spec.num_gpus,
                include_dashboard=spec.include_dashboard,
                env=self.start_env(spec.env),
            )
        except errors.RayRuntimeError:
            last = self._runtime.last_error
            raise RuntimeError(
                f"Ray head start failed (version {spec.version}): "
                f"{last.stderr if last else 'launch error'}"
            ) from None

        resolved_host = spec.node_ip_address or spec.dashboard_host
        return {
            "cluster_address": result.get("cluster_address", f"{resolved_host}:{spec.port}"),
            "head_address": result.get("head_address", f"{resolved_host}:{spec.port}"),
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

        bundle = self._bundle_spec(payload)
        if bundle is not None:
            await self._container.ensure_image(bundle, loader=self._image_loader())
            await self._container.ensure_container(bundle)
            try:
                await self._container.join_cluster(
                    bundle,
                    head_address=spec.head_address,
                    node_ip_address=spec.node_ip_address,
                    num_cpus=spec.num_cpus,
                    num_gpus=spec.num_gpus,
                    env=self.start_env(spec.env),
                )
            except ContainerRuntimeError as exc:
                raise RuntimeError(f"Ray join failed in container: {exc}") from None
            return {
                "joined": True,
                "head_address": spec.head_address,
                "runtime": "container",
                "container": bundle.name,
            }

        try:
            result = await self._runtime.join_cluster(
                version=spec.version,
                head_address=spec.head_address,
                node_ip_address=spec.node_ip_address,
                num_cpus=spec.num_cpus,
                num_gpus=spec.num_gpus,
                env=self.start_env(spec.env),
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
        bundle = self._bundle_spec(payload)
        if bundle is not None:
            result = await self._container.stop(bundle, remove=True)
            return {"left": True, "runtime": "container", **result}
        await self._runtime.stop(version=version)
        return {"left": True}

    async def stop_ray(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Force stop Ray processes."""
        spec = StopRayPayload.model_validate(payload)
        try:
            self._core.disconnect()
        except Exception:  # pragma: no cover - best effort
            pass
        bundle = self._bundle_spec(payload)
        if bundle is not None:
            result = await self._container.stop(bundle, force=spec.force, remove=True)
        else:
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
        if spec.credential_ref:
            try:
                await self._ensure_token({"credential_ref": spec.credential_ref})
            except Exception as e:
                log.warning("Could not ensure token for get_status: %s", e)

        # Containerized runtime: every Ray-aware call goes through the
        # in-container helper, with the same tiers the host path reports.
        if await self._use_container(payload):
            bundle = self._bundle_spec(payload)
            return await self._container.get_cluster_status(
                bundle.name,
                include_serve=spec.include_serve,
                include_metrics=spec.include_metrics,
                include_state=spec.include_state,
                expected_version=spec.expected_version,
            )

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

    async def run_serve_app(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Deploy (or update) a named LLM Serve application (Phase 3).

        The head node builds the ingress application *in-process* with
        ``ray.serve.llm.build_openai_app`` and runs it under an explicit
        application name — Ray's per-application update path.  The deploy is
        non-blocking: convergence is observed through the serve status tier.
        """
        spec = RunServeAppPayload.model_validate(payload)
        if await self._use_container(payload):
            bundle = self._bundle_spec(payload)
            return await self._container.run_serve_app(
                bundle.name,
                spec.app_name,
                spec.llm_serving_args,
                http_options=spec.http_options,
            )

        # build_openai_app + serve.run are driver-side Python API calls that
        # do GCS round-trips; run off the event loop.
        return await asyncio.to_thread(
            self._serve.run_app,
            spec.app_name,
            spec.llm_serving_args,
            proxy_location=spec.proxy_location,
            http_options=spec.http_options,
        )

    async def delete_serve_app(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Delete a named Serve application (Phase 3)."""
        spec = DeleteServeAppPayload.model_validate(payload)
        if await self._use_container(payload):
            bundle = self._bundle_spec(payload)
            return await self._container.delete_serve_app(
                bundle.name,
                spec.app_name,
            )
        return await asyncio.to_thread(self._serve.delete_app, spec.app_name)

    async def get_serve_status(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Serve-tier status command (additive; Dashboard-independent).

        The Serve control plane is a regular Ray job in the cluster — its
        status comes from the Python API, so this works with the dashboard
        disabled too.
        """
        spec = GetRayServeStatusPayload.model_validate(payload)
        if spec.credential_ref:
            try:
                await self._ensure_token({"credential_ref": spec.credential_ref})
            except Exception as e:
                log.warning("Could not ensure token for get_serve_status: %s", e)

        if await self._use_container(payload):
            bundle = self._bundle_spec(payload)
            serve_data = await self._container.get_serve_status(
                bundle.name, app_name=spec.app_name,
            )
            # The helper reports Serve's own ``applications`` key; the wire
            # contract for this command is ``apps``.
            apps = serve_data.get("apps") or serve_data.get("applications") or {}
            if spec.app_name is not None:
                apps = {spec.app_name: apps[spec.app_name]} if spec.app_name in apps else {}
            return {
                "available": bool(serve_data.get("available", False)),
                "apps": apps,
                "detail": serve_data.get("error") or serve_data.get("detail"),
            }

        serve = await asyncio.to_thread(self._serve.status)
        apps: dict[str, Any] = {}
        if serve is not None and serve.available:
            for k, v in (serve.apps or {}).items():
                if hasattr(v, "model_dump"):
                    apps[k] = v.model_dump(mode="json")
                else:
                    apps[k] = dict(v) if isinstance(v, dict) else v
        if spec.app_name is not None:
            apps = {spec.app_name: apps[spec.app_name]} if spec.app_name in apps else {}
        dump = serve.model_dump(mode="json") if serve is not None else {}
        dump["apps"] = apps
        return {
            "alive": bool(serve is not None and serve.available),
            "serve": dump,
        }


def _dist_version(name: str) -> str | None:
    """Installed version of distribution *name* (no import), else ``None``."""
    from importlib import metadata  # noqa: PLC0415

    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def _sdk_version() -> str | None:
    """Version of the Ray SDK the agent packages (``ray.__version__``)."""
    try:
        import ray  # noqa: PLC0415  - packaged with the agent

        return getattr(ray, "__version__", None)
    except Exception:
        return None

