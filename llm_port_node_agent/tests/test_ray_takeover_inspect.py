"""Reading a running cluster from Ray itself, for a server that takes it over.

The script is also run against a stand-in ``ray`` package here: the real one
lives only in the runtime image. It was checked against the DGX pair's
cluster (Ray 2.58): the LLM app's configuration sits in the LLMServer
deployment's ``init_kwargs['llm_config']``, whose class is not the public
``ray.serve.llm.LLMConfig``, and the ingress's arguments hold DeploymentHandles
that answer every attribute name.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

from llm_port_node_agent.ray import inspect
from llm_port_node_agent.ray.container import DEFAULT_CONTAINER_NAME, RayContainerRuntime

APP = "llmport-e783c0c2-cde1-4385-9d2e-08ea4bd2a52e"
LLM_CONFIG = {
    "model_loading_config": {"model_id": "Qwen2.5-0.5B-Instruct",
                             "model_source": "/root/.cache/huggingface/models--Qwen--Qwen2.5-0.5B-Instruct/snapshots/7ae5"},
    "engine_kwargs": {"tool_call_parser": "hermes", "gpu_memory_utilization": 0.8},
    "deployment_config": {"num_replicas": 1},
}

# A stand-in for the parts of Ray the script uses.
_FAKE_RAY = {
    "ray/__init__.py": """
        __version__ = "2.58.0"
        def init(**kw): pass
        class _Ctx:
            gcs_address = "10.100.0.2:6379"
        def get_runtime_context(): return _Ctx()
        def nodes():
            return [
                {"NodeID": "h", "NodeManagerAddress": "10.100.0.2", "NodeManagerHostname": "spark-3201",
                 "Alive": True, "Resources": {"GPU": 1.0, "CPU": 20.0, "node:__internal_head__": 1.0,
                                              "accelerator_type:GB10": 1.0}},
                {"NodeID": "w", "NodeManagerAddress": "10.100.0.1", "NodeManagerHostname": "spark-ts3202",
                 "Alive": True, "Resources": {"GPU": 1.0, "CPU": 20.0}},
            ]
    """,
    "ray/serve/__init__.py": "",
    "ray/serve/llm.py": """
        import pydantic
        class LLMConfig(pydantic.BaseModel):  # the public class
            model_loading_config: dict
            engine_kwargs: dict = {}
            deployment_config: dict = {}
            accelerator_type: str | None = None
        class _Internal(LLMConfig):  # what Serve stores: not the public class
            pass
    """,
    "ray/serve/context.py": """
        from ray.serve.llm import _Internal
        CONFIG = __CONFIG__
        class Handle:  # like DeploymentHandle: any attribute is a method
            def __getattr__(self, name):
                return lambda *a, **k: None
        class _RC:
            def __init__(self, args, kwargs):
                self.init_args, self.init_kwargs = args, kwargs
        class _Info:
            def __init__(self, rc): self.replica_config = rc
        class _Client:
            def get_serve_details(self):
                return {"http_options": {"host": "0.0.0.0", "port": 8000}, "applications": {
                    "__APP__": {"route_prefix": "/__APP__", "status": "RUNNING", "deployments": {
                        "LLMServer:Qwen": {"status": "HEALTHY", "replicas": [{"state": "RUNNING"}],
                                           "deployment_config": {"num_replicas": 1}},
                        "OpenAiIngress": {"status": "HEALTHY", "replicas": [{"state": "RUNNING"}]},
                    }},
                    "someone-elses-app": {"route_prefix": "/x", "status": "RUNNING", "deployments": {}},
                }}
            def get_deployment_info(self, name, app):
                if name.startswith("LLMServer"):
                    return _Info(_RC((), {"llm_config": _Internal(**CONFIG)})), None
                return _Info(_RC((), {"llm_deployments": {"q": Handle()}})), "/__APP__"
        def _get_global_client(raise_if_no_controller_running=True):
            return _Client()
    """,
}


@pytest.fixture()
def fake_ray(tmp_path: Path) -> Path:
    for rel, body in _FAKE_RAY.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        text = textwrap.dedent(body).replace("__CONFIG__", repr(LLM_CONFIG)).replace("__APP__", APP)
        path.write_text(text, encoding="utf-8")
    return tmp_path


def _run(fake_ray: Path, verify: dict[str, Any] | None = None) -> dict[str, Any]:
    proc = subprocess.run(  # noqa: S603
        [sys.executable, "-"], input=inspect.script(verify), capture_output=True, text=True,
        env={"PYTHONPATH": str(fake_ray), "SYSTEMROOT": "C:\\Windows", "PATH": ""}, check=False, timeout=60,
    )
    doc = inspect.parse(proc.stdout)
    assert doc is not None, proc.stdout + proc.stderr
    return doc


def test_the_script_reads_members_and_each_apps_configuration(fake_ray: Path) -> None:
    doc = _run(fake_ray)
    assert doc["attached"] is True and doc["errors"] == []
    assert doc["gcs_address"] == "10.100.0.2:6379"
    assert [(n["ip"], n["is_head"]) for n in doc["nodes"]] == [("10.100.0.2", True), ("10.100.0.1", False)]
    ours = next(a for a in doc["apps"] if a["name"] == APP)
    assert ours["llm_configs"][0]["model_loading_config"]["model_id"] == "Qwen2.5-0.5B-Instruct"
    assert ours["llm_configs"][0]["engine_kwargs"]["tool_call_parser"] == "hermes"
    assert len(ours["llm_configs"]) == 1, "the ingress's handles are not taken for configurations"
    other = next(a for a in doc["apps"] if a["name"] == "someone-elses-app")
    assert other["llm_configs"] == [], "an app LLM.Port did not deploy is listed, not read"


def test_a_candidate_that_compiles_to_what_runs_is_equal(fake_ray: Path) -> None:
    doc = _run(fake_ray, {APP: {"llm_configs": [LLM_CONFIG]}})
    assert next(a for a in doc["apps"] if a["name"] == APP)["verify"] == {"equal": True, "diff": []}


def test_a_candidate_that_differs_says_where(fake_ray: Path) -> None:
    other = {**LLM_CONFIG, "engine_kwargs": {**LLM_CONFIG["engine_kwargs"], "tool_call_parser": "llama3_json"}}
    verify = next(a for a in _run(fake_ray, {APP: {"llm_configs": [other]}})["apps"] if a["name"] == APP)["verify"]
    assert verify["equal"] is False
    assert verify["diff"] == [{"path": "llm_configs.0.engine_kwargs.tool_call_parser",
                               "running": "hermes", "candidate": "llama3_json"}]


def test_the_output_is_found_among_rays_own_lines() -> None:
    noisy = "2026-09-24 INFO worker.py:1 -- Connecting\n__INSPECT_BEGIN__{\"attached\": true}__INSPECT_END__\n(some log)"
    assert inspect.parse(noisy) == {"attached": True}
    assert inspect.parse('{"attached": false, "error": "attach: no cluster"}\n') == {
        "attached": False, "error": "attach: no cluster"}
    assert inspect.parse("nothing useful") is None


class _Runtime:
    """A container runtime with the Ray container, whose Python prints *stdout*."""

    def __init__(self, stdout: str, running: bool = True) -> None:
        self.stdout, self.running, self.stdin = stdout, running, None

    @property
    def name(self) -> str:
        return "docker"

    async def exists(self, name: str) -> bool:
        return True

    async def inspect(self, name: str, **_: Any) -> dict[str, Any]:
        return {"State": {"Running": self.running, "StartedAt": "t"}, "Config": {"Image": "llmport/ray:2.58"},
                "Image": "sha256:abc"}

    async def exec_(self, name: str, command: list[str], *, stdin: str | None = None, **_: Any) -> tuple[int, str, str]:
        assert command == ["python3", "-"]
        self.stdin = stdin
        return 0, self.stdout, ""


def _manager(tmp_path: Path, runtime: _Runtime) -> Any:
    from llm_port_node_agent.event_buffer import EventBuffer
    from llm_port_node_agent.ray.manager import RayManager
    from llm_port_node_agent.state_store import StateStore

    manager = RayManager(state_store=StateStore(tmp_path / "state.json"), events=EventBuffer(),
                         ray_base_path=str(tmp_path / "ray-base"), token_dir=tmp_path / "ray-tokens")
    manager._container = RayContainerRuntime(runtime=runtime, token_path=str(tmp_path / "ray-tokens" / "cluster.token"))
    (tmp_path / "ray-tokens").mkdir(exist_ok=True)
    (tmp_path / "ray-tokens" / "cluster.token").write_text("s3cret-token\n", encoding="utf-8")
    return manager


_DOC = "__INSPECT_BEGIN__" + json.dumps({"attached": True, "apps": [], "nodes": [], "errors": []}) + "__INSPECT_END__"


@pytest.mark.anyio()
async def test_describe_hands_over_the_token_only_when_asked(tmp_path: Path) -> None:
    runtime = _Runtime(_DOC)
    manager = _manager(tmp_path, runtime)

    plain = await manager.describe_cluster({})
    assert plain["running"] is True and plain["attached"] is True
    assert "cluster_token" not in plain
    assert plain["container"]["image_id"] == "sha256:abc"

    handed = await manager.describe_cluster({"hand_over_token": True})
    assert handed["cluster_token"] == "s3cret-token"


@pytest.mark.anyio()
async def test_describe_sends_the_candidates_into_the_script(tmp_path: Path) -> None:
    runtime = _Runtime(_DOC)
    await _manager(tmp_path, runtime).describe_cluster({"verify": {APP: {"llm_configs": [LLM_CONFIG]}}})
    assert APP in runtime.stdin and "hermes" in runtime.stdin


@pytest.mark.anyio()
async def test_a_machine_without_the_runtime_says_so(tmp_path: Path) -> None:
    result = await _manager(tmp_path, _Runtime(_DOC, running=False)).describe_cluster({"hand_over_token": True})
    assert result["running"] is False and "cluster_token" not in result


@pytest.mark.anyio()
async def test_no_token_is_handed_over_when_ray_did_not_answer(tmp_path: Path) -> None:
    runtime = _Runtime('{"attached": false, "error": "attach: no cluster"}')
    result = await _manager(tmp_path, runtime).describe_cluster({"hand_over_token": True})
    assert result["attached"] is False and "cluster_token" not in result


@pytest.mark.anyio()
async def test_the_inventory_says_whether_the_runtime_runs() -> None:
    assert (await inspect.runtime_summary(_Runtime(_DOC), DEFAULT_CONTAINER_NAME))["running"] is True

    class _None(_Runtime):
        async def inspect(self, name: str, **_: Any) -> dict[str, Any]:
            return {"__missing": True}

    assert await inspect.runtime_summary(_None(_DOC), DEFAULT_CONTAINER_NAME) is None


@pytest.mark.anyio()
async def test_the_token_goes_back_on_the_reply_and_nowhere_else(tmp_path: Path) -> None:
    """Not into the agent's replay cache on disk, not into the events the backend stores."""
    from unittest.mock import AsyncMock, MagicMock

    from llm_port_node_agent.dispatcher import CommandDispatcher
    from llm_port_node_agent.event_buffer import EventBuffer
    from llm_port_node_agent.policy_guard import PolicyGuard
    from llm_port_node_agent.state_store import StateStore

    manager = _manager(tmp_path, _Runtime(_DOC))
    state = StateStore(tmp_path / "agent-state.json")
    events = EventBuffer()
    dispatcher = CommandDispatcher(state_store=state, runtime_manager=MagicMock(), policy_guard=PolicyGuard(),
                                   events=events, ray_manager=manager)

    reply = await dispatcher.handle(
        {"id": "cmd-1", "command_type": "describe_ray_cluster", "payload": {"hand_over_token": True}}, AsyncMock(),
    )
    assert reply["success"] is True and reply["result"]["cluster_token"] == "s3cret-token"
    assert "s3cret-token" not in (tmp_path / "agent-state.json").read_text(encoding="utf-8")
    assert "s3cret-token" not in json.dumps(events.drain(max_items=100))
    replayed = await dispatcher.handle(
        {"id": "cmd-1", "command_type": "describe_ray_cluster", "payload": {"hand_over_token": True}}, AsyncMock(),
    )
    assert "cluster_token" not in replayed["result"], "a replay does not have it"
