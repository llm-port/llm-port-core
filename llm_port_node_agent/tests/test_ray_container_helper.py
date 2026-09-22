"""Status and Serve operations through the in-container helper (PR-5E).

The Phase 4B boundary says Ray-aware SDK/status/deployment operations live in
``llm_port_ray_runtime`` inside the certified image, and the host agent only
invokes them.  These tests pin that: every call below goes out as an
``llm-port-ray-runtime`` verb, and the host never constructs Ray code.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from llm_port_node_agent.ray.container import (
    DEFAULT_CONTAINER_NAME,
    RayContainerRuntime,
)
from llm_port_node_agent.runtimes import ContainerRuntimeError


class _HelperRuntime:
    """Container handler that answers helper verbs from a script."""

    def __init__(self, responses: dict[str, Any], *, running: bool = True) -> None:
        self._responses = responses
        self._running = running
        self.calls: list[dict[str, Any]] = []

    @property
    def name(self) -> str:
        return "docker"

    async def exists(self, name: str) -> bool:
        return self._running

    async def inspect(self, name: str, *, format_: str | None = None, timeout_sec: float = 10):
        return {"State": {"Running": self._running}}

    async def exec_(
        self,
        name: str,
        command: list[str],
        *,
        env: dict[str, str] | None = None,
        workdir: str | None = None,
        stdin: str | None = None,
        timeout_sec: float = 120,
        raise_on_error: bool = True,
    ) -> tuple[int, str, str]:
        self.calls.append({"name": name, "command": command, "stdin": stdin})
        verb = command[1] if len(command) > 1 else ""
        payload = self._responses.get(verb)
        if payload is None:
            return 1, "", f"no scripted response for {verb}"
        # The helper prints one JSON document, sometimes after library noise.
        return 0, "warning: something from a library\n" + json.dumps(payload), ""


def _runtime(responses: dict[str, Any], **kw: Any) -> RayContainerRuntime:
    return RayContainerRuntime(runtime=_HelperRuntime(responses, **kw))


@pytest.mark.anyio()
async def test_cluster_status_emits_the_same_contract_as_the_host_path() -> None:
    """``version`` is the key the backend reads.

    The helper reports ``ray_version``; emitting only that made the backend
    parse ``version=None`` for every containerized cluster and lose the
    observed Ray version entirely.
    """
    container = _runtime({
        "cluster-status": {
            "alive": True,
            "ray_version": "2.58.0",
            "num_nodes": 2,
            "nodes": [{"node_id": "a", "alive": True}],
            "total_gpus": 2.0,
            "total_cpus": 40.0,
            "cluster_address": "10.100.0.1:6379",
            "head_address": "10.100.0.1",
        }
    })

    status = await container.get_cluster_status(DEFAULT_CONTAINER_NAME)

    assert status["version"] == "2.58.0"
    assert status["ray_version"] == "2.58.0"
    assert status["alive"] is True
    assert status["num_nodes"] == 2
    assert status["available_gpus"] == 2.0  # defaults to total when absent


@pytest.mark.anyio()
async def test_cluster_status_reports_a_version_mismatch() -> None:
    """The parity check has to survive the move into the container."""
    container = _runtime({"cluster-status": {"alive": True, "ray_version": "2.57.0"}})

    status = await container.get_cluster_status(
        DEFAULT_CONTAINER_NAME, expected_version="2.58.0"
    )

    assert status["version_mismatch"] == {"expected": "2.58.0", "observed": "2.57.0"}


@pytest.mark.anyio()
async def test_metrics_targets_are_derived_from_live_nodes() -> None:
    """``include_metrics`` was silently dropped, so the tier never populated."""
    container = _runtime({
        "cluster-status": {
            "alive": True,
            "ray_version": "2.58.0",
            "nodes": [
                {"node_id": "h", "node_manager_address": "10.100.0.1",
                 "metrics_export_port": 8089, "alive": True},
                {"node_id": "w", "node_manager_address": "10.100.0.2",
                 "metrics_export_port": 8089, "alive": True},
                {"node_id": "dead", "node_manager_address": "10.100.0.3",
                 "metrics_export_port": 8089, "alive": False},
            ],
        }
    })

    status = await container.get_cluster_status(DEFAULT_CONTAINER_NAME, include_metrics=True)

    metrics = status["metrics"]
    assert metrics["enabled"] is True
    assert [t["url"] for t in metrics["targets"]] == [
        "http://10.100.0.1:8089/metrics",
        "http://10.100.0.2:8089/metrics",
    ]
    assert status["capabilities"]["metrics"] is True


@pytest.mark.anyio()
async def test_run_serve_app_goes_through_the_helper_on_stdin() -> None:
    """The Serve call must be the helper's, not Python generated on the host.

    Host-generated source would not be versioned with the image, would not be
    covered by its certification, and would run against whichever Ray the host
    happened to have.
    """
    container = _runtime({
        "run-serve-app": {"deployed": True, "app_name": "app-1", "route_prefix": "/"}
    })
    args = {"llm_configs": [{"model_id": "qwen"}]}

    result = await container.run_serve_app(
        DEFAULT_CONTAINER_NAME, "app-1", args, http_options={"port": 8000},
    )

    assert result["deployed"] is True
    call = container._runtime.calls[-1]
    assert call["command"][0] == "llm-port-ray-runtime"
    assert call["command"][1] == "run-serve-app"
    assert "--app-name" in call["command"] and "app-1" in call["command"]
    # The compiled document travels on stdin, not argv.
    assert call["stdin"] is not None
    assert json.loads(call["stdin"])["llm_serving_args"] == args
    assert not any("import" in part for part in call["command"]), "host-generated Python leaked in"


@pytest.mark.anyio()
async def test_run_serve_app_surfaces_the_helper_error() -> None:
    """A deploy failure must name the cause, not a generic exit code."""
    container = _runtime({
        "run-serve-app": {"deployed": False, "app_name": "app-1", "error": "bad engine args"}
    })

    with pytest.raises(ContainerRuntimeError) as exc:
        await container.run_serve_app(DEFAULT_CONTAINER_NAME, "app-1", {})
    assert "bad engine args" in str(exc.value)


@pytest.mark.anyio()
async def test_delete_serve_app_is_idempotent() -> None:
    """Deleting an app that was never deployed is a converged state."""
    container = _runtime({
        "delete-serve-app": {"deleted": True, "app_name": "gone", "detail": "application not present"}
    })

    result = await container.delete_serve_app(DEFAULT_CONTAINER_NAME, "gone")

    assert result["deleted"] is True
    assert container._runtime.calls[-1]["command"][:2] == ["llm-port-ray-runtime", "delete-serve-app"]


@pytest.mark.anyio()
async def test_serve_status_filters_to_one_application() -> None:
    container = _runtime({
        "serve-status": {"available": True, "applications": {"a": {"status": "RUNNING"}}}
    })

    result = await container.get_serve_status(DEFAULT_CONTAINER_NAME, app_name="a")

    assert result["available"] is True
    assert "--app-name" in container._runtime.calls[-1]["command"]


@pytest.mark.anyio()
async def test_is_running_is_false_when_the_container_is_absent() -> None:
    container = _runtime({}, running=False)
    assert await container.is_running(DEFAULT_CONTAINER_NAME) is False


@pytest.mark.anyio()
async def test_unparseable_helper_output_is_not_mistaken_for_a_live_cluster() -> None:
    """A broken helper must read as "not alive", never as a healthy default."""

    class _Noise(_HelperRuntime):
        async def exec_(self, *a: Any, **k: Any) -> tuple[int, str, str]:
            return 1, "Traceback (most recent call last): ...", "boom"

    container = RayContainerRuntime(runtime=_Noise({}))
    status = await container.get_cluster_status(DEFAULT_CONTAINER_NAME)
    assert status["alive"] is False
    assert status["version"] is None


# ---------------------------------------------------------------------------
# Mode selection belongs to the backend, not to local state
# ---------------------------------------------------------------------------


def _manager(tmp_path, runtime):
    from llm_port_node_agent.event_buffer import EventBuffer
    from llm_port_node_agent.ray.manager import RayManager
    from llm_port_node_agent.state_store import StateStore

    manager = RayManager(
        state_store=StateStore(tmp_path / "state.json"),
        events=EventBuffer(),
        ray_base_path=str(tmp_path / "ray-base"),
        token_dir=tmp_path / "ray-tokens",
    )
    manager._container = RayContainerRuntime(runtime=runtime)
    return manager


def _bundle_payload(name: str = DEFAULT_CONTAINER_NAME) -> dict[str, Any]:
    return {
        "name": name,
        "image": "llmport/ray-vllm-gb10:ray2.58-nv26.08",
        "digest": "sha256:" + "7d" * 32,
        "requirements": {},
        "mounts": [],
        "env": {},
    }


@pytest.mark.anyio()
async def test_a_stray_container_cannot_hijack_a_host_based_environment(tmp_path) -> None:
    """Without a bundle on the command, the host path is used.

    Selecting the mode by "is a container running?" let a leftover container
    from a previous environment answer for a host-based one.
    """
    runtime = _HelperRuntime({"cluster-status": {"alive": True, "ray_version": "9.9.9"}})
    manager = _manager(tmp_path, runtime)

    # No runtime_bundle -> must not consult the container at all.
    assert await manager._use_container({}) is False
    assert runtime.calls == []


@pytest.mark.anyio()
async def test_a_bundled_command_with_no_container_fails_loudly(tmp_path) -> None:
    """Falling back to the host SDK would report a dead cluster, not the cause.

    A certified node has no host Ray, so a silent fallback turns "the runtime
    container is not running" into "the cluster is down" and the environment
    flips to FAILED for the wrong reason.
    """
    runtime = _HelperRuntime({}, running=False)
    manager = _manager(tmp_path, runtime)

    with pytest.raises(RuntimeError) as exc:
        await manager._use_container({"runtime_bundle": _bundle_payload()})
    assert "not running" in str(exc.value)


@pytest.mark.anyio()
async def test_status_tiers_reach_the_container(tmp_path) -> None:
    """include_serve / include_metrics / expected_version must be forwarded."""
    runtime = _HelperRuntime({
        "cluster-status": {
            "alive": True,
            "ray_version": "2.58.0",
            "nodes": [{"node_id": "h", "node_manager_address": "10.100.0.1",
                       "metrics_export_port": 8089, "alive": True}],
        },
        "serve-status": {"available": True, "applications": {}},
    })
    manager = _manager(tmp_path, runtime)

    result = await manager.get_status({
        "runtime_bundle": _bundle_payload(),
        "include_serve": True,
        "include_metrics": True,
        "expected_version": "2.58.0",
    })

    assert result["version"] == "2.58.0"
    assert result["serve"]["available"] is True
    assert result["metrics"]["enabled"] is True
    assert "version_mismatch" not in result


def test_session_mismatch_counts_as_already_running() -> None:
    """Ray 2.58's phrasing for "a head is already up here".

    Starting a head over a live session does not produce "already running";
    ``_write_cluster_info_to_kv`` asserts the new session name against the one
    persisted in the GCS KV store.  Treating that as a hard failure turned a
    healthy two-node cluster into ``failed`` on the next reconcile pass.
    """
    from llm_port_node_agent.ray.container import _means_already_running

    detail = (
        "assertionerror: session name session_2026-09-21_06-01-04_502717_4415 "
        "does not match persisted value b'session_2026-09-21_05-58-09_508397_98'. "
        "perhaps there was an error connecting to the gcs storage backend."
    )
    assert _means_already_running(detail)
    # Still a failure when the head genuinely could not start.
    assert not _means_already_running("ray start failed: port 6379 unreachable")
