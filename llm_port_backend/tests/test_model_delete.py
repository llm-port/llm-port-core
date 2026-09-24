"""Deleting a model: refused while a deployment uses it; its files go only when asked, and only ours."""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.dao.llm_dao import ArtifactDAO, DownloadJobDAO, ModelDAO
from llm_port_backend.db.models.llm import LLMModel, ModelSource, ModelStatus
from llm_port_backend.services.llm.service import LLMService
from llm_port_backend.settings import settings

pytestmark = pytest.mark.anyio


def _service() -> LLMService:
    return LLMService(MagicMock())


async def _model(session: AsyncSession, repo: str | None, source: ModelSource = ModelSource.HUGGINGFACE) -> LLMModel:
    model = LLMModel(display_name=repo or "mine", source=source, status=ModelStatus.AVAILABLE, hf_repo_id=repo)
    session.add(model)
    await session.flush()
    return model


def _store(tmp_path: Path, repo: str) -> Path:
    folder = tmp_path / f"models--{repo.replace('/', '--')}" / "snapshots" / "abc"
    folder.mkdir(parents=True)
    (folder / "model.safetensors").write_bytes(b"x" * 1000)
    return tmp_path / f"models--{repo.replace('/', '--')}"


async def _delete(session: AsyncSession, model: LLMModel, *, files: bool) -> int:
    return await _service().delete_model(
        ModelDAO(session), model.id, job_dao=DownloadJobDAO(session), artifact_dao=ArtifactDAO(session),
        remove_files=files,
    )


async def test_a_model_a_deployment_uses_is_not_deleted(dbsession: AsyncSession) -> None:
    """The database refused it anyway (RESTRICT), as an error nobody could read."""
    from llm_port_backend.db.models.inference import InferenceControlPlane, InferenceDeployment, InferenceEnvironment

    model = await _model(dbsession, "Qwen/Qwen3-8B")
    plane = InferenceControlPlane(name=f"cp-{uuid.uuid4().hex[:6]}", driver="ray")
    dbsession.add(plane)
    await dbsession.flush()
    env = InferenceEnvironment(control_plane_id=plane.id, name=f"env-{uuid.uuid4().hex[:6]}")
    dbsession.add(env)
    await dbsession.flush()
    dbsession.add(InferenceDeployment(environment_id=env.id, model_id=model.id, name="qwen3-8b", spec_json={}))
    await dbsession.flush()

    with pytest.raises(ValueError, match="qwen3-8b"):
        await _delete(dbsession, model, files=False)
    assert await ModelDAO(dbsession).get(model.id) is not None


async def test_its_files_go_only_when_asked(dbsession: AsyncSession, tmp_path: Path,
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "model_store_root", str(tmp_path))
    kept = await _model(dbsession, "Qwen/Qwen3-0.6B")
    folder = _store(tmp_path, "Qwen/Qwen3-0.6B")
    assert await _delete(dbsession, kept, files=False) == 0
    assert folder.is_dir(), "a plain delete forgets the record, not the files"

    again = await _model(dbsession, "Qwen/Qwen3-0.6B")
    assert await _delete(dbsession, again, files=True) == 1000
    assert not folder.exists()


async def test_files_another_record_uses_stay(dbsession: AsyncSession, tmp_path: Path,
                                             monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "model_store_root", str(tmp_path))
    folder = _store(tmp_path, "Qwen/Qwen3-4B")
    first = await _model(dbsession, "Qwen/Qwen3-4B")
    await _model(dbsession, "Qwen/Qwen3-4B")
    assert await _delete(dbsession, first, files=True) == 0
    assert folder.is_dir()


async def test_a_model_registered_from_a_path_keeps_its_files(dbsession: AsyncSession, tmp_path: Path,
                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    """Those are the operator's own files, wherever they are."""
    monkeypatch.setattr(settings, "model_store_root", str(tmp_path))
    folder = _store(tmp_path, "org/finetune")
    model = await _model(dbsession, "org/finetune", source=ModelSource.LOCAL_PATH)
    assert await _delete(dbsession, model, files=True) == 0
    assert folder.is_dir()
