"""Downloading a repo that is already kept does not keep it twice.

Each download used to create a new model record. The provider wizard
downloads on every Hugging Face runtime it creates, so after a few runs the
deploy picker offered ``Qwen2.5-0.5B-Instruct`` three times -- one of them a
failed download, which has no files to deploy -- with nothing to tell them
apart.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from llm_port_backend.db.models.llm import ModelStatus
from llm_port_backend.services.llm import tasks as llm_tasks
from llm_port_backend.services.llm.service import LLMService

REPO = "Qwen/Qwen2.5-0.5B-Instruct"


class _Models:
    def __init__(self, kept: Any = None) -> None:
        self.kept = kept
        self.created: list[Any] = []

    async def find_by_repo(self, hf_repo_id: str, hf_revision: str | None = None) -> Any:
        return self.kept

    async def create(self, display_name: str, source: Any, **fields: Any) -> Any:
        model = SimpleNamespace(id=uuid.uuid4(), display_name=display_name, **fields)
        self.created.append(model)
        return model


class _Jobs:
    def __init__(self, active: Any = None) -> None:
        self.active = active
        self.created: list[Any] = []

    async def active_for_model(self, model_id: uuid.UUID) -> Any:
        return self.active

    async def create(self, model_id: uuid.UUID) -> Any:
        job = SimpleNamespace(id=uuid.uuid4(), model_id=model_id, error_message=None)
        self.created.append(job)
        return job


@pytest.fixture
def dispatched(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    sent: list[dict] = []

    async def kiq(**kwargs: Any) -> None:
        sent.append(kwargs)

    monkeypatch.setattr(llm_tasks.download_model_task, "kiq", kiq)
    return sent


def _service() -> LLMService:
    return LLMService(docker=None)  # type: ignore[arg-type]


def _kept(status: ModelStatus) -> Any:
    return SimpleNamespace(id=uuid.uuid4(), hf_repo_id=REPO, status=status)


@pytest.mark.anyio()
async def test_a_new_repo_is_recorded_and_fetched(dispatched: list[dict]) -> None:
    models, jobs = _Models(), _Jobs()
    model, job = await _service().start_download(models, jobs, hf_repo_id=REPO)  # type: ignore[arg-type]
    assert models.created == [model]
    assert job is jobs.created[0]
    assert len(dispatched) == 1


@pytest.mark.anyio()
async def test_an_available_repo_is_returned_as_it_is(dispatched: list[dict]) -> None:
    kept = _kept(ModelStatus.AVAILABLE)
    models, jobs = _Models(kept), _Jobs()
    model, job = await _service().start_download(models, jobs, hf_repo_id=REPO)  # type: ignore[arg-type]
    assert model is kept
    assert job is None, "nothing to fetch"
    assert models.created == [] and jobs.created == [] and dispatched == []


@pytest.mark.anyio()
async def test_a_download_under_way_is_joined(dispatched: list[dict]) -> None:
    kept = _kept(ModelStatus.DOWNLOADING)
    running = SimpleNamespace(id=uuid.uuid4(), error_message=None)
    models, jobs = _Models(kept), _Jobs(active=running)
    model, job = await _service().start_download(models, jobs, hf_repo_id=REPO)  # type: ignore[arg-type]
    assert (model, job) == (kept, running)
    assert dispatched == [], "one download of one repo at a time"


@pytest.mark.anyio()
async def test_a_failed_download_is_retried_in_place(dispatched: list[dict]) -> None:
    kept = _kept(ModelStatus.FAILED)
    models, jobs = _Models(kept), _Jobs()
    model, job = await _service().start_download(models, jobs, hf_repo_id=REPO)  # type: ignore[arg-type]
    assert model is kept and models.created == []
    assert kept.status == ModelStatus.DOWNLOADING
    assert job is jobs.created[0] and job.model_id == kept.id
    assert dispatched[0]["model_id"] == str(kept.id)


@pytest.mark.anyio()
async def test_a_stalled_download_with_no_job_is_restarted(dispatched: list[dict]) -> None:
    """Status says downloading, but nothing is: fetch it rather than wait forever."""
    kept = _kept(ModelStatus.DOWNLOADING)
    models, jobs = _Models(kept), _Jobs(active=None)
    model, job = await _service().start_download(models, jobs, hf_repo_id=REPO)  # type: ignore[arg-type]
    assert model is kept and job is not None
    assert len(dispatched) == 1
