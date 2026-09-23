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

import hashlib
import json
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

# Above this, a GPU is carrying a real allocation rather than driver overhead.
_GPU_BUSY_THRESHOLD_MIB = 1024

# Ray records the cluster a node last joined here.  It is the only
# trustworthy answer to "am I already a member of *this* cluster", which
# is a different question from "is a raylet running".
_CURRENT_CLUSTER_FILE = "/tmp/ray/ray_current_cluster"


def _container_name(ps_line: str) -> str | None:
    """Container name out of one ``ps --format '{{json .}}'`` line."""
    import json

    try:
        record = json.loads(ps_line)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(record, dict):
        return None
    name = record.get("Names") or record.get("Name")
    if isinstance(name, list):
        name = name[0] if name else None
    return str(name) if name else None


def compute_rootfs_digest(layer_diff_ids: list[str]) -> str:
    """Content identity of an image from its ordered RootFS layer diff IDs.

    Must stay byte-for-byte identical to the backend's
    ``llm_port_backend.services.inference.bundles.compute_rootfs_digest``.
    """
    return "sha256:" + hashlib.sha256("\n".join(layer_diff_ids).encode()).hexdigest()


def _match(identity: dict[str, Any], spec: "RuntimeBundleSpec") -> str | None:
    """How a local image satisfies the pin, or ``None`` if it does not.

    Content identity first: two nodes can hold byte-identical copies of the
    same image under different config IDs when one was side-loaded, and
    rejecting the certified bits over a rewritten config would be a false
    alarm that blocks every deployment on that node.
    """
    if not identity.get("present"):
        return None
    local_id = str(identity.get("id") or "")
    repo_digests = [str(d) for d in identity.get("repo_digests") or []]
    layers = [str(x) for x in identity.get("rootfs_layers") or []]
    local_rootfs = compute_rootfs_digest(layers) if layers else None
    if spec.rootfs_digest and local_rootfs == spec.rootfs_digest:
        return "rootfs_digest"
    if local_id and local_id == spec.digest:
        return "image_id"
    if spec.repo_digest and any(d.endswith(spec.repo_digest) for d in repo_digests):
        return "repo_digest"
    return None


class RuntimeImageMissing(ContainerRuntimeError):
    """The pinned image is not present locally and could not be made present."""

    error_code = "runtime_image_missing"


