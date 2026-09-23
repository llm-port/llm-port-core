"""Containerized Ray bootstrap (Phase 4B).

The point of these tests is the boundary the old suite could not see: a node
with **no host Ray Python package** must still start and observe the certified
runtime, entirely from a locally present OCI digest.  The fake runtime handler
below therefore has no Ray anywhere on the host - everything happens through
``exec`` into the pinned image.
"""

from __future__ import annotations

from typing import Any

import pytest

from llm_port_node_agent.ray.container import (
    RayContainerRuntime,
    RuntimeBundleSpec,
    RuntimeDigestMismatch,
    RuntimeImageMissing,
    compute_rootfs_digest,
)

DIGEST = "sha256:7dc13b9aff5a00dc447251d550a29bcddf9480cc7efad1509cfbd9a0c661a9d8"
OTHER_DIGEST = "sha256:" + "11" * 32
# Two layers standing in for the certified image's 50.
LAYERS = ["sha256:" + "aa" * 32, "sha256:" + "bb" * 32]
ROOTFS_DIGEST = compute_rootfs_digest(LAYERS)
OTHER_LAYERS = ["sha256:" + "cc" * 32]


def _payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": "llm-port-ray-runtime",
        "image": "llmport/ray-vllm-gb10:ray2.58-nv26.08",
        "digest": DIGEST,
        "rootfs_digest": ROOTFS_DIGEST,
        "runtime_handler": "docker",
        "requirements": {
            "network_mode": "host",
            "ipc_mode": "host",
            "gpus": "all",
            "devices": ["/dev/infiniband"],
            "capabilities": ["IPC_LOCK"],
        },
        "mounts": [
            {"host_path": "/srv/llm-port/models", "container_path": "/models", "mode": "ro"},
        ],
        "env": {},
    }
    payload.update(overrides)
    return payload


class _FakeRuntime:
    """Minimal ContainerRuntime double recording what the agent asked for."""

    def __init__(
        self,
        *,
        image_id: str | None = DIGEST,
        present: bool = True,
        ray_version: str = "2.58.0",
        layers: list[str] | None = None,
    ) -> None:
        self._image_id = image_id
        self._present = present
        self._ray_version = ray_version
        self._layers = LAYERS if layers is None else layers
        self.containers: dict[str, dict[str, Any]] = {}
        self.runs: list[dict[str, Any]] = []
        self.execs: list[dict[str, Any]] = []
        self.loaded = 0

    @property
    def name(self) -> str:
        return "docker"

    async def image_identity(self, image: str, *, timeout_sec: float = 20) -> dict[str, Any]:
        if not self._present:
            return {
                "present": False, "id": None, "repo_digests": [], "tags": [], "rootfs_layers": [],
            }
        return {
            "present": True,
            "id": self._image_id,
            "repo_digests": [],
            "tags": [image],
            "rootfs_layers": list(self._layers),
        }

    async def exists(self, name: str) -> bool:
        return name in self.containers

    async def inspect(self, name: str, *, format_: str | None = None, timeout_sec: float = 10):
        return self.containers.get(name, {"__missing": True})

    async def run(self, **kwargs: Any) -> str:
        self.runs.append(kwargs)
        self.containers[kwargs["name"]] = {
            "Image": self._image_id,
            "State": {"Running": True},
        }
        return "container-id"

    async def ps(self, *, all_: bool = True, timeout_sec: float = 20) -> list[str]:
        import json as _json

        return [_json.dumps({"Names": n}) for n in self.containers]

    async def start(self, name: str, *, timeout_sec: float = 30) -> None:
        self.containers[name]["State"]["Running"] = True

    async def remove(self, name: str, *, force: bool = True, timeout_sec: float = 45) -> None:
        self.containers.pop(name, None)

    async def exec_(
        self,
        name: str,
        command: list[str],
        *,
        env: dict[str, str] | None = None,
        workdir: str | None = None,
        timeout_sec: float = 120,
        raise_on_error: bool = True,
    ) -> tuple[int, str, str]:
        self.execs.append({"name": name, "command": command, "env": dict(env or {})})
        if command[:2] == ["ray", "--version"]:
            return 0, f"ray, version {self._ray_version}\n", ""
        return 0, "", ""


