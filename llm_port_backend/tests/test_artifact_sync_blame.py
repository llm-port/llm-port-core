"""A server-side gap must not be recorded as a node failure.

What happened on the DGX pair: the backend held no copy of the model, built a
``model_sync`` payload with no files in it, sent that to both nodes anyway,
and recorded each rejection against the node:

    Artifact sync failed on node 10.88.10.71: model_sync payload with files is required.
    Artifact sync failed on node 10.88.10.49: model_sync payload with files is required.

Both machines were healthy and had done exactly the right thing. The operator
is sent to investigate two nodes for a problem that lives entirely on the
server, and the message never mentions the server at all.

The agent's refusal is correct and stays. What changes is that the backend no
longer asks a question it already knows the answer to.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from llm_port_backend.db.models.llm import LLMModel, ModelSource, ModelStatus
from llm_port_backend.services.llm.artifacts import (
    build_model_sync_payload,
    model_sync_carries_files,
)


def _model(repo: str | None = "Qwen/Qwen2.5-0.5B-Instruct") -> LLMModel:
    return LLMModel(
        id=uuid.uuid4(),
        display_name="Qwen2.5-0.5B-Instruct",
        source=ModelSource.HUGGINGFACE,
        status=ModelStatus.AVAILABLE,
        hf_repo_id=repo,
        hf_revision=None,
    )


class TestCarriesFiles:
    def test_a_payload_with_blobs_does(self) -> None:
        assert model_sync_carries_files(
            {"source": "sync_from_server", "blobs": [{"sha256": "a", "size": 1}]}
        )

    def test_a_payload_without_blobs_does_not(self) -> None:
        """The exact shape the backend used to send to every node."""
        assert not model_sync_carries_files(
            {
                "model_id": str(uuid.uuid4()),
                "hf_repo_id": "Qwen/Qwen2.5-0.5B-Instruct",
                "source": "sync_from_server",
            }
        )

    def test_an_empty_blob_list_does_not(self) -> None:
        # The hollow-cache case: a directory exists, and holds nothing.
        assert not model_sync_carries_files({"source": "sync_from_server", "blobs": []})

    def test_none_does_not(self) -> None:
        assert not model_sync_carries_files(None)

    def test_a_download_from_hf_payload_does(self) -> None:
        """Nothing for the server to carry, because the node fetches it."""
        assert model_sync_carries_files(
            {"source": "download_from_hf", "hf_repo_id": "Qwen/Qwen2.5-0.5B-Instruct"}
        )


class TestBuilder:
    def test_no_repo_id_is_still_none(self) -> None:
        assert build_model_sync_payload(_model(repo=None)) is None

    def test_a_model_this_server_lacks_yields_a_fileless_payload(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Not an exception, and not None -- which is why it slipped through.

        ``ensure`` only checked for ``None``, so this went out to every node.
        """
        import llm_port_backend.services.llm.artifacts as mod

        monkeypatch.setattr(mod, "model_cache_dir", lambda _repo: None)
        payload = build_model_sync_payload(_model())

        assert payload is not None
        assert payload["hf_repo_id"] == "Qwen/Qwen2.5-0.5B-Instruct"
        assert not model_sync_carries_files(payload)

    def test_a_hollow_cache_directory_also_yields_a_fileless_payload(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any
    ) -> None:
        """A directory that exists and holds nothing looks present to a path check.

        This is what both DGX nodes had: the folder for the model, 0 files,
        66 bytes, no ``refs/`` and no ``snapshots/``.
        """
        import llm_port_backend.services.llm.artifacts as mod

        hollow = tmp_path / "models--Qwen--Qwen2.5-0.5B-Instruct"
        hollow.mkdir()
        monkeypatch.setattr(mod, "model_cache_dir", lambda _repo: hollow)
        monkeypatch.setattr(mod, "build_cache_manifest", lambda _d: {"blobs": []})

        assert not model_sync_carries_files(build_model_sync_payload(_model()))

    def test_a_real_cache_yields_files(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
        import llm_port_backend.services.llm.artifacts as mod

        real = tmp_path / "models--Qwen--Qwen2.5-0.5B-Instruct"
        real.mkdir()
        monkeypatch.setattr(mod, "model_cache_dir", lambda _repo: real)
        monkeypatch.setattr(
            mod,
            "build_cache_manifest",
            lambda _d: {"blobs": [{"sha256": "abc", "size": 10}], "snapshots": []},
        )

        payload = build_model_sync_payload(_model())
        assert model_sync_carries_files(payload)
        assert payload["blobs"]
