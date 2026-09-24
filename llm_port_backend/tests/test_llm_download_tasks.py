"""Unit tests for the LLM model download task pipeline in ``services/llm/tasks.py``.

Covers two layers:

1. ``_do_download`` — the pure download+register pipeline.  All DAO imports
   and the HF download / directory scan are function-local, so we patch the
   real module attributes (``ModelDAO``, ``DownloadJobDAO``, ``ArtifactDAO``
   on ``llm_port_backend.db.dao.llm_dao``) and the top-level
   ``_run_download_sync`` and ``scan_model_directory``.

2. ``download_model_task`` — the taskiq-wrapped task's crash handler, which
   must (a) mark model/job as failed in a *fresh* session, (b) return
   ``{"status": "failed", "error": ...}``.  We call ``original_func``
   directly (bypassing the broker) and stub the DAOs used by the crash
   handler, plus ``_resolve_hf_token`` and ``broker.state.fastapi_app``.

No RabbitMQ, no Postgres, no HuggingFace network.
"""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import llm_port_backend.db.dao.llm_dao as llm_dao_mod
import llm_port_backend.services.llm.scanner as scanner_mod
import llm_port_backend.services.llm.tasks as tasks_mod
from llm_port_backend.db.models.llm import DownloadJobStatus, ModelStatus
from llm_port_backend.tkq import broker


_MODEL_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")
_JOB_ID = uuid.UUID("00000000-0000-4000-8000-000000000002")
_HF_REPO = "org/very-small-model"
_TARGET = "/tmp/fake-target"


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────────


def _fake_session() -> SimpleNamespace:
    session = SimpleNamespace()
    session.commit = AsyncMock()
    session.close = AsyncMock()
    return session


def _fake_artifact(name: str) -> SimpleNamespace:
    return SimpleNamespace(file_name=name, size_bytes=100, role="weights")


# ──────────────────────────────────────────────────────────────────────────────
# _do_download — success path
# ──────────────────────────────────────────────────────────────────────────────


async def test_do_download_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_dao = MagicMock()
    model_dao.set_status = AsyncMock()
    job_dao = MagicMock()
    job_dao.update_progress = AsyncMock()
    job_dao.status_of = AsyncMock(return_value=DownloadJobStatus.RUNNING)
    artifact_dao = MagicMock()
    artifact_dao.create_batch = AsyncMock()

    monkeypatch.setattr(llm_dao_mod, "ModelDAO", lambda session: model_dao)
    monkeypatch.setattr(llm_dao_mod, "DownloadJobDAO", lambda session: job_dao)
    monkeypatch.setattr(llm_dao_mod, "ArtifactDAO", lambda session: artifact_dao)
    monkeypatch.setattr(
        tasks_mod,
        "_run_download_sync",
        lambda *a, **k: "/tmp/fake-snapshot",
    )
    monkeypatch.setattr(
        scanner_mod,
        "scan_model_directory",
        lambda path: [_fake_artifact("a.safetensors"), _fake_artifact("b.safetensors")],
    )

    session = _fake_session()
    result = await tasks_mod._do_download(
        session=session,
        model_id=_MODEL_ID,
        job_id=_JOB_ID,
        hf_repo_id=_HF_REPO,
        hf_revision=None,
        target_dir=_TARGET,
        hf_token=None,
    )
    assert result == {"status": "success", "artifacts": 2}
    model_dao.set_status.assert_awaited_once_with(_MODEL_ID, ModelStatus.AVAILABLE)
    # artifact_dao.create_batch called once with the model_id and the list of artifacts
    artifact_dao.create_batch.assert_awaited_once()
    args, _ = artifact_dao.create_batch.await_args
    assert args[0] is _MODEL_ID
    # job_dao.update_progress was called with the SUCCESS and 100% at the end
    last_call_pct = job_dao.update_progress.await_args.args[1]
    last_call_status = job_dao.update_progress.await_args.args[2]
    assert last_call_pct == 100
    assert last_call_status is DownloadJobStatus.SUCCESS


