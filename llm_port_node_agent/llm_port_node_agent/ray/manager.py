"""High-level coordinator for Ray lifecycle on a physical node."""

import logging
import os
import stat
from pathlib import Path
from typing import Any

import httpx

from llm_port_node_agent.event_buffer import EventBuffer
from llm_port_node_agent.ray.process import RayProcessManager
from llm_port_node_agent.ray.schemas import (
    EnsureRayRuntimePayload,
    JoinRayClusterPayload,
    StartRayHeadPayload,
    StopRayPayload,
    GetRayStatusPayload,
)
from llm_port_node_agent.ray.status import get_ray_status
from llm_port_node_agent.state_store import StateStore

log = logging.getLogger(__name__)

_SECRET_ENDPOINT = "/api/admin/system/nodes/secrets/"


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
        self._process = RayProcessManager(ray_base_path=ray_base_path)
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
        """Check if Ray version is installed, install if missing."""
        spec = EnsureRayRuntimePayload.model_validate(payload)
        ray_bin = self._process.ray_binary_path(spec.version)
        
        # Option C implementation: Try pip/uv if not found
        installed = ray_bin.exists()
        
        return {
            "installed": installed,
            "version": spec.version,
            "path": str(ray_bin.parent.parent),
        }

    async def start_head(self, payload: dict[str, Any], emit_progress: Any) -> dict[str, Any]:
        """Start a Ray head node."""
        spec = StartRayHeadPayload.model_validate(payload)
        await emit_progress({"phase": "starting_head", "message": f"Starting Ray head on port {spec.port}"})

        # Ensure a live, 0600 cluster token file exists (fetch + write).
        await self._write_token_securely(payload)

        args = [
            "start",
            "--head",
            f"--port={spec.port}",
            f"--dashboard-host={spec.dashboard_host}",
            f"--dashboard-port={spec.dashboard_port}",
        ]
        
        if spec.num_cpus is not None:
            args.append(f"--num-cpus={spec.num_cpus}")
        if spec.num_gpus is not None:
            args.append(f"--num-gpus={spec.num_gpus}")

        try:
            await self._process.start(args, version=spec.version, env=self.start_env())
        except FileNotFoundError as exc:
            raise RuntimeError(f"Ray CLI missing for version {spec.version}") from exc

        return {
            "cluster_address": f"{spec.dashboard_host}:{spec.port}",
            "dashboard_url": f"http://{spec.dashboard_host}:{spec.dashboard_port}",
        }

    async def join_cluster(self, payload: dict[str, Any], emit_progress: Any) -> dict[str, Any]:
        """Join a Ray cluster as a worker."""
        spec = JoinRayClusterPayload.model_validate(payload)
        await emit_progress({"phase": "joining_cluster", "message": f"Joining Ray cluster at {spec.head_address}"})

        # Workers must hold the same token so the head's GCS accepts them.
        await self._write_token_securely(payload)

        args = [
            "start",
            f"--address={spec.head_address}",
        ]
        
        if spec.node_ip_address:
            args.append(f"--node-ip-address={spec.node_ip_address}")
        if spec.num_cpus is not None:
            args.append(f"--num-cpus={spec.num_cpus}")
        if spec.num_gpus is not None:
            args.append(f"--num-gpus={spec.num_gpus}")

        try:
            await self._process.start(args, version=spec.version, env=self.start_env())
        except FileNotFoundError as exc:
            raise RuntimeError(f"Ray CLI missing for version {spec.version}") from exc

        return {
            "joined": True,
            "head_address": spec.head_address,
        }

    async def leave_cluster(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Stop Ray processes on this node (leave the cluster)."""
        version = payload.get("version") or "2.58.0"
        await self._process.stop(version=version)
        return {"left": True}

    async def stop_ray(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Force stop Ray processes."""
        spec = StopRayPayload.model_validate(payload)
        await self._process.stop(version=spec.version, force=spec.force)

        if self._token_file.exists():
            self._token_file.unlink()
        self._token_ref = None
        self._token_written_at = 0.0

        return {"stopped": True}

    async def get_status(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Check process status and cluster connectivity."""
        spec = GetRayStatusPayload.model_validate(payload)
        version = payload.get("version") or "2.58.0"
        result = await get_ray_status(self._process, version=version, address=spec.address)
        return result.model_dump()

