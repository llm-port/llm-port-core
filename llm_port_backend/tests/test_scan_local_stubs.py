"""A model on this server has weights; reading a model's page leaves nothing behind.

Found in an end-to-end run: "On this server" listed eleven models as ready on a
fresh install. Nine were stubs -- a config.json and a README the marketplace had
fetched into the Hugging Face cache while showing their pages -- and the scan
that runs when the tab opens imported every one of them.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from llm_port_backend.services.llm import service as llm_service_mod
from llm_port_backend.services.llm.service import LLMService
from llm_port_backend.services.marketplace import hub


def _cached_repo(cache: Path, repo_id: str, files: dict[str, bytes]) -> None:
    """A Hugging Face cache entry the way huggingface_hub lays it out (copies, not links)."""
    folder = cache / ("models--" + repo_id.replace("/", "--"))
    revision = "0123456789abcdef0123456789abcdef01234567"
    (folder / "refs").mkdir(parents=True)
    (folder / "refs" / "main").write_text(revision)
    snapshot = folder / "snapshots" / revision
    snapshot.mkdir(parents=True)
    (folder / "blobs").mkdir()
    for name, data in files.items():
        (snapshot / name).write_bytes(data)


@pytest.mark.anyio
async def test_only_cached_models_with_weights_are_imported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = tmp_path / "store"
    _cached_repo(store, "Qwen/Qwen3-0.6B", {"config.json": b"{}", "README.md": b"# Qwen3"})  # a page was read
    _cached_repo(store, "Qwen/Qwen3-Embedding-0.6B", {"config.json": b"{}", "model.safetensors": b"\0" * 64})
    monkeypatch.setattr(llm_service_mod.settings, "model_store_root", str(store))
    monkeypatch.setattr(llm_service_mod.settings, "host_hf_cache_dir", "")
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))

    created: list[str] = []

    async def create(**kw: Any) -> SimpleNamespace:
        created.append(kw["hf_repo_id"])
        return SimpleNamespace(id=len(created), **kw)

    model_dao = SimpleNamespace(list_all=AsyncMock(return_value=[]), create=create)
    artifact_dao = SimpleNamespace(create_batch=AsyncMock())
    service = LLMService.__new__(LLMService)

    imported = await service.auto_import_hf_cache(model_dao, artifact_dao)

    assert created == ["Qwen/Qwen3-Embedding-0.6B"]
    assert [m.hf_repo_id for m in imported] == created


@pytest.mark.anyio
async def test_a_model_page_reads_its_metadata_into_a_cache_of_its_own(monkeypatch: pytest.MonkeyPatch) -> None:
    hub.clear_cache()
    asked: list[dict[str, Any]] = []

    def fake_download(repo_id: str, filename: str, **kw: Any) -> str:
        asked.append({"filename": filename, **kw})
        raise FileNotFoundError(filename)  # the page still stands without them

    info = SimpleNamespace(
        id="Qwen/Qwen3-0.6B", author="Qwen", downloads=1, likes=1, trending_score=1, created_at=None,
        last_modified=None, pipeline_tag="text-generation", library_name="transformers", tags=[], gated=False,
        card_data=None, config={}, safetensors=None,
        siblings=[SimpleNamespace(rfilename="config.json", size=10), SimpleNamespace(rfilename="README.md", size=10)],
    )
    monkeypatch.setattr(hub.HubClient, "_api", lambda self: SimpleNamespace(model_info=lambda *a, **k: info))
    monkeypatch.setattr("huggingface_hub.hf_hub_download", fake_download)

    await hub.HubClient().detail("Qwen/Qwen3-0.6B")

    assert {a["filename"] for a in asked} == {"config.json", "README.md"}
    assert all(a.get("cache_dir") == hub.METADATA_CACHE for a in asked), "never the Hugging Face cache"
