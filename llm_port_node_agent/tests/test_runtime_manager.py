"""Tests for RuntimeManager — Docker arg sanitization, GPU, resource limits."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from llm_port_node_agent.event_buffer import EventBuffer
from llm_port_node_agent.runtime_manager import RuntimeManager, RuntimeManagerError
from llm_port_node_agent.state_store import StateStore


class FakeRuntime:
    """In-memory runtime that records calls for assertions."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []
        self.name = "fake"

    async def run(self, **kwargs: Any) -> str:
        self.calls.append(("run", (), kwargs))
        return "container123"

    async def start(self, name: str, **kw: Any) -> None:
        self.calls.append(("start", (name,), kw))

    async def stop(self, name: str, **kw: Any) -> None:
        self.calls.append(("stop", (name,), kw))

    async def restart(self, name: str, **kw: Any) -> None:
        self.calls.append(("restart", (name,), kw))

    async def remove(self, name: str, **kw: Any) -> None:
        self.calls.append(("remove", (name,), kw))

    async def inspect(self, name: str, **kw: Any) -> dict:
        self.calls.append(("inspect", (name,), kw))
        return {"__missing": True}

    async def exists(self, name: str) -> bool:
        self.calls.append(("exists", (name,), {}))
        return False

    async def port(self, name: str, container_port: str, **kw: Any) -> str | None:
        self.calls.append(("port", (name, container_port), kw))
        return "32001"

    async def logs(self, name: str, **kw: Any) -> tuple[int, str]:
        self.calls.append(("logs", (name,), kw))
        return (0, "")

    async def ps(self, **kw: Any) -> list[str]:
        self.calls.append(("ps", (), kw))
        return []

    async def images(self, **kw: Any) -> list[str]:
        self.calls.append(("images", (), kw))
        return []

    async def pull(self, image: str, **kw: Any) -> None:
        self.calls.append(("pull", (image,), kw))

    async def load_image_tar(self, stream: Any, **kw: Any) -> str:
        self.calls.append(("load_image_tar", (stream,), kw))
        return "Loaded"

    async def is_available(self) -> bool:
        return True

    def run_kwargs(self) -> dict[str, Any]:
        """Return kwargs from the first ``run`` call."""
        for method, _, kwargs in self.calls:
            if method == "run":
                return kwargs
        return {}


@pytest.fixture()
def fake_runtime() -> FakeRuntime:
    return FakeRuntime()


@pytest.fixture()
def manager(tmp_path: Path, fake_runtime: FakeRuntime) -> RuntimeManager:
    store = StateStore(tmp_path / "state.json")
    events = EventBuffer()
    return RuntimeManager(
        runtime=fake_runtime,
        state_store=store,
        events=events,
        advertise_host="10.0.0.1",
        advertise_scheme="http",
    )


