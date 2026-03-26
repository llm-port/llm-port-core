"""Workload lifecycle management backed by a pluggable container runtime."""

from __future__ import annotations

import logging
import shlex
from collections.abc import Awaitable, Callable
from typing import Any

ProgressEmitter = Callable[[dict[str, Any]], Awaitable[None]]

from llm_port_node_agent.event_buffer import EventBuffer
from llm_port_node_agent.runtimes import ContainerRuntime
from llm_port_node_agent.state_store import StateStore

log = logging.getLogger(__name__)


class RuntimeManagerError(RuntimeError):
    """Raised when local runtime operations fail."""


class RuntimeManager:
    """Executes workload lifecycle operations via a pluggable container runtime."""

    _DEFAULT_IMAGES = {
        "vllm": "vllm/vllm-openai:latest",
        "llamacpp": "ghcr.io/ggerganov/llama.cpp:server",
        "tgi": "ghcr.io/huggingface/text-generation-inference:latest",
        "ollama": "ollama/ollama:latest",
    }

    _DENIED_DOCKER_FLAGS = {
        "--privileged",
        "--cap-add",
        "--pid",
        "--security-opt",
        "--device",
        "--userns",
        "--ipc=host",
    }

    def __init__(
        self,
        *,
        runtime: ContainerRuntime,
        state_store: StateStore,
        events: EventBuffer,
        advertise_host: str,
        advertise_scheme: str = "http",
        model_store_root: str = "/srv/llm-port/models",
        model_puller: Callable[..., Any] | None = None,
        image_loader: Callable[..., Any] | None = None,
    ) -> None:
        self._runtime = runtime
        self._state = state_store
        self._events = events
        self._advertise_host = advertise_host.strip() or "127.0.0.1"
        self._advertise_scheme = advertise_scheme.strip().lower() or "http"
        self._model_store_root = model_store_root
        self._model_puller = model_puller
        self._image_loader = image_loader

    async def deploy_workload(
        self, payload: dict[str, Any], *, emit_progress: ProgressEmitter | None = None,
    ) -> dict[str, Any]:
        """Create and start a Docker container for a runtime."""

        async def _progress(phase: str, message: str) -> None:
            if emit_progress is not None:
                await emit_progress({"phase": phase, "message": message})

        runtime_id = str(payload.get("runtime_id") or "").strip()
        if not runtime_id:
            raise RuntimeManagerError("runtime_id is required for deploy_workload.")

        provider_type = str(payload.get("provider_type") or "vllm").strip().lower()
        provider_config = payload.get("provider_config")
        provider_config = provider_config if isinstance(provider_config, dict) else {}
        image = (
            str(payload.get("image") or "").strip()
            or str(provider_config.get("image") or "").strip()
            or self._DEFAULT_IMAGES.get(provider_type, "")
        )
        if not image:
            raise RuntimeManagerError("No image provided for workload deployment.")

        await _progress("validate", f"Validated payload — image={image}, provider={provider_type}")

        runtime_name = str(payload.get("runtime_name") or runtime_id)
        container_name = str(payload.get("container_name") or "").strip() or self._container_name(runtime_id, runtime_name=runtime_name)

        await _progress("remove_stale", f"Removing stale container {container_name} (if exists)")
        if await self._runtime.exists(container_name):
            await self._runtime.remove(container_name)

        command_override = provider_config.get("command")
        cmd: list[str] = []
        if isinstance(command_override, list):
            cmd = [str(item) for item in command_override]
        elif isinstance(command_override, str) and command_override.strip():
            cmd = shlex.split(command_override)

        self._sanitize_command_args(cmd)

        # GPU passthrough
        gpu_request = str(payload.get("gpu_request") or provider_config.get("gpu_request") or "").strip()
        # Default to all GPUs for engines that always require a GPU
        if not gpu_request and provider_type in ("vllm", "tgi"):
            gpu_request = "all"

        # Resource limits
        container_port = str(payload.get("container_port") or provider_config.get("container_port") or "8000").strip()
        memory_limit = str(payload.get("memory_limit") or provider_config.get("memory_limit") or "").strip()
        cpu_limit = str(payload.get("cpu_limit") or provider_config.get("cpu_limit") or "").strip()
        shm_size = str(payload.get("shm_size") or provider_config.get("shm_size") or "").strip()
        ipc_mode = str(payload.get("ipc_mode") or provider_config.get("ipc_mode") or "").strip()
        # Default --shm-size=1g and --ipc=host for GPU workloads (required by vLLM/TGI)
        if not shm_size and gpu_request:
            shm_size = "1g"
        if not ipc_mode and gpu_request and provider_type in ("vllm", "tgi"):
            ipc_mode = "host"

        # ── Model sync — pull model files from backend if needed ──
        model_sync = payload.get("model_sync")
        hf_cache_mount: str | None = None
        hf_offline = False
        if isinstance(model_sync, dict) and model_sync.get("hf_repo_id"):
            source = model_sync.get("source", "sync_from_server")

            if source == "sync_from_server" and model_sync.get("blobs"):
                # Pull blobs from the backend's file server
                if self._model_puller is not None:
                    hf_repo = model_sync.get("hf_repo_id", "unknown")
                    await _progress("sync_model", f"Syncing model files for {hf_repo} from backend")
                    log.info("Pulling model files for %s", hf_repo)
                    try:
                        await self._model_puller(model_sync=model_sync)
                    except Exception as exc:
                        raise RuntimeManagerError(f"Model sync failed: {exc}") from exc
                hf_cache_mount = "/data/hf-cache"
                hf_offline = True

            elif source == "download_from_hf":
                # Node has internet — mount cache volume, container downloads on start
                await _progress("sync_model", "Model will be downloaded from HuggingFace by the container")
                hf_cache_mount = "/data/hf-cache"
                hf_offline = False
        else:
            await _progress("sync_model", "No model sync required")

        # ── Ensure the container image is available locally ───────
        image_source = str(payload.get("image_source") or "").strip()
        if image_source == "pull_from_registry":
            await _progress("pull_image", f"Pulling image {image} from registry")
            log.info("Pulling image %s from registry on node", image)
            await self._runtime.pull(image, timeout_sec=1800)
        elif image_source == "transfer_from_server":
            await _progress("pull_image", f"Transferring image {image} from backend")
            log.info("Loading image %s from backend transfer", image)
            await self._load_image_from_backend(image, payload)
        else:
            await _progress("pull_image", "Using locally available image")

        await _progress("start_container", f"Starting container {container_name}")

        # Build env dict and extra_args for the runtime
        run_env: dict[str, str] = {
            "LLM_PORT_RUNTIME_ID": runtime_id,
            "LLM_PORT_PROVIDER_TYPE": provider_type,
        }
        run_volumes: list[str] = []
        extra_args: list[str] = []

        if memory_limit:
            extra_args.extend(["--memory", memory_limit])
        if cpu_limit:
            extra_args.extend(["--cpus", cpu_limit])
        if shm_size:
            extra_args.extend(["--shm-size", shm_size])
        if ipc_mode:
            extra_args.extend(["--ipc", ipc_mode])

        # ── Mount model cache if model was synced ─────────────────
        if hf_cache_mount:
            host_hf_path = self._model_store_root
            run_volumes.append(f"{host_hf_path}:{hf_cache_mount}")
            run_env["HF_HUB_CACHE"] = hf_cache_mount
            if hf_offline:
                run_env["HF_HUB_OFFLINE"] = "1"
                run_env["TRANSFORMERS_OFFLINE"] = "1"

        container_id = await self._runtime.run(
            image=image,
            name=container_name,
            ports=[container_port],
            env=run_env,
            gpus=gpu_request or None,
            volumes=run_volumes or None,
            command=cmd or None,
            extra_args=extra_args or None,
            timeout_sec=120,
        )

        await _progress("resolve_endpoint", "Resolving container endpoint")
        endpoint = await self._resolve_endpoint(container_name, container_port=container_port)

        self._state.set_workload(
            runtime_id,
            {
                "runtime_name": runtime_name,
                "container_name": container_name,
                "container_id": container_id,
                "provider_type": provider_type,
                "image": image,
                "container_port": container_port,
                "endpoint_url": endpoint,
            },
        )
        self._events.add(
            event_type="workload.deployed",
            payload={"runtime_id": runtime_id, "container_name": container_name, "endpoint_url": endpoint},
            correlation_id=runtime_id,
        )
        await _progress("ready", f"Container started — endpoint {endpoint or 'pending'}")
        return {
            "runtime_id": runtime_id,
            "container_name": container_name,
            "container_id": container_id,
            "endpoint_url": endpoint,
        }

    async def sync_model(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Pull model files from the backend without starting a container."""
        model_sync = payload.get("model_sync")
        if not isinstance(model_sync, dict) or not model_sync.get("blobs"):
            raise RuntimeManagerError("model_sync payload with files is required.")
        if self._model_puller is None:
            raise RuntimeManagerError("Model puller not configured.")
        await self._model_puller(model_sync=model_sync)
        return {
            "hf_repo_id": model_sync.get("hf_repo_id", ""),
            "files_synced": len(model_sync.get("files", [])),
        }

    async def start_workload(
        self, payload: dict[str, Any], *, emit_progress: ProgressEmitter | None = None,
    ) -> dict[str, Any]:
        """Start an existing workload container (deploy if missing)."""
        runtime_id = self._require_runtime_id(payload)
        container_name = self._lookup_container(runtime_id, payload, allow_missing=True)
        # If no container exists yet, fall back to a full deploy
        if not container_name or not await self._runtime.exists(container_name):
            log.info("Container not found for %s — falling back to deploy", runtime_id)
            return await self.deploy_workload(payload, emit_progress=emit_progress)
        await self._runtime.start(container_name)
        endpoint = await self._resolve_endpoint(container_name)
        self._events.add(
            event_type="workload.started",
            payload={"runtime_id": runtime_id, "container_name": container_name},
            correlation_id=runtime_id,
        )
        return {"runtime_id": runtime_id, "container_name": container_name, "endpoint_url": endpoint}

    async def stop_workload(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Stop a running workload container."""
        runtime_id = self._require_runtime_id(payload)
        container_name = self._lookup_container(runtime_id, payload)
        await self._runtime.stop(container_name)
        self._events.add(
            event_type="workload.stopped",
            payload={"runtime_id": runtime_id, "container_name": container_name},
            correlation_id=runtime_id,
        )
        return {"runtime_id": runtime_id, "container_name": container_name}

    async def restart_workload(
        self, payload: dict[str, Any], *, emit_progress: ProgressEmitter | None = None,
    ) -> dict[str, Any]:
        """Restart a workload container (deploy if missing)."""
        runtime_id = self._require_runtime_id(payload)
        container_name = self._lookup_container(runtime_id, payload, allow_missing=True)
        if not container_name or not await self._runtime.exists(container_name):
            log.info("Container not found for %s — falling back to deploy", runtime_id)
            return await self.deploy_workload(payload, emit_progress=emit_progress)
        await self._runtime.restart(container_name)
        endpoint = await self._resolve_endpoint(container_name)
        self._events.add(
            event_type="workload.restarted",
            payload={"runtime_id": runtime_id, "container_name": container_name},
            correlation_id=runtime_id,
        )
        return {"runtime_id": runtime_id, "container_name": container_name, "endpoint_url": endpoint}

    async def remove_workload(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Remove a workload container and local mapping."""
        runtime_id = self._require_runtime_id(payload)
        container_name = self._lookup_container(runtime_id, payload, allow_missing=True)
        if container_name:
            await self._runtime.remove(container_name)
        self._state.drop_workload(runtime_id)
        self._events.add(
            event_type="workload.removed",
            payload={"runtime_id": runtime_id, "container_name": container_name},
            correlation_id=runtime_id,
        )
        return {"runtime_id": runtime_id, "container_name": container_name}

    async def update_workload(
        self, payload: dict[str, Any], *, emit_progress: ProgressEmitter | None = None,
    ) -> dict[str, Any]:
        """Apply update by replacing the existing container."""
        runtime_id = self._require_runtime_id(payload)
        await self.remove_workload(payload)
        return await self.deploy_workload(payload, emit_progress=emit_progress)

    async def collect_diagnostics(self) -> dict[str, Any]:
        """Return basic diagnostics without mutating workloads."""
        rows_ps = await self._runtime.ps()
        rows_images = await self._runtime.images()
        return {
            "docker_ps_rows": rows_ps[:200],
            "docker_images_rows": rows_images[:200],
        }

    async def fetch_container_logs(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Fetch recent container logs for a runtime."""
        runtime_id = self._require_runtime_id(payload)
        container_name = self._lookup_container(runtime_id, payload, allow_missing=True)
        if not container_name:
            return {"logs": f"No container found for runtime {runtime_id}. Deploy or update the runtime to create the container."}
        tail = str(payload.get("tail", 300))
        code, combined = await self._runtime.logs(
            container_name,
            tail=tail,
            timestamps=True,
        )
        if code != 0 and "no such container" in combined.lower():
            return {"logs": f"Container {container_name} does not exist on this node. Deploy or update the runtime to create it."}
        return {"logs": combined}

    async def _resolve_endpoint(self, container_name: str, *, container_port: str = "8000") -> str | None:
        host_port = await self._runtime.port(container_name, container_port)
        if not host_port:
            return None
        return f"{self._advertise_scheme}://{self._advertise_host}:{host_port}"

    async def _load_image_from_backend(self, image: str, payload: dict) -> None:
        """Download image tarball from backend and load via ``docker load``."""
        if self._image_loader is None:
            raise RuntimeManagerError("Image loader not configured — cannot transfer image.")
        await self._image_loader(image=image)

    @staticmethod
    def _container_name(runtime_id: str, *, runtime_name: str | None = None) -> str:
        slug = (runtime_name or runtime_id).replace("_", "-").replace("/", "-").replace(" ", "-").lower()
        slug = "".join(ch for ch in slug if ch.isalnum() or ch == "-").strip("-")
        if not slug:
            slug = runtime_id.replace("-", "")
        return f"llm-port-{slug[:48]}"

    @classmethod
    def _sanitize_command_args(cls, args: list[str]) -> None:
        """Reject command args containing dangerous Docker flags."""
        for arg in args:
            normalized = arg.strip().lower()
            for denied in cls._DENIED_DOCKER_FLAGS:
                if normalized == denied or normalized.startswith(denied + "="):
                    raise RuntimeManagerError(
                        f"Blocked Docker flag in command override: {arg}"
                    )
            if normalized.startswith("-v") or normalized.startswith("--volume"):
                raise RuntimeManagerError(
                    f"Volume mounts in command override are not allowed: {arg}"
                )
            if normalized == "--network=host" or (
                normalized == "--network" and "host" in args
            ):
                raise RuntimeManagerError(
                    f"Host network mode in command override is not allowed: {arg}"
                )

    def _lookup_container(
        self,
        runtime_id: str,
        payload: dict[str, Any],
        *,
        allow_missing: bool = False,
    ) -> str | None:
        row = self._state.workload(runtime_id)
        if row and isinstance(row.get("container_name"), str):
            return str(row["container_name"])
        # Prefer backend-provided container name over auto-generation
        explicit = payload.get("container_name")
        if isinstance(explicit, str) and explicit.strip():
            return explicit.strip()
        runtime_name = payload.get("runtime_name")
        if isinstance(runtime_name, str) and runtime_name.strip():
            return self._container_name(runtime_id, runtime_name=runtime_name)
        if allow_missing:
            return None
        raise RuntimeManagerError("Container mapping not found for runtime.")

    @staticmethod
    def _require_runtime_id(payload: dict[str, Any]) -> str:
        runtime_id = str(payload.get("runtime_id") or "").strip()
        if not runtime_id:
            raise RuntimeManagerError("runtime_id is required.")
        return runtime_id
