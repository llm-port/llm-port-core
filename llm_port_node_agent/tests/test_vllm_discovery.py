"""The agent finds the vLLM a machine already runs (Phase 8.1).

The containers below are the real ones found on the project's machines on
2026-09-23, trimmed to the fields discovery reads: a hand-started embedding
server on the workstation, and spark_manager's vLLM containers on the DGX
Sparks. Environment variables are left out of them, as discovery leaves them
out of what it sends.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from llm_port_node_agent.vllm_discovery import (
    describe,
    discover_vllm,
    looks_like_vllm,
    parse_args,
    redact,
)

# Workstation: started by hand with the vllm/vllm-openai image's own entrypoint.
QWEN3_EMBED: dict[str, Any] = {
    "Id": "5f0e6c1f2a3b4c5d6e7f",
    "Name": "/Qwen3-Embed",
    "Config": {
        "Image": "vllm/vllm-openai:v0.8.5",
        "Entrypoint": ["python3", "-m", "vllm.entrypoints.openai.api_server"],
        "Cmd": [
            "--model", "Qwen/Qwen3-Embedding-0.6B", "--task", "embed", "--device", "cuda",
            "--dtype", "float16", "--served-model-name", "qwen3-embedding", "--max-model-len", "8192",
            "--gpu-memory-utilization", "0.45", "--swap-space", "0", "--hf-overrides", '{"is_matryoshka":true}',
        ],
        "Labels": {"maintainer": "NVIDIA CORPORATION", "org.opencontainers.image.version": "22.04"},
    },
    "HostConfig": {
        "NetworkMode": "bridge",
        "DeviceRequests": [{"Driver": "", "Count": -1, "DeviceIDs": None, "Capabilities": [["gpu"]]}],
        "RestartPolicy": {"Name": "unless-stopped"},
        "PortBindings": {"8000/tcp": [{"HostIp": "", "HostPort": "7997"}]},
    },
    "NetworkSettings": {"Ports": {"8000/tcp": [{"HostIp": "0.0.0.0", "HostPort": "7997"}]}},
    "State": {"Status": "running", "StartedAt": "2026-09-23T05:10:00Z", "ExitCode": 0},
    "Mounts": [{"Source": "/root/.cache/huggingface", "Destination": "/root/.cache/huggingface"}],
}

# DGX Spark: spark_manager's container, NVIDIA's image, positional model, an
# empty --api-key, stopped -- so its port is only in the configured bindings.
SPARK_QWEN_FP8: dict[str, Any] = {
    "Id": "9a8b7c6d5e4f3a2b1c0d",
    "Name": "/spark-llm-Qwen-Qwen3.8-27B-FP8-8130",
    "Config": {
        "Image": "nvcr.io/nvidia/vllm:26.05-py3",
        "Entrypoint": ["/opt/nvidia/nvidia_entrypoint.sh"],
        "Cmd": [
            "vllm", "serve", "Qwen/Qwen3.8-27B-FP8", "--dtype", "auto", "--api-key", "",
            "--host", "0.0.0.0", "--port", "8000", "--allowed-origins", '["*"]',
            "--enable-auto-tool-choice", "--tool-call-parser", "qwen3_coder",
            "--tensor-parallel-size", "1", "--kv-cache-dtype", "fp8",
        ],
        "Labels": {
            "com.nvidia.vllm.version": "0.20.1",
            "spark.llm": "true",
            "spark.llm.model": "Qwen/Qwen3.8-27B-FP8",
            "spark.llm.port": "8130",
            "spark.llm.role": "main",
        },
    },
    "HostConfig": {
        "NetworkMode": "bridge",
        "DeviceRequests": [{"Driver": "", "Count": -1, "DeviceIDs": [], "Capabilities": [["gpu"]]}],
        "RestartPolicy": {"Name": "no"},
        "PortBindings": {"8000/tcp": [{"HostIp": "", "HostPort": "8130"}]},
    },
    "NetworkSettings": {"Ports": {}},
    "State": {"Status": "exited", "ExitCode": 0, "FinishedAt": "2026-08-19T10:00:00Z"},
    "Mounts": [],
}


def test_a_hand_started_embedding_server_is_described() -> None:
    found = describe(QWEN3_EMBED)

    assert found["name"] == "Qwen3-Embed"
    assert found["model"] == "Qwen/Qwen3-Embedding-0.6B"
    assert found["served_model_names"] == ["qwen3-embedding"], "the name clients use"
    assert found["task"] == "embeddings" and found["task_from"] == "flags"
    assert (found["port"], found["host_port"]) == (8000, 7997)
    assert found["state"] == "running"
    assert found["gpus"] == "all"
    assert found["managed_by"] is None, "the image's own labels are not a manager"
    assert found["settings"] == {"dtype": "float16", "max_model_len": "8192", "gpu_memory_utilization": "0.45"}
    assert found["api_key_required"] is False


def test_another_tools_stopped_container_is_described_with_its_manager() -> None:
    found = describe(SPARK_QWEN_FP8)

    assert found["model"] == "Qwen/Qwen3.8-27B-FP8", "positional, after `vllm serve`"
    assert found["served_model_names"] == ["Qwen/Qwen3.8-27B-FP8"]
    assert found["host_port"] == 8130, "from the configured bindings: a stopped one has no live ones"
    assert found["state"] == "exited"
    assert found["managed_by"] == "spark"
    assert found["labels"]["spark.llm.role"] == "main"
    assert "com.nvidia.vllm.version" not in found["labels"]
    assert found["api_key_required"] is False, '--api-key "" asks for none'
    assert found["task"] is None
    assert found["settings"]["kv_cache_dtype"] == "fp8"


def test_an_embedding_model_that_does_not_say_so_is_guessed_and_marked_as_guessed() -> None:
    info = json.loads(json.dumps(SPARK_QWEN_FP8))
    info["Config"]["Cmd"][2] = "Qwen/Qwen3-Embedding-0.6B"
    found = describe(info)
    assert (found["task"], found["task_from"]) == ("embeddings", "name")


def test_secrets_never_leave_the_machine() -> None:
    args = ["m", "--api-key", "sk-live-123", "--hf-token=hf_abc", "--port", "8000", "--ssl-keyfile", "/k.pem"]
    assert redact(args) == ["m", "--api-key", "***", "--hf-token=***", "--port", "8000", "--ssl-keyfile", "***"]

    info = json.loads(json.dumps(QWEN3_EMBED))
    info["Config"]["Cmd"] += ["--api-key", "sk-live-123"]
    info["Config"]["Env"] = ["HF_TOKEN=hf_secret"]
    found = describe(info)
    text = json.dumps(found)
    assert "sk-live-123" not in text and "hf_secret" not in text
    assert found["api_key_required"] is True


def test_llm_ports_own_containers_are_not_reported_as_found() -> None:
    info = json.loads(json.dumps(QWEN3_EMBED))
    info["Name"] = "/llm-port-vllm-local"
    assert describe(info) is None
    assert looks_like_vllm({"Names": "llm-port-ray-runtime", "Image": "llmport/ray-runtime-vllm"}) is False


@pytest.mark.parametrize(
    ("config", "host_config", "expect_model", "expect_port"),
    [
        # vllm serve inside a shell
        ({"Image": "vllm/vllm-openai:latest", "Entrypoint": ["/bin/bash", "-c"],
          "Cmd": ["vllm serve meta-llama/Llama-3.1-8B --port 9000 --served-model-name llama"]},
         {"NetworkMode": "host"}, "meta-llama/Llama-3.1-8B", 9000),
        # the image's newer `vllm serve` entrypoint, model by flag
        ({"Image": "vllm/vllm-openai:v0.9.2", "Entrypoint": ["vllm", "serve"],
          "Cmd": ["--model", "org/m", "-tp", "2"]},
         {"NetworkMode": "bridge", "PortBindings": {"8000/tcp": [{"HostPort": "18000"}]}}, "org/m", 18000),
    ],
)
def test_the_command_line_shapes_people_use(config: dict, host_config: dict, expect_model: str, expect_port: int) -> None:
    found = describe({"Name": "/x", "Config": config, "HostConfig": host_config, "State": {"Status": "running"}})
    assert found["model"] == expect_model
    assert found["host_port"] == expect_port


def test_a_vllm_image_doing_something_else_is_not_a_model() -> None:
    info = {"Name": "/debug", "Config": {"Image": "vllm/vllm-openai:latest", "Entrypoint": ["sleep"], "Cmd": ["infinity"]},
            "HostConfig": {}, "State": {"Status": "running"}}
    assert describe(info) is None
    assert describe({"Name": "/vllm-grafana", "Config": {"Image": "grafana/grafana", "Cmd": []}}) is None


def test_several_served_names_keep_the_first_for_clients() -> None:
    parsed = parse_args(["m", "--served-model-name", "chat", "chat-v2", "--port", "8001"])
    assert parsed["served_model_names"] == ["chat", "chat-v2"]
    assert parsed["port"] == 8001


class _Runtime:
    def __init__(self, containers: list[dict[str, Any]]) -> None:
        self.inspected: list[str] = []
        self._by_id = {c["Id"]: c for c in containers}
        self._rows = [
            json.dumps({"ID": c["Id"], "Names": c["Name"].lstrip("/"), "Image": c["Config"]["Image"],
                        "Command": " ".join(c["Config"].get("Cmd") or [])[:20]})
            for c in containers
        ]

    async def ps(self, *, all_: bool = True) -> list[str]:
        return self._rows

    async def inspect(self, name: str) -> dict[str, Any]:
        self.inspected.append(name)
        return self._by_id[name]


@pytest.mark.asyncio
async def test_only_vllm_containers_are_inspected() -> None:
    grafana = {"Id": "gr", "Name": "/vllm-grafana", "Config": {"Image": "grafana/grafana:latest", "Cmd": []}}
    runtime = _Runtime([QWEN3_EMBED, SPARK_QWEN_FP8, grafana])

    found = await discover_vllm(runtime)

    assert [f["name"] for f in found] == ["Qwen3-Embed", "spark-llm-Qwen-Qwen3.8-27B-FP8-8130"]
    assert "gr" not in runtime.inspected, "its name mentions vllm, its image does not"


@pytest.mark.asyncio
async def test_no_container_runtime_is_simply_nothing_found() -> None:
    class _Broken:
        async def ps(self, *, all_: bool = True) -> list[str]:
            raise RuntimeError("docker daemon not running")

    assert await discover_vllm(_Broken()) == []