class RuntimeDigestMismatch(ContainerRuntimeError):
    """A locally present image does not match the digest the bundle pins.

    Permanent for as long as the pin and the image stay as they are: asking
    again returns the same answer, which is what the backend needs to know
    to stop asking.
    """

    error_code = "runtime_image_mismatch"


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
    rootfs_digest: str | None = None
    repo_digest: str | None = None
    runtime_handler: str = "docker"
    network_mode: str = "host"
    ipc_mode: str = "host"
    pid_mode: str | None = None
    gpus: str = "all"
    #: Vendor the bundle targets, from the backend's target architecture.
    #: Decides how the semantic ``gpus`` request becomes runtime flags -- an
    #: AMD node needs /dev/kfd, not ``--gpus``.
    accelerator_vendor: str = "nvidia"
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
            rootfs_digest=payload.get("rootfs_digest"),
            repo_digest=payload.get("repo_digest"),
            runtime_handler=str(payload.get("runtime_handler") or "docker"),
            network_mode=str(req.get("network_mode") or "host"),
            ipc_mode=str(req.get("ipc_mode") or "host"),
            pid_mode=req.get("pid_mode"),
            gpus=str(req.get("gpus") or "all"),
            accelerator_vendor=str(
                (payload.get("target_architecture") or {}).get("accelerator_vendor")
                or req.get("accelerator_vendor")
                or "nvidia"
            ),
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
        handler_hint: str = "docker",
    ) -> None:
        self._runtime = runtime
        self._token_path = token_path
        # Which handler to use for calls that address an already-running
        # container (status/Serve), where no bundle is in hand to read it from.
        self._handler_hint = handler_hint
        self.last_error: str | None = None

    def _handler(self, spec: RuntimeBundleSpec) -> ContainerRuntime:
        if spec.runtime_handler and spec.runtime_handler != "-":
            self._handler_hint = spec.runtime_handler
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

        # Absent, or present under the right name but the wrong build: either
        # way the answer is the pinned build, and the loader is how to get it.
        # A wrong build used to be a dead end -- the node failed the check and
        # never asked the server, even when the server held the right image,
        # so a machine with a stale copy could not recover on its own. The
        # loader passes the pin along, and a server holding the wrong build
        # too refuses before sending anything.
        needs_load = not identity.get("present") or _match(identity, spec) is None
        if needs_load and loader is not None:
            reason = "absent" if not identity.get("present") else "a different build"
            log.info("Runtime image %s is %s; loading from backend", spec.image, reason)
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
        layers = [str(x) for x in identity.get("rootfs_layers") or []]
        local_rootfs = compute_rootfs_digest(layers) if layers else None

        matched_by = _match(identity, spec)
        if matched_by is None:
            self.last_error = (
                f"runtime image {spec.image} is id={local_id or 'unknown'} "
                f"rootfs={local_rootfs or 'unknown'}, but the bundle pins "
                f"id={spec.digest} rootfs={spec.rootfs_digest or 'unset'}"
            )
            raise RuntimeDigestMismatch(self.last_error)

        return {
            "present": True,
            "verified": True,
            "matched_by": matched_by,
            "image": spec.image,
            "image_id": local_id,
            "rootfs_digest": local_rootfs,
            "repo_digests": repo_digests,
            "runtime_handler": handler.name,
        }

    # ------------------------------------------------------------------
    # Preflight
    # ------------------------------------------------------------------

    async def preflight(self, spec: RuntimeBundleSpec) -> dict[str, Any]:
        """Report what else on this host could break the runtime container.

        This is a *diagnostic*, not a gate: a node may legitimately run other
        workloads, and LLM.Port's own legacy single-node runtimes coexist with
        Ray by design, so refusing to start whenever a GPU is busy would be
        wrong.  What was wrong was starting blind - on the DGX worker an
        unmanaged ``tmux`` loop kept relaunching old containers that held GPU
        memory, and the only symptom was Ray failing with CUDA OOM and port
        conflicts at nondeterministic points during distributed startup.

        Returns ``{"gpu": [...], "foreign_containers": [...], "conflicts": [...]}``
        so the backend can put the real reason in the command result instead of
        leaving an operator to guess.
        """
        handler = self._handler(spec)
        report: dict[str, Any] = {"gpu": [], "foreign_containers": [], "conflicts": []}

        for entry in await self._gpu_memory_in_use():
            report["gpu"].append(entry)
            if entry.get("used_mib", 0) > _GPU_BUSY_THRESHOLD_MIB:
                report["conflicts"].append(
                    f"GPU {entry['index']} already has {entry['used_mib']} MiB allocated "
                    "before the runtime container starts"
                )

        try:
            for line in await handler.ps(all_=False):
                name = _container_name(line)
                if not name or name == spec.name:
                    continue
                report["foreign_containers"].append(name)
        except Exception as exc:  # noqa: BLE001 - diagnostics must never fail a start
            log.debug("Preflight container listing failed: %s", exc)

        if report["conflicts"]:
            log.warning(
                "Runtime preflight on this node found: %s (other containers running: %s)",
                "; ".join(report["conflicts"]),
                ", ".join(report["foreign_containers"]) or "none",
            )
        return report

    @staticmethod
    async def _gpu_memory_in_use() -> list[dict[str, Any]]:
        """Per-GPU memory already allocated, via ``nvidia-smi``.

        Best effort: a host without ``nvidia-smi`` simply reports nothing.
        """
        import asyncio
        import shutil

        if shutil.which("nvidia-smi") is None:
            return []
        try:
            proc = await asyncio.create_subprocess_exec(
                "nvidia-smi",
                "--query-gpu=index,memory.used,memory.total",
                "--format=csv,noheader,nounits",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
        except Exception as exc:  # noqa: BLE001 - diagnostics must never fail a start
            log.debug("nvidia-smi preflight failed: %s", exc)
            return []
        if proc.returncode != 0:
            return []

        entries: list[dict[str, Any]] = []
        for line in stdout.decode("utf-8", "replace").splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) != 3:
                continue
            try:
                entries.append({
                    "index": int(parts[0]),
                    "used_mib": int(parts[1]),
                    "total_mib": int(parts[2]),
                })
            except ValueError:
                continue
        return entries

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
            # A container records the *local* config id of its image, which
            # need not equal the bundle's pinned digest even when the content
            # is identical (the side-loaded copy on the DGX worker is exactly
            # that case), so both are acceptable.
            expected_ids = {spec.digest}
            local_id = await self._local_image_id(spec)
            if local_id:
                expected_ids.add(local_id)
            drift = self._launch_drift(info, spec, merged_env)
            if running_image and running_image not in expected_ids:
                log.warning(
                    "Container %s runs image %s, expected %s — recreating",
                    spec.name, running_image, spec.digest,
                )
                await handler.remove(spec.name, force=True)
            elif drift:
                # The image alone is not the contract: a container created
                # before the token path or a mount changed keeps the old one
                # silently, and `ray start` then fails inside it with an
                # opaque "token file cannot be opened or is empty".
                log.warning("Container %s no longer matches its spec (%s) — recreating", spec.name, drift)
                await handler.remove(spec.name, force=True)
            elif state.get("Running"):
                return {"container": spec.name, "created": False, "running": True}
            else:
                await handler.start(spec.name)
                return {"container": spec.name, "created": False, "running": True}

        preflight = await self.preflight(spec)

        volumes = [m.to_flag() for m in spec.mounts]
        token_dir = self._token_path.rsplit("/", 1)[0]
        volumes.append(f"{token_dir}:{token_dir}:ro")

        container_id = await handler.run(
            image=spec.image,
            name=spec.name,
            env=merged_env,
            gpus=spec.gpus if spec.gpus not in ("", "none") else None,
            accelerator_vendor=spec.accelerator_vendor,
            volumes=volumes,
            command=list(_IDLE_COMMAND),
            entrypoint="",
            extra_args=self._run_flags(spec),
            timeout_sec=180,
        )
        return {
            "container": spec.name,
            "container_id": container_id,
            "created": True,
            "running": True,
            "preflight": preflight,
        }

    def _launch_drift(
        self,
        info: dict[str, Any] | None,
        spec: RuntimeBundleSpec,
        env: dict[str, str],
    ) -> str | None:
        """Describe how a running container differs from the spec, if it does.

        Only the parts that silently break the runtime are compared: the bind
        mounts it must read models and the cluster token through, and the
        token path it was told to use.  Diffing everything would recreate the
        container on cosmetic differences between runtimes.

        Returns a short reason, or ``None`` when the container still matches.
        """
        if not isinstance(info, dict):
            return None

        wanted_targets = {m.container_path for m in spec.mounts}
        token_dir = self._token_path.rsplit("/", 1)[0]
        wanted_targets.add(token_dir)

        actual_targets = {
            str(m.get("Destination"))
            for m in (info.get("Mounts") or [])
            if isinstance(m, dict) and m.get("Destination")
        }
        missing = wanted_targets - actual_targets
        if missing:
            return f"missing mounts: {', '.join(sorted(missing))}"

        config = info.get("Config") if isinstance(info.get("Config"), dict) else {}
        actual_env = {}
        for entry in config.get("Env") or []:
            key, _, value = str(entry).partition("=")
            actual_env[key] = value
        wanted_token = env.get("RAY_AUTH_TOKEN_PATH")
        if wanted_token and actual_env.get("RAY_AUTH_TOKEN_PATH") != wanted_token:
            return "RAY_AUTH_TOKEN_PATH changed"
        return None

    async def _local_image_id(self, spec: RuntimeBundleSpec) -> str | None:
        """The config ID of the locally present image, if any.

        Used to decide whether a running container is on the right image: the
        container records the *local* config ID, which need not equal the
        bundle's pinned ``digest`` even when the content is identical.
        """
        try:
            identity = await self._handler(spec).image_identity(spec.image)
        except ContainerRuntimeError:  # pragma: no cover - defensive
            return None
        return str(identity.get("id") or "") or None

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
        """``ray start --head`` inside the runtime container.

        Idempotent against the node, for the same reason ``join_cluster`` is.
        A node already heading *this* cluster is left alone; a node heading a
        *different* one is stopped first.

        Without that second case, asking the same machine to head a second
        cluster -- which is exactly what creating a new cluster from the same
        fleet does -- started another GCS and another raylet beside the first.
        The DGX head reached three GCS servers and four raylets that way, and
        a cluster with two control planes in one container serves nothing.
        """
        target = f"{node_ip_address or ''}:{port}" if node_ip_address else None
        joined = await self._joined_cluster(spec)
        if joined and await self._raylet_running(spec):
            if target is None or joined == target:
                log.info("Already heading %s; not starting a second head.", joined)
                return {
                    "started": True,
                    "already_running": True,
                    "in_container": spec.name,
                    "head_address": joined,
                    "cluster_address": joined,
                    "dashboard_url": None,
                }
            log.warning("Node heads %s, not %s; stopping it first.", joined, target)
            await self.stop(spec, force=True)

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
        """``ray start --address=<head>`` inside the runtime container.

        Idempotent against the *node*, not against the error message.  This
        used to rely on ``tolerate_already_running``, which assumes a second
        ``ray start --address`` fails -- it does not.  It starts an additional
        raylet, and the cluster gains a phantom node that is ``Alive`` in GCS,
        advertises CPUs and a GPU, and attracts placement it can never serve.

        The DGX pair reached four "alive" nodes for two machines this way:
        three raylets in one container, and a Serve proxy scheduled onto a
        node that was not where we thought it was.
        """
        target = head_address.strip()
        joined = await self._joined_cluster(spec)

        if joined == target and await self._raylet_running(spec):
            log.info("Already a member of %s; not starting a second raylet.", target)
            return {
                "joined": True,
                "already_member": True,
                "in_container": spec.name,
                "head_address": head_address,
            }

        if joined and joined != target:
            # Pointing at a different cluster.  Leaving it up would keep its
            # registration alive alongside the new one.
            log.warning("Node is joined to %s, not %s; stopping it first.", joined, target)
            await self.stop(spec, force=True)

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
        return {
            "joined": True,
            "already_member": False,
            "in_container": spec.name,
            "head_address": head_address,
        }

    async def _joined_cluster(self, spec: RuntimeBundleSpec) -> str | None:
        """The cluster this node last joined, per Ray's own marker.

        ``None`` when the node has never joined, or when the container is not
        up to answer -- both of which mean "go ahead and start".
        """
        try:
            handler = self._handler(spec)
            code, out, _ = await handler.exec_(
                spec.name,
                ["cat", _CURRENT_CLUSTER_FILE],
                env=None,
                timeout_sec=15,
                raise_on_error=False,
            )
        except ContainerRuntimeError:
            return None
        if code != 0:
            return None
        value = (out or "").strip()
        return value or None

    async def _raylet_running(self, spec: RuntimeBundleSpec) -> bool:
        """Whether a raylet is actually alive, not merely recorded as joined."""
        try:
            handler = self._handler(spec)
            code, out, _ = await handler.exec_(
                spec.name,
                ["pgrep", "-c", "raylet"],
                env=None,
                timeout_sec=15,
                raise_on_error=False,
            )
        except ContainerRuntimeError:
            return False
        if code != 0:
            return False
        try:
            return int((out or "0").strip().splitlines()[0]) > 0
        except (ValueError, IndexError):
            return False

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

    def _spec_for(self, container_name: str) -> RuntimeBundleSpec:
        """A minimal spec used only to resolve the runtime handler.

        Status and Serve calls address a container that is already running, so
        the image identity is irrelevant here - only which handler (docker /
        podman) to talk to.  ``handler_hint`` carries that across.
        """
        return RuntimeBundleSpec(
            image="-",
            digest="-",
            name=container_name,
            runtime_handler=self._handler_hint,
        )

    async def is_running(self, container_name: str = DEFAULT_CONTAINER_NAME) -> bool:
        """Is the runtime container up right now?

        Used as a *sanity check*, never to decide which execution mode applies:
        that decision belongs to the backend and travels on the command as
        ``runtime_bundle``.  Inferring the mode from local state would let a
        leftover container from a previous environment hijack a host-based one,
        and would silently fall back to the host SDK - which on a certified
        node has no Ray at all - whenever the container happened to be
        restarting.
        """
        handler = self._handler(self._spec_for(container_name))
        try:
            if not await handler.exists(container_name):
                return False
            info = await handler.inspect(container_name)
            state = (info.get("State") or {}) if isinstance(info, dict) else {}
            return bool(state.get("Running"))
        except Exception:  # noqa: BLE001 - a probe failure is "not running"
            return False

    async def _helper(
        self,
        container_name: str,
        args: list[str],
        *,
        stdin: str | None = None,
        timeout_sec: float = 60,
    ) -> tuple[int, Any]:
        """Run one ``llm-port-ray-runtime`` verb and parse its JSON document.

        The helper is the whole control contract: it ships inside the certified
        image, so every Ray SDK call runs against the exact version the cluster
        is running, and the host never imports Ray.
        """
        handler = self._handler(self._spec_for(container_name))
        command = ["llm-port-ray-runtime", *args]
        code, out, err = await handler.exec_(
            container_name,
            command,
            stdin=stdin,
            timeout_sec=timeout_sec,
            raise_on_error=False,
        )
        parsed = _extract_json(out)
        if parsed is None:
            detail = (err or out or "").strip()
            log.warning("Helper %s produced no JSON (exit %s): %s", " ".join(args), code, detail[:400])
        return code, parsed

    async def get_cluster_status(
        self,
        container_name: str = DEFAULT_CONTAINER_NAME,
        *,
        include_serve: bool = False,
        include_metrics: bool = False,
        include_state: bool = False,
        expected_version: str | None = None,
        timeout_sec: float = 30,
    ) -> dict[str, Any]:
        """Cluster status from the in-container helper.

        The result has to match the shape the host-SDK path returns for the
        same command: the backend parses one contract and must not be able to
        tell where the probe ran.  In particular ``version`` is the key the
        backend reads - emitting only ``ray_version`` made every containerized
        cluster report an unknown Ray version.
        """
        _code, res = await self._helper(
            container_name, ["cluster-status"], timeout_sec=timeout_sec
        )
        if not isinstance(res, dict):
            return {"alive": False, "version": None, "num_nodes": 0, "nodes": []}

        version = res.get("version") or res.get("ray_version")
        status_dict: dict[str, Any] = {
            "alive": bool(res.get("alive", False)),
            "version": version,
            "ray_version": version,
            "num_nodes": int(res.get("num_nodes", 0) or 0),
            "nodes": list(res.get("nodes") or []),
            "total_gpus": float(res.get("total_gpus", 0.0) or 0.0),
            "available_gpus": float(res.get("available_gpus", res.get("total_gpus", 0.0)) or 0.0),
            "total_cpus": float(res.get("total_cpus", 0.0) or 0.0),
            "available_cpus": float(res.get("available_cpus", res.get("total_cpus", 0.0)) or 0.0),
            "cluster_address": res.get("cluster_address"),
            "head_address": res.get("head_address"),
            "capabilities": dict(res.get("capabilities") or {}),
        }

        if expected_version and version and version != expected_version:
            status_dict["version_mismatch"] = {
                "expected": expected_version,
                "observed": version,
            }

        if include_serve:
            serve_res = await self.get_serve_status(
                container_name=container_name, timeout_sec=timeout_sec
            )
            status_dict["serve"] = serve_res
            status_dict["capabilities"]["serve"] = bool(serve_res.get("available", False))

        if include_metrics:
            status_dict["metrics"] = await self.get_metrics_targets(
                container_name=container_name, timeout_sec=timeout_sec
            )
            status_dict["capabilities"]["metrics"] = bool(
                status_dict["metrics"].get("enabled", False)
            )

        if include_state:
            # Tier B (ray.util.state) is Dashboard-dependent and the certified
            # image runs with the Dashboard disabled, so it is reported absent
            # rather than pretended.
            status_dict["state"] = {
                "available": False,
                "detail": "state API not available in the Dashboard-independent runtime",
            }
            status_dict["capabilities"]["state"] = False

        return status_dict

    async def get_metrics_targets(
        self,
        container_name: str = DEFAULT_CONTAINER_NAME,
        timeout_sec: float = 30,
    ) -> dict[str, Any]:
        """Prometheus scrape targets, as Ray itself publishes them.

        Ray writes the authoritative list to
        ``/tmp/ray/prom_metrics_service_discovery.json`` and keeps it current
        as nodes join and leave.  Deriving the list from the node records
        instead gave one endpoint per node and missed the head's other
        exporters entirely -- on the DGX pair, four targets published, two
        derived.  The two lost were the autoscaler (cluster capacity) and the
        dashboard/component exporter (per-component memory).

        The node-derived list is kept as a fallback: an older Ray, or a
        session whose file has not appeared yet, still yields the per-node
        endpoints rather than nothing.
        """
        published = await self._published_metrics_targets(container_name)
        if published:
            return {"enabled": True, "targets": published}

        # A metrics read runs inside the status probe the backend polls, so it
        # must not be able to fail that probe: a container that went away is a
        # cluster with no targets, not a cluster that cannot be described.
        try:
            _code, res = await self._helper(
                container_name, ["cluster-status"], timeout_sec=timeout_sec
            )
        except ContainerRuntimeError:
            return {"enabled": False, "targets": []}
        if not isinstance(res, dict) or not res.get("alive"):
            return {"enabled": False, "targets": []}

        targets: list[dict[str, Any]] = []
        for node in res.get("nodes") or []:
            if not isinstance(node, dict) or not node.get("alive", True):
                continue
            port = node.get("metrics_export_port")
            address = node.get("node_manager_address") or node.get("node_ip")
            if address and port:
                targets.append({
                    "node_id": node.get("node_id"),
                    "address": address,
                    "port": int(port),
                    "url": f"http://{address}:{int(port)}/metrics",
                })
        return {"enabled": bool(targets), "targets": targets}

    async def get_serve_status(
        self,
        container_name: str = DEFAULT_CONTAINER_NAME,
        timeout_sec: float = 30,
        app_name: str | None = None,
    ) -> dict[str, Any]:
        """Ray Serve status from the in-container helper."""
        args = ["serve-status"]
        if app_name:
            args += ["--app-name", app_name]
        _code, res = await self._helper(container_name, args, timeout_sec=timeout_sec)
        if not isinstance(res, dict):
            return {"available": False, "applications": {}}
        res.setdefault("applications", {})
        return res

    async def _published_metrics_targets(
        self, container_name: str
    ) -> list[dict[str, Any]]:
        """Read Ray's own service-discovery file, if it is there.

        Format is Prometheus file_sd: a list of ``{"labels": ..., "targets":
        ["host:port", ...]}``.  Ray owns it, so it covers every exporter on
        every node without us having to know which exporters exist.
        """
        try:
            handler = self._handler(self._spec_for(container_name))
            code, out, _ = await handler.exec_(
                container_name,
                ["cat", "/tmp/ray/prom_metrics_service_discovery.json"],
                env=None,
                timeout_sec=15,
                raise_on_error=False,
            )
        except ContainerRuntimeError:
            return []
        if code != 0 or not out:
            return []

        try:
            groups = json.loads(out)
        except (json.JSONDecodeError, ValueError):
            return []
        if not isinstance(groups, list):
            return []

        targets: list[dict[str, Any]] = []
        for group in groups:
            if not isinstance(group, dict):
                continue
            for entry in group.get("targets") or []:
                address, _, port = str(entry).rpartition(":")
                if not address or not port.isdigit():
                    continue
                targets.append({
                    # Ray's file names no node id; the backend maps the
                    # address to one of our machines anyway.
                    "node_id": None,
                    "address": address,
                    "port": int(port),
                    "url": f"http://{address}:{int(port)}/metrics",
                })
        return targets

    async def run_serve_app(
        self,
        container_name: str,
        app_name: str,
        llm_serving_args: dict[str, Any],
        http_options: dict[str, Any] | None = None,
        route_prefix: str = "/",
        timeout_sec: float = 300,
    ) -> dict[str, Any]:
        """Deploy a named Serve application through the in-container helper.

        The document goes in on **stdin**: a compiled ``LLMServingArgs`` is far
        larger than an argv-safe string, and passing it as an argument would
        also put the model configuration into the container's process list.
        """
        document = json.dumps({
            "llm_serving_args": llm_serving_args,
            "http_options": http_options or {},
        })
        code, res = await self._helper(
            container_name,
            ["run-serve-app", "--app-name", app_name, "--route-prefix", route_prefix, "--config", "-"],
            stdin=document,
            timeout_sec=timeout_sec,
        )
        if isinstance(res, dict) and res.get("deployed"):
            return res
        detail = (res or {}).get("error") if isinstance(res, dict) else None
        raise ContainerRuntimeError(
            f"run_serve_app failed in container (exit {code}): {detail or 'no result from helper'}"
        )

    async def delete_serve_app(
        self,
        container_name: str,
        app_name: str,
        timeout_sec: float = 60,
    ) -> dict[str, Any]:
        """Delete a named Serve application through the in-container helper."""
        _code, res = await self._helper(
            container_name,
            ["delete-serve-app", "--app-name", app_name],
            timeout_sec=timeout_sec,
        )
        if isinstance(res, dict):
            return res
        return {"deleted": False, "app_name": app_name, "error": "no result from helper"}


def _extract_json(text: str) -> Any:
    for i in range(len(text)):
        if text[i] in ("{", "["):
            try:
                return json.loads(text[i:])
            except Exception:
                pass
    return None


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
        # Ray 2.58 does not say "already running" when a head is started over
        # a live session: `_write_cluster_info_to_kv` asserts that the new
        # session name matches the one already persisted in the GCS KV store.
        # That assertion *is* "a head is already up here", and without this a
        # second reconcile pass turned a healthy cluster into `failed`.
        or "does not match persisted value" in detail
    )