@pytest.mark.anyio()
async def test_start_head_runs_inside_the_pinned_container() -> None:
    """The whole bootstrap happens via exec into the image - no host Ray."""
    runtime = _FakeRuntime()
    container = RayContainerRuntime(runtime=runtime, token_path="/var/run/llm-port/ray/cluster.token")
    spec = RuntimeBundleSpec.from_payload(_payload())

    await container.ensure_image(spec)
    await container.ensure_container(spec, env={"RAY_memory_monitor_refresh_ms": "0"})
    result = await container.start_head(
        spec,
        port=6379,
        dashboard_port=8265,
        dashboard_host="127.0.0.1",
        node_ip_address="10.100.0.1",
        include_dashboard=False,
        env={"RAY_AUTH_MODE": "token", "NCCL_SOCKET_IFNAME": "enp1s0f1np1"},
    )

    assert result["head_address"] == "10.100.0.1:6379"
    assert result["in_container"] == "llm-port-ray-runtime"

    ray_exec = next(e for e in runtime.execs if e["command"][:2] == ["ray", "start"])
    assert "--head" in ray_exec["command"]
    assert "--node-ip-address=10.100.0.1" in ray_exec["command"]
    assert "--include-dashboard=false" in ray_exec["command"]
    assert ray_exec["env"]["NCCL_SOCKET_IFNAME"] == "enp1s0f1np1"


@pytest.mark.anyio()
async def test_semantic_requirements_become_handler_flags() -> None:
    """The manifest stays free of CLI strings; the agent owns the mapping."""
    runtime = _FakeRuntime()
    container = RayContainerRuntime(runtime=runtime)
    spec = RuntimeBundleSpec.from_payload(_payload())

    await container.ensure_container(spec)

    run = runtime.runs[0]
    flags = run["extra_args"]
    assert flags[flags.index("--network") + 1] == "host"
    assert flags[flags.index("--ipc") + 1] == "host"
    assert flags[flags.index("--device") + 1] == "/dev/infiniband"
    assert flags[flags.index("--cap-add") + 1] == "IPC_LOCK"
    assert run["gpus"] == "all"
    assert "/srv/llm-port/models:/models:ro" in run["volumes"]
    # The cluster token file is mounted read-only so the container can attach.
    assert any(v.startswith("/var/run/llm-port/ray:") for v in run["volumes"])
    assert run["env"]["RAY_AUTH_TOKEN_PATH"].endswith("cluster.token")


@pytest.mark.anyio()
async def test_ensure_runtime_image_refuses_a_different_image() -> None:
    """A tag match is not identity: pinning is the integrity mechanism.

    Different config id *and* different content - the case where the tag has
    been moved to some other image entirely.
    """
    runtime = _FakeRuntime(image_id=OTHER_DIGEST, layers=OTHER_LAYERS)
    container = RayContainerRuntime(runtime=runtime)
    spec = RuntimeBundleSpec.from_payload(_payload())

    with pytest.raises(RuntimeDigestMismatch):
        await container.ensure_image(spec)


@pytest.mark.anyio()
async def test_missing_image_without_a_loader_is_reported_not_pulled() -> None:
    """Offline mode: a missing image is a failure, never an internet pull."""
    runtime = _FakeRuntime(present=False)
    container = RayContainerRuntime(runtime=runtime)
    spec = RuntimeBundleSpec.from_payload(_payload())

    with pytest.raises(RuntimeImageMissing):
        await container.ensure_image(spec)


@pytest.mark.anyio()
async def test_missing_image_is_side_loaded_from_the_backend() -> None:
    """Air-gap path: the backend streams the image and it is re-verified."""
    runtime = _FakeRuntime(present=False)
    container = RayContainerRuntime(runtime=runtime)
    spec = RuntimeBundleSpec.from_payload(_payload())

    async def _loader(_spec: RuntimeBundleSpec) -> None:
        runtime._present = True
        runtime.loaded += 1

    result = await container.ensure_image(spec, loader=_loader)
    assert runtime.loaded == 1
    assert result["verified"] is True
    assert result["image_id"] == DIGEST


