"""Docker-backed workload lifecycle management."""

from __future__ import annotations

import asyncio
import shlex
from typing import Any

from llm_port_node_agent.event_buffer import EventBuffer
from llm_port_node_agent.state_store import StateStore


class RuntimeManagerError(RuntimeError):
    """Raised when local runtime operations fail."""


class RuntimeManager:
    """Executes workload lifecycle operations using Docker CLI."""

    _DEFAULT_IMAGES = {
        "vllm": "vllm/vllm-openai:latest",
        "llamacpp": "ghcr.io/ggerganov/llama.cpp:server",
        "tgi": "ghcr.io/huggingface/text-generation-inference:latest",
        "ollama": "ollama/ollama:latest",
    }

    def __init__(
        self,
        *,
        state_store: StateStore,
        events: EventBuffer,
        advertise_host: str,
        advertise_scheme: str = "http",
    ) -> None:
        self._state = state_store
        self._events = events
        self._advertise_host = advertise_host.strip() or "127.0.0.1"
        self._advertise_scheme = advertise_scheme.strip().lower() or "http"

    async def deploy_workload(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Create and start a Docker container for a runtime."""
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

        runtime_name = str(payload.get("runtime_name") or runtime_id)
        container_name = self._container_name(runtime_id, runtime_name=runtime_name)

        await self._remove_container_if_exists(container_name)

        command_override = provider_config.get("command")
        cmd: list[str] = []
        if isinstance(command_override, list):
            cmd = [str(item) for item in command_override]
        elif isinstance(command_override, str) and command_override.strip():
            cmd = shlex.split(command_override)

        args = [
            "run",
            "-d",
            "--name",
            container_name,
            "--restart",
            "unless-stopped",
            "-p",
            "8000",
            "-e",
            f"LLM_PORT_RUNTIME_ID={runtime_id}",
            "-e",
            f"LLM_PORT_PROVIDER_TYPE={provider_type}",
            image,
            *cmd,
        ]
        _, out, _ = await self._docker(*args, timeout_sec=120)
        container_id = out.strip().splitlines()[0] if out.strip() else ""
        endpoint = await self._resolve_endpoint(container_name)

        self._state.set_workload(
            runtime_id,
            {
                "runtime_name": runtime_name,
                "container_name": container_name,
                "container_id": container_id,
                "provider_type": provider_type,
                "image": image,
                "endpoint_url": endpoint,
            },
        )
        self._events.add(
            event_type="workload.deployed",
            payload={"runtime_id": runtime_id, "container_name": container_name, "endpoint_url": endpoint},
            correlation_id=runtime_id,
        )
        return {
            "runtime_id": runtime_id,
            "container_name": container_name,
            "container_id": container_id,
            "endpoint_url": endpoint,
        }

    async def start_workload(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Start an existing workload container."""
        runtime_id = self._require_runtime_id(payload)
        container_name = self._lookup_container(runtime_id, payload)
        await self._docker("start", container_name, timeout_sec=30)
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
        await self._docker("stop", container_name, timeout_sec=30)
        self._events.add(
            event_type="workload.stopped",
            payload={"runtime_id": runtime_id, "container_name": container_name},
            correlation_id=runtime_id,
        )
        return {"runtime_id": runtime_id, "container_name": container_name}

    async def restart_workload(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Restart a workload container."""
        runtime_id = self._require_runtime_id(payload)
        container_name = self._lookup_container(runtime_id, payload)
        await self._docker("restart", container_name, timeout_sec=45)
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
            await self._docker("rm", "-f", container_name, timeout_sec=45)
        self._state.drop_workload(runtime_id)
        self._events.add(
            event_type="workload.removed",
            payload={"runtime_id": runtime_id, "container_name": container_name},
            correlation_id=runtime_id,
        )
        return {"runtime_id": runtime_id, "container_name": container_name}

    async def update_workload(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Apply update by replacing the existing container."""
        runtime_id = self._require_runtime_id(payload)
        await self.remove_workload({"runtime_id": runtime_id})
        return await self.deploy_workload(payload)

    async def collect_diagnostics(self) -> dict[str, Any]:
        """Return basic diagnostics without mutating workloads."""
        _, ps_out, _ = await self._docker("ps", "-a", "--format", "{{json .}}", timeout_sec=20)
        _, images_out, _ = await self._docker("images", "--format", "{{json .}}", timeout_sec=20)
        rows_ps = [line for line in ps_out.splitlines() if line.strip()]
        rows_images = [line for line in images_out.splitlines() if line.strip()]
        return {
            "docker_ps_rows": rows_ps[:200],
            "docker_images_rows": rows_images[:200],
        }

    async def _remove_container_if_exists(self, container_name: str) -> None:
        code, _, _ = await self._docker("inspect", container_name, timeout_sec=8, raise_on_error=False)
        if code == 0:
            await self._docker("rm", "-f", container_name, timeout_sec=45)

    async def _resolve_endpoint(self, container_name: str) -> str | None:
        code, out, _ = await self._docker(
            "port",
            container_name,
            "8000/tcp",
            timeout_sec=10,
            raise_on_error=False,
        )
        if code != 0:
            return None
        value = out.strip().splitlines()
        if not value:
            return None
        host_port = value[0].split(":")[-1].strip()
        if not host_port:
            return None
        return f"{self._advertise_scheme}://{self._advertise_host}:{host_port}"

    async def _docker(
        self,
        *args: str,
        timeout_sec: float = 30,
        raise_on_error: bool = True,
    ) -> tuple[int, str, str]:
        proc = await asyncio.create_subprocess_exec(
            "docker",
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
        except TimeoutError:
            proc.kill()
            await proc.communicate()
            if raise_on_error:
                raise RuntimeManagerError(f"docker {' '.join(args)} timed out.")
            return 124, "", "timeout"
        code = proc.returncode
        out = stdout.decode("utf-8", "replace")
        err = stderr.decode("utf-8", "replace")
        if raise_on_error and code != 0:
            raise RuntimeManagerError(f"docker {' '.join(args)} failed: {err.strip() or out.strip()}")
        return code, out, err

    @staticmethod
    def _container_name(runtime_id: str, *, runtime_name: str | None = None) -> str:
        slug = (runtime_name or runtime_id).replace("_", "-").replace("/", "-").replace(" ", "-").lower()
        slug = "".join(ch for ch in slug if ch.isalnum() or ch == "-").strip("-")
        if not slug:
            slug = runtime_id
        return f"llm-port-{slug[:32]}-{runtime_id.replace('-', '')[:8]}"

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