async def test_do_download_no_artifacts_skips_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model_dao = MagicMock()
    model_dao.set_status = AsyncMock()
    job_dao = MagicMock()
    job_dao.update_progress = AsyncMock()
    job_dao.status_of = AsyncMock(return_value=DownloadJobStatus.RUNNING)
    artifact_dao = MagicMock()
    artifact_dao.create_batch = AsyncMock()

    monkeypatch.setattr(llm_dao_mod, "ModelDAO", lambda session: model_dao)
    monkeypatch.setattr(llm_dao_mod, "DownloadJobDAO", lambda session: job_dao)
    monkeypatch.setattr(llm_dao_mod, "ArtifactDAO", lambda session: artifact_dao)
    monkeypatch.setattr(tasks_mod, "_run_download_sync", lambda *a, **k: "/tmp/snap")
    monkeypatch.setattr(scanner_mod, "scan_model_directory", lambda path: [])

    result = await tasks_mod._do_download(
        session=_fake_session(),
        model_id=_MODEL_ID,
        job_id=_JOB_ID,
        hf_repo_id=_HF_REPO,
        hf_revision="main",
        target_dir=_TARGET,
        hf_token=None,
    )
    assert result == {"status": "success", "artifacts": 0}
    artifact_dao.create_batch.assert_not_awaited()
    model_dao.set_status.assert_awaited_once_with(_MODEL_ID, ModelStatus.AVAILABLE)


# ──────────────────────────────────────────────────────────────────────────────
# download_model_task — crash handler
# ──────────────────────────────────────────────────────────────────────────────


def _install_fake_broker_app(monkeypatch: pytest.MonkeyPatch) -> None:
    """Install a fake FastAPI-style app on broker.state for the task to read."""
    session = _fake_session()
    app = SimpleNamespace(state=SimpleNamespace(db_session_factory=lambda: session))
    # Reinstall in case a previous test left state behind
    broker.state.fastapi_app = app  # type: ignore[attr-defined]
    monkeypatch.setattr(broker.state, "fastapi_app", app, raising=False)


async def test_download_model_task_crash_marks_model_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The crash handler constructs fresh DAOs in a fresh session — patch the
    # llm_dao module attributes so we can observe set_status/set_failed.
    model_dao = MagicMock()
    model_dao.set_status = AsyncMock()
    job_dao = MagicMock()
    job_dao.set_failed = AsyncMock()
    monkeypatch.setattr(llm_dao_mod, "ModelDAO", lambda session: model_dao)
    monkeypatch.setattr(llm_dao_mod, "DownloadJobDAO", lambda session: job_dao)

    # Stub the inner _do_download to blow up
    boom = Exception("HF network down")
    monkeypatch.setattr(
        tasks_mod,
        "_do_download",
        (lambda **kw: (_ for _ in ()).throw(boom)),
    )
    # Stub _resolve_hf_token so it doesn't touch the real DB
    monkeypatch.setattr(tasks_mod, "_resolve_hf_token", AsyncMock(return_value=None))

    _install_fake_broker_app(monkeypatch)

    result = await tasks_mod.download_model_task.original_func(
        str(_MODEL_ID),
        str(_JOB_ID),
        _HF_REPO,
        None,
        _TARGET,
    )
    assert result == {"status": "failed", "error": "HF network down"}
    model_dao.set_status.assert_awaited_once_with(_MODEL_ID, ModelStatus.FAILED)
    job_dao.set_failed.assert_awaited_once_with(_JOB_ID, "HF network down")


async def test_download_model_task_happy_path_delegates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Stub the inner _do_download to a normal return; confirm the task wraps
    # the same return.  We only touch the outer wrapper here.
    expected = {"status": "success", "artifacts": 3}
    calls: list[tuple[str, str]] = []

    async def fake_do_download(**kw: object) -> dict:
        calls.append(("ok", str(kw.get("hf_repo_id"))))
        return expected

    monkeypatch.setattr(tasks_mod, "_do_download", fake_do_download)
    monkeypatch.setattr(tasks_mod, "_resolve_hf_token", AsyncMock(return_value=None))
    _install_fake_broker_app(monkeypatch)

    result = await tasks_mod.download_model_task.original_func(
        str(_MODEL_ID),
        str(_JOB_ID),
        _HF_REPO,
        None,
        _TARGET,
    )
    assert result == expected
    assert calls == [("ok", _HF_REPO)]