@pytest.fixture()
def healthy_health(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    """Make the readiness probe report HTTP 200 immediately.

    Deploys now *fail* if the container never becomes ready, so every test
    that exercises a full deploy must stub the health endpoint.
    """
    from llm_port_node_agent import runtime_manager as rm

    class _OKResponse:
        status_code = 200

    async def _fake_get(self, url, **kwargs):
        return _OKResponse()

    monkeypatch.setattr(rm.httpx.AsyncClient, "get", _fake_get)
    return AsyncMock()


def test_sanitize_rejects_privileged() -> None:
    with pytest.raises(RuntimeManagerError, match="--privileged"):
        RuntimeManager._sanitize_command_args(["--privileged"])


def test_sanitize_rejects_cap_add() -> None:
    with pytest.raises(RuntimeManagerError, match="--cap-add"):
        RuntimeManager._sanitize_command_args(["--cap-add=SYS_ADMIN"])


def test_sanitize_rejects_pid_host() -> None:
    with pytest.raises(RuntimeManagerError, match="--pid"):
        RuntimeManager._sanitize_command_args(["--pid=host"])


def test_sanitize_rejects_volume_mount() -> None:
    with pytest.raises(RuntimeManagerError, match="Volume mounts"):
        RuntimeManager._sanitize_command_args(["-v", "/host:/container"])


def test_sanitize_rejects_network_host() -> None:
    with pytest.raises(RuntimeManagerError, match="Host network"):
        RuntimeManager._sanitize_command_args(["--network=host"])


def test_sanitize_allows_clean_args() -> None:
    # Should not raise
    RuntimeManager._sanitize_command_args(["--model", "/models/llama.gguf", "--port", "8080"])


def test_container_name_is_deterministic() -> None:
    runtime_id = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    name = RuntimeManager._container_name(runtime_id, runtime_name="test-model")
    assert name == "llm-port-test-model"
    # Same name regardless of runtime_id
    name2 = RuntimeManager._container_name("11111111-2222-3333-4444-555555555555", runtime_name="test-model")
    assert name == name2


@pytest.mark.asyncio()
async def test_deploy_workload_includes_gpu_flag(manager: RuntimeManager, fake_runtime: FakeRuntime, healthy_health: AsyncMock) -> None:
    """Verify GPU and memory settings are passed to the runtime."""
    result = await manager.deploy_workload({
        "runtime_id": "rt-001",
        "provider_type": "vllm",
        "image": "vllm/vllm-openai:latest",
        "gpu_request": "all",
        "memory_limit": "16g",
        "model_sync": {"hf_repo_id": "org/model"},
    })
    assert result["runtime_id"] == "rt-001"

    kw = fake_runtime.run_kwargs()
    assert kw["gpus"] == "all"
    assert "--memory" in (kw.get("extra_args") or [])
    assert "16g" in (kw.get("extra_args") or [])
    # Default shm for GPU workloads
    assert "--shm-size" in (kw.get("extra_args") or [])
    assert "1g" in (kw.get("extra_args") or [])


@pytest.mark.asyncio()
async def test_deploy_workload_custom_port(manager: RuntimeManager, fake_runtime: FakeRuntime, healthy_health: AsyncMock) -> None:
    """Verify custom container_port is used."""
    await manager.deploy_workload({
        "runtime_id": "rt-002",
        "image": "test:latest",
        "container_port": "3000",
        "model_sync": {"hf_repo_id": "org/model"},
    })
    kw = fake_runtime.run_kwargs()
    # Host port is pinned to the container port (host:container binding).
    assert "3000:3000" in (kw.get("ports") or [])


@pytest.mark.asyncio()
async def test_deploy_workload_ipc_mode_default(manager: RuntimeManager, fake_runtime: FakeRuntime, healthy_health: AsyncMock) -> None:
    """vLLM with GPU defaults to --ipc host."""
    await manager.deploy_workload({
        "runtime_id": "rt-003",
        "provider_type": "vllm",
        "image": "vllm/vllm-openai:latest",
        "model_sync": {"hf_repo_id": "org/model"},
    })
    kw = fake_runtime.run_kwargs()
    # gpu defaults to all for vllm, ipc defaults to host for vllm+gpu
    assert kw["gpus"] == "all"
    extra = kw.get("extra_args") or []
    assert "--ipc" in extra
    idx = extra.index("--ipc")
    assert extra[idx + 1] == "host"


# ── vLLM command building ────────────────────────────────────


@pytest.mark.asyncio()
async def test_deploy_vllm_builds_command_from_model_sync(
    manager: RuntimeManager, fake_runtime: FakeRuntime, healthy_health: AsyncMock,
) -> None:
    """When no command is provided, vLLM deploys build a serve command."""
    await manager.deploy_workload({
        "runtime_id": "rt-vllm-1",
        "provider_type": "vllm",
        "image": "nvcr.io/nvidia/vllm:26.01-py3",
        "model_sync": {"hf_repo_id": "meta-llama/Llama-3-8B", "source": "download_from_hf"},
    })
    kw = fake_runtime.run_kwargs()
    cmd = kw.get("command") or []
    assert "serve" in cmd
    assert "--model" in cmd
    assert "meta-llama/Llama-3-8B" in cmd
    assert "--host" in cmd
    assert "0.0.0.0" in cmd
    # Entrypoint should be overridden to "vllm"
    assert kw.get("entrypoint") == "vllm"


@pytest.mark.asyncio()
async def test_deploy_vllm_applies_generic_config(
    manager: RuntimeManager, fake_runtime: FakeRuntime, healthy_health: AsyncMock,
) -> None:
    """Generic config keys are mapped to vLLM CLI flags."""
    await manager.deploy_workload({
        "runtime_id": "rt-vllm-2",
        "provider_type": "vllm",
        "image": "nvcr.io/nvidia/vllm:26.01-py3",
        "model_sync": {"hf_repo_id": "my/model"},
        "generic_config": {
            "max_model_len": 4096,
            "tensor_parallel_size": 2,
            "enforce_eager": True,
        },
    })
    kw = fake_runtime.run_kwargs()
    cmd = kw.get("command") or []
    assert "--max-model-len" in cmd
    assert "4096" in cmd
    assert "--tensor-parallel-size" in cmd
    assert "2" in cmd
    assert "--enforce-eager" in cmd


@pytest.mark.asyncio()
async def test_deploy_vllm_applies_engine_args(
    manager: RuntimeManager, fake_runtime: FakeRuntime, healthy_health: AsyncMock,
) -> None:
    """engine_args from provider_config become CLI flags."""
    await manager.deploy_workload({
        "runtime_id": "rt-vllm-3",
        "provider_type": "vllm",
        "image": "vllm/vllm-openai:latest",
        "model_sync": {"hf_repo_id": "my/model"},
        "provider_config": {
            "engine_args": {"quantization": "awq", "enable-prefix-caching": True},
        },
    })
    kw = fake_runtime.run_kwargs()
    cmd = kw.get("command") or []
    assert "--quantization" in cmd
    assert "awq" in cmd
    assert "--enable-prefix-caching" in cmd


@pytest.mark.asyncio()
async def test_deploy_vllm_explicit_command_skips_builder(
    manager: RuntimeManager, fake_runtime: FakeRuntime, healthy_health: AsyncMock,
) -> None:
    """An explicit command in provider_config should be used as-is."""
    await manager.deploy_workload({
        "runtime_id": "rt-vllm-4",
        "provider_type": "vllm",
        "image": "nvcr.io/nvidia/vllm:26.01-py3",
        "model_sync": {"hf_repo_id": "my/model"},
        "provider_config": {"command": "vllm serve /data/model --port 8000"},
    })
    kw = fake_runtime.run_kwargs()
    cmd = kw.get("command") or []
    assert cmd == ["vllm", "serve", "/data/model", "--port", "8000"]
    # No entrypoint override when explicit command is given
    assert kw.get("entrypoint") is None


# ── vLLM command building: fail-fast ─────────────────────────


@pytest.mark.asyncio()
async def test_deploy_vllm_missing_model_fails_fast(
    manager: RuntimeManager, fake_runtime: FakeRuntime, healthy_health: AsyncMock,
) -> None:
    """A vLLM deploy with no resolvable model must hard-fail, not emit an
    empty command (which would run the image's default CMD)."""
    with pytest.raises(RuntimeManagerError, match="no model"):
        await manager.deploy_workload({
            "runtime_id": "rt-vllm-nomodel",
            "provider_type": "vllm",
            "image": "nvcr.io/nvidia/vllm:26.01-py3",
            # no model_sync, no generic_config.model, no provider_config.command
        })
    # The container must not have been started
    assert fake_runtime.run_kwargs() == {}


# ── readiness: timeout now fails with log tail ───────────────


@pytest.mark.asyncio()
async def test_deploy_readiness_timeout_fails_with_log_tail(
    manager: RuntimeManager, fake_runtime: FakeRuntime, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the health endpoint never returns 200 the deploy must report a
    failure and include the container's log tail."""
    # Make the health probe return 500 forever.
    from llm_port_node_agent import runtime_manager as rm

    class _BadResponse:
        status_code = 500

    async def _fake_get(self, url, **kwargs):
        return _BadResponse()

    monkeypatch.setattr(rm.httpx.AsyncClient, "get", _fake_get)
    # Keep the test fast: cap the readiness window at 2 seconds.
    monkeypatch.setattr(rm, "_READINESS_TIMEOUT_SEC", 2)

    # The container's logs carry the root-cause line.
    async def _fake_logs(name, **kw):
        return (0, "ValueError: Too large swap space. 4.00 GiB out of 3.82 GiB total CPU memory")

    fake_runtime.logs = _fake_logs

    with pytest.raises(RuntimeManagerError, match="did not become ready") as excinfo:
        await manager.deploy_workload({
            "runtime_id": "rt-vllm-unhealthy",
            "provider_type": "vllm",
            "image": "nvcr.io/nvidia/vllm:26.01-py3",
            "model_sync": {"hf_repo_id": "meta-llama/Llama-3-8B"},
        }, emit_progress=None)
    # The timeout branch must surface the container's log tail.
    assert "container log tail" in str(excinfo.value)
    assert "Too large swap space" in str(excinfo.value)


# ── log tail cap ─────────────────────────────────────────────


@pytest.mark.asyncio()
async def test_fetch_container_logs_caps_tail(
    manager: RuntimeManager, fake_runtime: FakeRuntime, healthy_health: AsyncMock,
) -> None:
    """fetch_container_logs must clamp the requested tail length."""
    seen: dict[str, Any] = {}

    async def _spy_logs(name, **kw):
        seen["tail"] = kw.get("tail")
        return (0, "")

    fake_runtime.logs = _spy_logs
    await manager.deploy_workload({
        "runtime_id": "rt-logs-1",
        "provider_type": "vllm",
        "image": "img:latest",
        "model_sync": {"hf_repo_id": "my/model"},
    })
    await manager.fetch_container_logs({"runtime_id": "rt-logs-1", "tail": 99999})
    assert seen["tail"] == "200"
    await manager.fetch_container_logs({"runtime_id": "rt-logs-1", "tail": 1})
    assert seen["tail"] == "10"


# ── image transfer skip when present ─────────────────────────


@pytest.mark.asyncio()
async def test_image_transfer_skipped_when_present(
    manager: RuntimeManager, fake_runtime: FakeRuntime, healthy_health: AsyncMock,
) -> None:
    """transfer_from_server must not re-transfer an image already pulled."""
    fake_runtime.images = AsyncMock(
        return_value=[json.dumps({"Repository": "vllm/vllm-openai", "Tag": "latest"})]
    )
    # If the (unconfigured) loader were called, deploy would raise;
    # success proves the transfer was skipped.
    result = await manager.deploy_workload({
        "runtime_id": "rt-img-skip",
        "provider_type": "vllm",
        "image": "vllm/vllm-openai:latest",
        "image_source": "transfer_from_server",
        "model_sync": {"hf_repo_id": "my/model"},
    })
    assert result["runtime_id"] == "rt-img-skip"
    fake_runtime.images.assert_awaited()
