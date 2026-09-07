"""Unit tests for the pure helpers in `LLMService`.

Covers `_node_container_name`, `_extract_endpoint`, and
`_build_model_sync_payload` — the container-naming, port-extraction, and
sync-payload logic that the node-scheduling path relies on.  No DB, Docker,
or network is required: `LLMService` is only touched through its static
methods, and the node-files import inside `_build_model_sync_payload` is
replaced with fakes.
"""

from __future__ import annotations

import uuid

from llm_port_backend.services.llm.service import LLMService
from llm_port_backend.web.api.node_files import views as node_files_views

MODEL_ID = uuid.UUID("11111111-2222-3333-4444-555555555555")
HF_REPO = "org/mistral-7b-q4"


def _model(hf_repo_id: str | None = HF_REPO, **overrides: object) -> object:
    from types import SimpleNamespace

    base: dict[str, object] = {"id": MODEL_ID, "hf_repo_id": hf_repo_id, "hf_revision": "main"}
    base.update(overrides)
    return SimpleNamespace(**base)


# ──────────────────────────────────────────────────────────────────────────────
# _node_container_name
# ──────────────────────────────────────────────────────────────────────────────


def test_node_container_name_slugifies() -> None:
    # separators (space) become '-', punctuation is dropped, lower-cased
    assert LLMService._node_container_name("My Model!") == "llm-port-my-model"


def test_node_container_name_strips_underscores_and_slashes() -> None:
    assert LLMService._node_container_name("my_model/runtime") == "llm-port-my-model-runtime"


def test_node_container_name_keeps_kebab() -> None:
    assert LLMService._node_container_name("llama-70b-v2") == "llm-port-llama-70b-v2"


def test_node_container_name_truncates_to_48() -> None:
    name = LLMService._node_container_name("a" * 200)
    # "llm-port-" (9) + 48-char slug
    assert len(name) == 9 + 48
    assert name.startswith("llm-port-" + "a" * 48)


def test_node_container_name_empty_falls_back_to_runtime() -> None:
    assert LLMService._node_container_name("") == "llm-port-runtime"
    assert LLMService._node_container_name("!!##") == "llm-port-runtime"


# ──────────────────────────────────────────────────────────────────────────────
# _extract_endpoint
# ──────────────────────────────────────────────────────────────────────────────


def _port_info(host_ip: str, host_port: str) -> dict:
    return {"NetworkSettings": {"Ports": {"8000/tcp": [{"HostIp": host_ip, "HostPort": host_port}]}}}


def test_extract_endpoint_reads_host_port() -> None:
    assert LLMService._extract_endpoint(_port_info("127.0.0.1", "32200")) == "http://127.0.0.1:32200"


def test_extract_endpoint_0000_maps_to_loopback() -> None:
    assert LLMService._extract_endpoint(_port_info("0.0.0.0", "8000")) == "http://127.0.0.1:8000"


def test_extract_endpoint_empty_host_maps_to_loopback() -> None:
    assert LLMService._extract_endpoint(_port_info("", "9000")) == "http://127.0.0.1:9000"


def test_extract_endpoint_defaults_port_to_8000() -> None:
    # binding present but HostPort omitted → defaults to 8000
    info = {"NetworkSettings": {"Ports": {"8000/tcp": [{"HostIp": "127.0.0.1"}]}}}
    assert LLMService._extract_endpoint(info) == "http://127.0.0.1:8000"


def test_extract_endpoint_missing_ports_none() -> None:
    assert LLMService._extract_endpoint({}) is None
    assert LLMService._extract_endpoint({"NetworkSettings": {}}) is None
    assert LLMService._extract_endpoint({"NetworkSettings": {"Ports": {}}}) is None


def test_extract_endpoint_empty_binding_none() -> None:
    # empty list → falsy → None; a single empty dict is also falsy → None
    assert LLMService._extract_endpoint({"NetworkSettings": {"Ports": {"8000/tcp": []}}}) is None
    assert LLMService._extract_endpoint({"NetworkSettings": {"Ports": {"8000/tcp": [{}]} }} ) is None


# ──────────────────────────────────────────────────────────────────────────────
# _build_model_sync_payload
# ──────────────────────────────────────────────────────────────────────────────


def test_sync_payload_download_from_hf_is_inline() -> None:
    payload = LLMService._build_model_sync_payload(_model(), source="download_from_hf")  # type: ignore[arg-type]
    assert payload == {
        "model_id": str(MODEL_ID),
        "hf_repo_id": HF_REPO,
        "source": "download_from_hf",
    }


def test_sync_payload_missing_hf_repo_returns_none() -> None:
    assert LLMService._build_model_sync_payload(_model(hf_repo_id=None)) is None  # type: ignore[arg-type]
    assert LLMService._build_model_sync_payload(_model(), source="download_from_hf") is not None  # type: ignore[arg-type]


def test_sync_payload_uses_cache_manifest(
    monkeypatch,
) -> None:
    manifest = {"blobs": [{"path": "model.safetensors", "size": 5, "sha256": "abc"}]}
    monkeypatch.setattr(node_files_views, "_model_cache_dir", lambda repo: "/models/org/model")
    monkeypatch.setattr(node_files_views, "_build_cache_manifest", lambda path: manifest)

    payload = LLMService._build_model_sync_payload(_model())  # type: ignore[arg-type]
    assert isinstance(payload, dict)
    assert payload["model_id"] == str(MODEL_ID)
    assert payload["source"] == "sync_from_server"
    assert payload["blobs"] == manifest["blobs"]
    # manifest keys are merged into the base payload
    for key, value in manifest.items():
        assert payload[key] == value


def test_sync_payload_no_cache_dir_returns_none(monkeypatch) -> None:
    monkeypatch.setattr(node_files_views, "_model_cache_dir", lambda repo: None)
    assert LLMService._build_model_sync_payload(_model()) is None  # type: ignore[arg-type]


def test_sync_payload_empty_blobs_returns_none(monkeypatch) -> None:
    monkeypatch.setattr(node_files_views, "_model_cache_dir", lambda repo: "/models/x")
    monkeypatch.setattr(node_files_views, "_build_cache_manifest", lambda path: {"blobs": []})
    assert LLMService._build_model_sync_payload(_model()) is None  # type: ignore[arg-type]
