"""Tests for RuntimeManager.sync_model and truthful result reporting (WI-4)."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from llm_port_node_agent.dispatcher import CommandDispatcher
from llm_port_node_agent.event_buffer import EventBuffer
from llm_port_node_agent.model_puller import pull_model
from llm_port_node_agent.runtime_manager import RuntimeManager, RuntimeManagerError
from llm_port_node_agent.policy_guard import PolicyGuard
from llm_port_node_agent.state_store import StateStore


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class DummyRuntime:
    async def is_available(self) -> bool:
        return True


@pytest.mark.asyncio()
async def test_sync_model_truthful_result_with_fake_backend(tmp_path: Path) -> None:
    blob_data = b'{"vocab_size": 32000, "architectures": ["LlamaForCausalLM"]}'
    blob_hash = _sha256(blob_data)
    model_id = "test-model-42"
    hf_repo = "org/test-model"
    model_dir_name = "models--org--test-model"
    commit = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"
    manifest_digest = "sha256-dummy-digest-12345"

    models_root = tmp_path / "srv_models"
    models_root.mkdir(parents=True, exist_ok=True)

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(f"/blob/{blob_hash}"):
            return httpx.Response(200, content=blob_data)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="http://backend.local") as client:
        async def _test_pull_model(*, model_sync: dict, emit_progress: Any = None) -> None:
            await pull_model(
                client=client,
                credential="dummy-token",
                model_sync=model_sync,
                model_store_root=str(models_root),
                emit_progress=emit_progress,
            )

        state = StateStore(tmp_path / "state.json")
        events = EventBuffer()
        manager = RuntimeManager(
            runtime=DummyRuntime(),  # type: ignore[arg-type]
            state_store=state,
            events=events,
            advertise_host="10.0.0.1",
            model_store_root=str(models_root),
            model_puller=_test_pull_model,
        )

        progress_emitted: list[dict[str, Any]] = []

        async def _emit(p: dict[str, Any]) -> None:
            progress_emitted.append(p)

        payload = {
            "model_id": model_id,
            "manifest_sha256": manifest_digest,
            "model_sync": {
                "model_id": model_id,
                "hf_repo_id": hf_repo,
                "model_dir_name": model_dir_name,
                "manifest_sha256": manifest_digest,
                "blobs": [{"hash": blob_hash, "size": len(blob_data)}],
                "refs": [{"name": "main", "commit": commit}],
                "snapshots": [
                    {
                        "commit": commit,
                        "links": [{"path": "config.json", "blob_hash": blob_hash}],
                    },
                ],
                "total_size": len(blob_data),
            },
        }

        result = await manager.sync_model(payload, emit_progress=_emit)

        # Assert full truthful dict per WI-4 specification
        assert result["synced"] is True
        assert result["model_id"] == model_id
        assert result["hf_repo_id"] == hf_repo
        assert result["model_dir_name"] == model_dir_name
        assert result["manifest_sha256"] == manifest_digest
        assert result["revision"] == commit
        assert result["total_size"] == len(blob_data)
        assert result["files_synced"] == 1
        assert result["cache_root"] == str(models_root)

        expected_snapshot_dir = models_root / model_dir_name / "snapshots" / commit
        assert Path(result["root_path"]) == expected_snapshot_dir
        assert expected_snapshot_dir.is_dir()

        # Check snapshot link
        config_link = expected_snapshot_dir / "config.json"
        assert config_link.exists()
        assert config_link.is_symlink()

        # Check ref was written
        ref_file = models_root / model_dir_name / "refs" / "main"
        assert ref_file.exists()
        assert ref_file.read_text(encoding="utf-8").strip() == commit

        # Check progress was emitted
        assert len(progress_emitted) > 0


@pytest.mark.asyncio()
async def test_sync_model_rejects_missing_payload(tmp_path: Path) -> None:
    state = StateStore(tmp_path / "state.json")
    events = EventBuffer()
    manager = RuntimeManager(
        runtime=DummyRuntime(),  # type: ignore[arg-type]
        state_store=state,
        events=events,
        advertise_host="10.0.0.1",
        model_store_root=str(tmp_path),
        model_puller=AsyncMock(),
    )

    with pytest.raises(RuntimeManagerError, match="model_sync payload with files is required"):
        await manager.sync_model({"model_id": "m1"})

    with pytest.raises(RuntimeManagerError, match="model_sync payload with files is required"):
        await manager.sync_model({"model_sync": {"blobs": []}})


@pytest.mark.asyncio()
async def test_dispatcher_routes_sync_model_with_progress(tmp_path: Path) -> None:
    state = StateStore(tmp_path / "state.json")
    events = EventBuffer()
    fake_puller = AsyncMock()

    manager = RuntimeManager(
        runtime=DummyRuntime(),  # type: ignore[arg-type]
        state_store=state,
        events=events,
        advertise_host="10.0.0.1",
        model_store_root=str(tmp_path),
        model_puller=fake_puller,
    )

    dispatcher = CommandDispatcher(
        state_store=state,
        runtime_manager=manager,
        policy_guard=PolicyGuard(),
        events=events,
    )

    progress_seen: list[dict[str, Any]] = []

    async def emit_progress(p: dict[str, Any]) -> None:
        progress_seen.append(p)

    cmd = {
        "id": "cmd-sync-1",
        "command_type": "sync_model",
        "payload": {
            "model_id": "m1",
            "model_sync": {
                "model_id": "m1",
                "hf_repo_id": "my/model",
                "blobs": [{"hash": "abc", "size": 100}],
                "refs": [{"name": "main", "commit": "c1"}],
                "snapshots": [{"commit": "c1", "links": [{"path": "f", "blob_hash": "abc"}]}],
                "manifest_sha256": "digest123",
            },
        },
    }

    res = await dispatcher.handle(cmd, emit_progress)
    assert res["success"] is True
    result = res["result"]
    assert result["synced"] is True
    assert result["model_id"] == "m1"
    assert result["revision"] == "c1"
    assert result["manifest_sha256"] == "digest123"
    fake_puller.assert_awaited_once()
