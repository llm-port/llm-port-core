"""Containerized Ray bootstrap — the Phase 4B runtime-bundle execution path.

Host boundary (03_PHASED_MIGRATION_PLAN.md §4B):

    Host Node Agent   : inventory, OCI digest presence/load, container
                        lifecycle, mounts/network, artifact coordination,
                        exec runtime helper.
    Runtime container : Ray SDK, Ray/Serve, vLLM/PyTorch/CUDA/NCCL,
                        ``llm_port_ray_runtime``.

The host therefore never needs a Ray Python distribution: on certified
hardware ``import ray`` fails on the DGX OS by design, and the whole air-gap
direction rests on the runtime living inside a digest-pinned image.

This module is the mirror image of :class:`~llm_port_node_agent.ray.runtime.RayRuntime`
(``ray start --head`` / ``ray start --address`` / ``ray stop``), except that
every command is executed **inside** the bundle container via the runtime
handler's ``exec``.  The bundle's semantic requirements (``network_mode``,
``ipc_mode``, devices, mounts, capabilities) are mapped onto handler flags
here — that mapping is the agent's job precisely so the manifest can stay free
of raw CLI strings.

Nothing in this path invokes ``pip``, ``apt``, Git, Hugging Face or NGC: the
image is either already present under its pinned digest, or it is streamed
from the backend's own ``docker save`` endpoint and loaded locally.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from llm_port_node_agent.runtimes import ContainerRuntime, ContainerRuntimeError, detect_runtime

log = logging.getLogger(__name__)

# Long-lived container that hosts this node's Ray processes.  One per node:
# Ray's own head/worker split is decided by which ``ray start`` we exec.
DEFAULT_CONTAINER_NAME = "llm-port-ray-runtime"

# ``sleep infinity`` keeps the container alive so ``ray start`` (which
# daemonizes and returns) has a stable process namespace to live in.
_IDLE_COMMAND = ["sleep", "infinity"]


class RuntimeImageMissing(ContainerRuntimeError):
    """The pinned image is not present locally and could not be made present."""


class RuntimeDigestMismatch(ContainerRuntimeError):
    """A locally present image does not match the digest the bundle pins."""


@dataclass
class ContainerMountSpec:
    """One host→container bind mount."""

    host_path: str
    container_path: str
    mode: str = "ro"

    def to_flag(self) -> str:
        return f"{self.host_path}:{self.container_path}:{self.mode}"


@dataclass
class RuntimeBundleSpec:
    """The node-agent view of a runtime bundle's container contract.

    Built from the backend's ``container_launch_spec`` payload.  Semantic
    fields only — the CLI mapping happens in :meth:`RayContainerRuntime._run_flags`.
    """

    image: str
    digest: str
    name: str = DEFAULT_CONTAINER_NAME
    repo_digest: str | None = None
    runtime_handler: str = "docker"
    network_mode: str = "host"
    ipc_mode: str = "host"
    pid_mode: str | None = None
    gpus: str = "all"
    devices: list[str] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)
    shm_size: str | None = None
    ulimits: dict[str, str] = field(default_factory=dict)
    mounts: list[ContainerMountSpec] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "RuntimeBundleSpec":
        """Parse the backend's runtime-bundle payload.

        Raises:
            ValueError: the payload carries no image or no pinned digest.  An
                unpinned bundle is refused rather than silently resolved to
                whatever ``:tag`` happens to be on the node.
        """
        image = str(payload.get("image") or "").strip()
        digest = str(payload.get("digest") or "").strip()
        if not image:
            raise ValueError("runtime_bundle.image is required")
        if not digest:
            raise ValueError(f"runtime_bundle for {image} carries no pinned digest")
        req = payload.get("requirements") or {}
        mounts = [
            ContainerMountSpec(
                host_path=str(m.get("host_path")),
                container_path=str(m.get("container_path")),
                mode=str(m.get("mode") or "ro"),
            )
            for m in (payload.get("mounts") or [])
            if m.get("host_path") and m.get("container_path")
        ]
        return cls(
            image=image,
            digest=digest,
            name=str(payload.get("name") or DEFAULT_CONTAINER_NAME),
            repo_digest=payload.get("repo_digest"),
            runtime_handler=str(payload.get("runtime_handler") or "docker"),
            network_mode=str(req.get("network_mode") or "host"),
            ipc_mode=str(req.get("ipc_mode") or "host"),
            pid_mode=req.get("pid_mode"),
            gpus=str(req.get("gpus") or "all"),
            devices=[str(d) for d in (req.get("devices") or [])],
            capabilities=[str(c) for c in (req.get("capabilities") or [])],
            shm_size=req.get("shm_size"),
            ulimits={str(k): str(v) for k, v in (req.get("ulimits") or {}).items()},
            mounts=mounts,
            env={str(k): str(v) for k, v in (payload.get("env") or {}).items()},
        )


class RayContainerRuntime:
    """Drives Ray bootstrap inside a digest-pinned runtime container."""

    def __init__(
        self,
        *,
        runtime: ContainerRuntime | None = None,
        token_path: str = "/var/run/llm-port/ray/cluster.token",
    ) -> None:
        self._runtime = runtime
        self._token_path = token_path
        self.last_error: str | None = None

    def _handler(self, spec: RuntimeBundleSpec) -> ContainerRuntime:
        if self._runtime is not None:
            return self._runtime
        self._runtime = detect_runtime(preferred=spec.runtime_handler)
        return self._runtime

    # ------------------------------------------------------------------
    # Image identity (OCI digest presence / load)
    # ------------------------------------------------------------------

    async def ensure_image(
        self,
        spec: RuntimeBundleSpec,
        *,
        loader: Any = None,
        emit_progress: Any = None,
    ) -> dict[str, Any]:
        """Ensure the pinned image is present locally, by identity.

        A tag match is not enough: the whole point of pinning is that the bytes
        on the node are the bytes that were certified.  If the tag resolves to
        a different image the call fails loudly instead of running something
        else under a certified name.

        ``loader`` is an optional ``async (spec) -> None`` callable that makes
        the image present (the backend's ``docker save`` stream, a local
        registry mirror).  Without it, a missing image is simply reported as
        missing — never pulled from the internet.
        """
        handler = self._handler(spec)
        identity = await handler.image_identity(spec.image)

        if not identity.get("present") and loader is not None:
            log.info("Runtime image %s absent; loading from backend", spec.image)
            if emit_progress is not None:
                await emit_progress(
                    {"phase": "loading_image", "message": f"Loading runtime image {spec.image}"}
                )
            await loader(spec)
            identity = await handler.image_identity(spec.image)

        if not identity.get("present"):
            self.last_error = f"runtime image {spec.image} is not present on this node"
            raise RuntimeImageMissing(self.last_error)

        local_id = str(identity.get("id") or "")
        repo_digests = [str(d) for d in identity.get("repo_digests") or []]
        matches_id = local_id == spec.digest
        matches_repo = spec.repo_digest is not None and any(
            d.endswith(spec.repo_digest) for d in repo_digests
        )
        if not (matches_id or matches_repo):
            self.last_error = (
                f"runtime image {spec.image} is {local_id or 'unknown'}, "
                f"but the bundle pins {spec.digest}"
            )
            raise RuntimeDigestMismatch(self.last_error)

        return {
            "present": True,
            "verified": True,
            "image": spec.image,
            "image_id": local_id,
            "repo_digests": repo_digests,
            "runtime_handler": handler.name,
        }

    # ------------------------------------------------------------------
    # Container lifecycle
    # ------------------------------------------------------------------

    def _run_flags(self, spec: RuntimeBundleSpec) -> list[str]:
        """Map the bundle's semantic requirements onto handler flags."""
        flags: list[str] = []
        if spec.network_mode:
            flags.extend(["--network", spec.network_mode])
        if spec.ipc_mode:
            flags.extend(["--ipc", spec.ipc_mode])
        if spec.pid_mode:
            flags.extend(["--pid", spec.pid_mode])
        for device in spec.devices:
            flags.extend(["--device", device])
        for capability in spec.capabilities:
            flags.extend(["--cap-add", capability])
        if spec.shm_size:
            flags.extend(["--shm-size", spec.shm_size])
        for name, value in spec.ulimits.items():
            flags.extend(["--ulimit", f"{name}={value}"])
        return flags

    async def ensure_container(
        self,
        spec: RuntimeBundleSpec,
        *,
        env: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Ensure the runtime container exists and is running.

        Idempotent: an existing container of the same name running the pinned
        image is reused; one running a *different* image is replaced, because
        the pinned digest is the contract.
        """
        handler = self._handler(spec)
        merged_env = dict(spec.env)
        merged_env.update(env or {})
        # The agent hands the cluster token in by path; the file is bind-mounted
        # so the container reads the same 0600 file the host wrote.
        merged_env.setdefault("RAY_AUTH_MODE", "token")
        merged_env.setdefault("RAY_AUTH_TOKEN_PATH", self._token_path)

        if await handler.exists(spec.name):
            info = await handler.inspect(spec.name)
            state = (info.get("State") or {}) if isinstance(info, dict) else {}
            running_image = str((info.get("Image") or "")) if isinstance(info, dict) else ""
            if running_image and running_image != spec.digest:
                log.warning(
                    "Container %s runs image %s, expected %s — recreating",
                    spec.name, running_image, spec.digest,
                )
                await handler.remove(spec.name, force=True)
            elif state.get("Running"):
                return {"container": spec.name, "created": False, "running": True}
            else:
                await handler.start(spec.name)
                return {"container": spec.name, "created": False, "running": True}

        volumes = [m.to_flag() for m in spec.mounts]
        token_dir = self._token_path.rsplit("/", 1)[0]
        volumes.append(f"{token_dir}:{token_dir}:ro")

        container_id = await handler.run(
            image=spec.image,
            name=spec.name,
            env=merged_env,
            gpus=spec.gpus if spec.gpus not in ("", "none") else None,
            volumes=volumes,
            command=list(_IDLE_COMMAND),
            entrypoint="",
            extra_args=self._run_flags(spec),
            timeout_sec=180,
        )
        return {"container": spec.name, "container_id": container_id, "created": True, "running": True}

    async def remove_container(self, spec: RuntimeBundleSpec) -> None:
        """Remove the runtime container (best effort)."""
        handler = self._handler(spec)
        try:
            if await handler.exists(spec.name):
                await handler.remove(spec.name, force=True)
        except ContainerRuntimeError as exc:  # pragma: no cover - best effort
            log.warning("Removing container %s failed: %s", spec.name, exc)

    # ------------------------------------------------------------------
    # Ray bootstrap inside the container
    # ------------------------------------------------------------------

    async def installed_version(self, spec: RuntimeBundleSpec) -> str | None:
        """Ray version reported by the CLI *inside* the container."""
        handler = self._handler(spec)
        code, out, _ = await handler.exec_(
            spec.name, ["ray", "--version"], timeout_sec=60, raise_on_error=False,
        )
        if code != 0:
            return None
        for line in out.splitlines():
            parts = line.split(",")
            if (
                len(parts) >= 2
                and parts[0].strip().lower() == "ray"
                and parts[1].strip().startswith("version")
            ):
                return parts[1].strip().split(None, 1)[-1]
        return None

    async def _ray(
        self,
        spec: RuntimeBundleSpec,
        args: list[str],
        *,
        env: dict[str, str] | None,
        label: str,
        tolerate_already_running: bool = False,
        tolerate_missing_processes: bool = False,
        timeout_sec: float = 300,
    ) -> str:
        """Exec one ``ray`` command inside the runtime container."""
        handler = self._handler(spec)
        code, out, err = await handler.exec_(
            spec.name,
            ["ray", *args],
            env=env,
            timeout_sec=timeout_sec,
            raise_on_error=False,
        )
        detail = (err or out or "").strip()
        if code != 0:
            lowered = detail.lower()
            if tolerate_already_running and _means_already_running(lowered):
                log.info("%s: node already running/joined (idempotent no-op)", label)
                return detail
            if tolerate_missing_processes and _means_nothing_running(lowered):
                log.info("%s: no local processes (idempotent no-op)", label)
                return detail
            self.last_error = detail
            raise ContainerRuntimeError(f"{label} failed (exit {code}): {detail}")
        self.last_error = None
        return detail

    async def start_head(
        self,
        spec: RuntimeBundleSpec,
        *,
        port: int,
        dashboard_port: int,
        dashboard_host: str,
        node_ip_address: str | None = None,
        num_cpus: int | None = None,
        num_gpus: int | None = None,
        include_dashboard: bool = True,
        env: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """``ray start --head`` inside the runtime container."""
        args = ["start", "--head", f"--port={port}"]
        if node_ip_address:
            args.append(f"--node-ip-address={node_ip_address}")
        if include_dashboard:
            args += [f"--dashboard-host={dashboard_host}", f"--dashboard-port={dashboard_port}"]
        else:
            args.append("--include-dashboard=false")
        if num_cpus is not None:
            args.append(f"--num-cpus={num_cpus}")
        if num_gpus is not None:
            args.append(f"--num-gpus={num_gpus}")

        await self._ray(
            spec, args, env=env, label="ray start --head", tolerate_already_running=True,
        )
        resolved_host = node_ip_address or dashboard_host
        dash_url_host = (
            resolved_host
            if dashboard_host in ("0.0.0.0", "127.0.0.1") and node_ip_address
            else dashboard_host
        )
        return {
            "started": True,
            "in_container": spec.name,
            "head_address": f"{resolved_host}:{port}",
            "cluster_address": f"{resolved_host}:{port}",
            "dashboard_url": f"http://{dash_url_host}:{dashboard_port}" if include_dashboard else None,
        }

    async def join_cluster(
        self,
        spec: RuntimeBundleSpec,
        *,
        head_address: str,
        node_ip_address: str | None = None,
        num_cpus: int | None = None,
        num_gpus: int | None = None,
        env: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """``ray start --address=<head>`` inside the runtime container."""
        args = ["start", f"--address={head_address}"]
        if node_ip_address:
            args.append(f"--node-ip-address={node_ip_address}")
        if num_cpus is not None:
            args.append(f"--num-cpus={num_cpus}")
        if num_gpus is not None:
            args.append(f"--num-gpus={num_gpus}")

        await self._ray(
            spec, args, env=env, label="ray start --address", tolerate_already_running=True,
        )
        return {"joined": True, "in_container": spec.name, "head_address": head_address}

    async def stop(
        self, spec: RuntimeBundleSpec, *, force: bool = False, remove: bool = False
    ) -> dict[str, Any]:
        """``ray stop`` inside the container; optionally tear the container down."""
        args = ["stop"]
        if force:
            args.append("--force")
        try:
            await self._ray(
                spec, args, env=None, label="ray stop", tolerate_missing_processes=True,
            )
            stopped = True
            detail = None
        except ContainerRuntimeError as exc:
            stopped = False
            detail = str(exc)
        if remove:
            await self.remove_container(spec)
        result: dict[str, Any] = {"stopped": stopped, "in_container": spec.name}
        if not stopped:
            result.update({"best_effort": True, "detail": detail})
        return result


def _means_nothing_running(detail: str) -> bool:
    return (
        "no local processes to stop" in detail
        or "no ray processes" in detail
        or "nothing to stop" in detail
        or "not running" in detail
        or "not found" in detail
    )


def _means_already_running(detail: str) -> bool:
    return (
        "already part of a ray cluster" in detail
        or "already running" in detail
        or "already started" in detail
        or "address is already in use" in detail
        or "already joined" in detail
        or "connection to the ray cluster already exists" in detail
    )