@pytest.mark.anyio()
async def test_container_running_a_different_image_is_replaced() -> None:
    """The pinned digest is the contract, including for an existing container."""
    runtime = _FakeRuntime()
    container = RayContainerRuntime(runtime=runtime)
    spec = RuntimeBundleSpec.from_payload(_payload())
    runtime.containers[spec.name] = {"Image": OTHER_DIGEST, "State": {"Running": True}}

    result = await container.ensure_container(spec)
    assert result["created"] is True
    assert runtime.containers[spec.name]["Image"] == DIGEST


@pytest.mark.anyio()
async def test_unpinned_bundle_is_refused() -> None:
    """A bundle without a digest must not resolve to whatever tag is local."""
    with pytest.raises(ValueError):
        RuntimeBundleSpec.from_payload(_payload(digest=""))


@pytest.mark.anyio()
async def test_ensure_runtime_reports_installed_with_no_host_ray(tmp_path) -> None:
    """Phase 4B exit criterion, at the command boundary.

    ``RayManager.ensure_runtime`` must answer ``installed=True`` from the
    container's own CLI even though nothing on this host has Ray.
    """
    from llm_port_node_agent.event_buffer import EventBuffer
    from llm_port_node_agent.ray.manager import RayManager
    from llm_port_node_agent.state_store import StateStore

    runtime = _FakeRuntime()
    manager = RayManager(
        state_store=StateStore(tmp_path / "state.json"),
        events=EventBuffer(),
        ray_base_path=str(tmp_path / "ray-base"),
        token_dir=tmp_path / "ray-tokens",
    )
    manager._container = RayContainerRuntime(runtime=runtime)
    # The host CLI is deliberately absent: ray_base_path is empty and nothing
    # in this venv layout is consulted for the container path.
    assert not (tmp_path / "ray-base" / "2.58.0" / "bin" / "ray").exists()

    result = await manager.ensure_runtime(
        {"version": "2.58.0", "runtime_bundle": _payload()}
    )
    assert result["installed"] is True
    assert result["runtime"] == "container"
    assert result["version"] == "2.58.0"
    assert result["digest_verified"] is True


@pytest.mark.anyio()
async def test_stop_ray_tears_down_the_container() -> None:
    """A stop must not leave the runtime container running."""
    runtime = _FakeRuntime()
    container = RayContainerRuntime(runtime=runtime)
    spec = RuntimeBundleSpec.from_payload(_payload())
    await container.ensure_container(spec)

    result = await container.stop(spec, force=True, remove=True)
    assert result["stopped"] is True
    assert spec.name not in runtime.containers
    assert ["ray", "stop", "--force"] in [e["command"] for e in runtime.execs]


@pytest.mark.anyio()
async def test_identical_content_under_a_different_config_id_is_accepted() -> None:
    """The two DGX nodes hold the same bits under different config IDs.

    ``docker image inspect`` on spark-ts3202 reports
    ``sha256:7dc13b9a...`` and on spark-3201 ``sha256:d36c047d...`` for the
    same tag, while both list the identical 50 RootFS layer diff IDs - the
    side-load rewrote the config.  Verifying ``.Id`` alone would reject a node
    that is running exactly the certified bits and block every deployment on
    it.
    """
    runtime = _FakeRuntime(image_id=OTHER_DIGEST, layers=LAYERS)
    container = RayContainerRuntime(runtime=runtime)
    spec = RuntimeBundleSpec.from_payload(_payload())

    result = await container.ensure_image(spec)
    assert result["verified"] is True
    assert result["matched_by"] == "rootfs_digest"
    assert result["rootfs_digest"] == ROOTFS_DIGEST


@pytest.mark.anyio()
async def test_different_content_is_still_refused() -> None:
    """Content identity must not become a way to wave anything through."""
    runtime = _FakeRuntime(image_id=OTHER_DIGEST, layers=OTHER_LAYERS)
    container = RayContainerRuntime(runtime=runtime)
    spec = RuntimeBundleSpec.from_payload(_payload())

    with pytest.raises(RuntimeDigestMismatch) as excinfo:
        await container.ensure_image(spec)
    # The error has to name both identities or an operator cannot act on it.
    assert ROOTFS_DIGEST in str(excinfo.value)
    assert OTHER_DIGEST in str(excinfo.value)