# ──────────────────────────────────────────────────────────────────────────────
# Invalid UUID argument — the task should blow up early with ValueError.
# (We don't catch ValueError in the wrapper; it propagates and is surfaced
# by taskiq as a task-level exception, so the raw original_func call also
# raises it here.)
# ──────────────────────────────────────────────────────────────────────────────


async def test_download_model_task_invalid_uuid_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tasks_mod, "_resolve_hf_token", AsyncMock(return_value=None))
    _install_fake_broker_app(monkeypatch)
    with pytest.raises(ValueError):
        await tasks_mod.download_model_task.original_func(
            "not-a-uuid",
            str(_JOB_ID),
            _HF_REPO,
            None,
            _TARGET,
        )


# -- progress follows bytes, and cancel stops the download ------------------


def _fake_hub(monkeypatch: pytest.MonkeyPatch, sizes: dict[str, int], seen: list[str]) -> None:
    import huggingface_hub
    from types import SimpleNamespace

    class _Api:
        def __init__(self, token: str | None = None) -> None:
            pass

        def model_info(self, repo_id: str, revision: str | None = None, files_metadata: bool = False):
            return SimpleNamespace(
                siblings=[SimpleNamespace(rfilename=n, size=b) for n, b in sizes.items()], sha="abc123",
            )

    def _download(repo_id: str, filename: str, **_: object) -> str:
        seen.append(filename)
        return filename

    monkeypatch.setattr(huggingface_hub, "HfApi", _Api)
    monkeypatch.setattr(huggingface_hub, "hf_hub_download", _download)


def test_progress_follows_the_bytes_not_the_file_count(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """One 16 GB weights file and nine small ones: counting files read 90% before the weights started."""
    sizes = {f"small-{i}.json": 1_000 for i in range(9)} | {"model.safetensors": 16_000_000_000}
    seen: list[str] = []
    _fake_hub(monkeypatch, sizes, seen)
    reported: list[int] = []

    tasks_mod._run_download_sync("org/m", "main", str(tmp_path), None, reported.append)

    assert len(seen) == 10
    small_done = [pct for pct, name in zip(reported, seen) if name.startswith("small")]
    assert max(small_done) == 0, "nine small files are almost none of the bytes"
    assert reported[-1] == 85


def test_a_canceled_download_stops_before_the_next_file(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    import threading

    seen: list[str] = []
    _fake_hub(monkeypatch, {"a.safetensors": 10, "b.safetensors": 10, "c.safetensors": 10}, seen)
    canceled = threading.Event()

    def progress(pct: int) -> None:
        canceled.set()  # the job is canceled while the first file downloads

    with pytest.raises(tasks_mod.DownloadCanceled):
        tasks_mod._run_download_sync("org/m", "main", str(tmp_path), None, progress, canceled)
    assert seen == ["a.safetensors"]


async def test_a_canceled_job_leaves_the_model_failed_not_available(monkeypatch: pytest.MonkeyPatch) -> None:
    model_dao = MagicMock()
    model_dao.set_status = AsyncMock()
    job_dao = MagicMock()
    job_dao.update_progress = AsyncMock()
    job_dao.status_of = AsyncMock(return_value=DownloadJobStatus.CANCELED)
    monkeypatch.setattr(llm_dao_mod, "ModelDAO", lambda session: model_dao)
    monkeypatch.setattr(llm_dao_mod, "DownloadJobDAO", lambda session: job_dao)
    monkeypatch.setattr(llm_dao_mod, "ArtifactDAO", lambda session: MagicMock())

    def _canceled(*_a: object, **_k: object) -> str:
        raise tasks_mod.DownloadCanceled("org/m")

    monkeypatch.setattr(tasks_mod, "_run_download_sync", _canceled)
    result = await tasks_mod._do_download(
        session=_fake_session(), model_id=_MODEL_ID, job_id=_JOB_ID, hf_repo_id=_HF_REPO,
        hf_revision=None, target_dir=_TARGET, hf_token=None,
    )
    assert result == {"status": "canceled"}
    model_dao.set_status.assert_awaited_once_with(_MODEL_ID, ModelStatus.FAILED)
    assert all(call.args[2] is not DownloadJobStatus.SUCCESS for call in job_dao.update_progress.await_args_list)