@pytest.mark.anyio()
async def test_config_id_alone_still_verifies_when_no_rootfs_is_pinned() -> None:
    """Bundles generated before the content identity existed keep working."""
    runtime = _FakeRuntime(image_id=DIGEST, layers=OTHER_LAYERS)
    container = RayContainerRuntime(runtime=runtime)
    payload = _payload()
    payload.pop("rootfs_digest")
    spec = RuntimeBundleSpec.from_payload(payload)

    result = await container.ensure_image(spec)
    assert result["matched_by"] == "image_id"


@pytest.mark.anyio()
async def test_preflight_reports_a_gpu_that_is_already_busy(monkeypatch) -> None:
    """A rogue process holding GPU memory must be named, not guessed at.

    On spark-3201 an unmanaged ``tmux`` loop kept relaunching old containers
    that held GPU memory; the only symptom was Ray failing with CUDA OOM and
    port conflicts at nondeterministic points in distributed startup.
    """
    async def _busy_gpu() -> list[dict[str, Any]]:
        return [{"index": 0, "used_mib": 86000, "total_mib": 121690}]

    monkeypatch.setattr(RayContainerRuntime, "_gpu_memory_in_use", staticmethod(_busy_gpu))

    runtime = _FakeRuntime()
    runtime.containers["some-other-workload"] = {"Image": OTHER_DIGEST, "State": {"Running": True}}
    container = RayContainerRuntime(runtime=runtime)
    spec = RuntimeBundleSpec.from_payload(_payload())

    result = await container.ensure_container(spec)

    preflight = result["preflight"]
    assert "86000 MiB" in preflight["conflicts"][0]
    assert "some-other-workload" in preflight["foreign_containers"]
    # A busy GPU is a diagnostic, not a veto: the node may legitimately be
    # running other work, so the container still starts.
    assert result["running"] is True


@pytest.mark.anyio()
async def test_preflight_is_quiet_on_an_idle_node(monkeypatch) -> None:
    """Driver overhead must not be reported as a conflict."""
    async def _idle_gpu() -> list[dict[str, Any]]:
        return [{"index": 0, "used_mib": 4, "total_mib": 121690}]

    monkeypatch.setattr(RayContainerRuntime, "_gpu_memory_in_use", staticmethod(_idle_gpu))

    container = RayContainerRuntime(runtime=_FakeRuntime())
    result = await container.ensure_container(RuntimeBundleSpec.from_payload(_payload()))
    assert result["preflight"]["conflicts"] == []


@pytest.mark.anyio()
async def test_a_stale_build_under_the_right_tag_is_replaced_from_the_backend() -> None:
    """The node holds the tag, but not the pinned build: fetch the right one.

    This used to be a dead end. A present-but-wrong image failed the check and
    the loader was never consulted, so a node with a stale copy could not
    recover even when the server held exactly the build the pin wanted.
    """
    runtime = _FakeRuntime(image_id=OTHER_DIGEST, layers=OTHER_LAYERS)
    container = RayContainerRuntime(runtime=runtime)
    spec = RuntimeBundleSpec.from_payload(_payload())

    async def _loader(_spec: RuntimeBundleSpec) -> None:
        runtime._image_id = DIGEST
        runtime._layers = LAYERS
        runtime.loaded += 1

    result = await container.ensure_image(spec, loader=_loader)

    assert runtime.loaded == 1
    assert result["verified"] is True
    assert result["image_id"] == DIGEST


@pytest.mark.anyio()
async def test_a_right_build_is_not_fetched_again() -> None:
    runtime = _FakeRuntime()
    container = RayContainerRuntime(runtime=runtime)
    spec = RuntimeBundleSpec.from_payload(_payload())

    async def _loader(_spec: RuntimeBundleSpec) -> None:
        raise AssertionError("an image that already matches must not be re-sent")

    result = await container.ensure_image(spec, loader=_loader)
    assert result["verified"] is True


@pytest.mark.anyio()
async def test_a_mismatch_reports_a_code_the_backend_can_act_on() -> None:
    """Permanent until the pin or the image changes -- and saying so is the point."""
    runtime = _FakeRuntime(image_id=OTHER_DIGEST, layers=OTHER_LAYERS)
    container = RayContainerRuntime(runtime=runtime)
    spec = RuntimeBundleSpec.from_payload(_payload())

    with pytest.raises(RuntimeDigestMismatch) as caught:
        await container.ensure_image(spec)
    assert caught.value.error_code == "runtime_image_mismatch"
