"""Taskiq background tasks for LLM model downloads."""

from __future__ import annotations

import asyncio
import logging
import uuid

from llm_port_backend.tkq import broker

log = logging.getLogger(__name__)

HF_TOKEN_KEY = "llm_backend.hf_token"


async def _resolve_hf_token() -> str | None:
    """Read the HF token from DB (encrypted secret), falling back to env var.

    Priority:
      1. Encrypted secret in ``system_setting_secret`` table
      2. ``LLM_PORT_BACKEND_HF_TOKEN`` env-var (pydantic Settings fallback)
      3. ``None`` (anonymous Hugging Face access)
    """
    from llm_port_backend.db.dao.system_settings_dao import SystemSettingsDAO  # noqa: PLC0415
    from llm_port_backend.services.system_settings.crypto import SettingsCrypto  # noqa: PLC0415
    from llm_port_backend.settings import settings  # noqa: PLC0415

    app = broker.state.fastapi_app
    session = app.state.db_session_factory()
    try:
        dao = SystemSettingsDAO(session)
        secret = await dao.get_secret(HF_TOKEN_KEY)
        if secret and secret.ciphertext:
            crypto = SettingsCrypto(settings.settings_master_key)
            return crypto.decrypt(secret.ciphertext)
    except Exception:
        log.warning("Could not read HF token from DB, falling back to env var", exc_info=True)
    finally:
        await session.close()

    # Fallback to env var
    return settings.hf_token or None


def _run_download_sync(
    hf_repo_id: str,
    hf_revision: str,
    target_dir: str,
    hf_token: str | None,
    progress_callback: object,
) -> str:
    """Run the HF download in a thread — calls *progress_callback(pct)* periodically.

    Downloads into the standard HuggingFace hub cache structure under
    *target_dir* (used as ``cache_dir``).  Returns the absolute path to
    the snapshot directory that contains the model files.
    """
    from pathlib import Path  # noqa: PLC0415

    from huggingface_hub import HfApi, hf_hub_download  # noqa: PLC0415

    Path(target_dir).mkdir(parents=True, exist_ok=True)

    api = HfApi(token=hf_token)
    repo_info = api.model_info(hf_repo_id, revision=hf_revision, files_metadata=True)

    siblings = repo_info.siblings or []
    total_files = len(siblings)
    if total_files == 0:
        # Fall back to snapshot_download if we can't enumerate files
        from huggingface_hub import snapshot_download  # noqa: PLC0415

        snapshot_dir = snapshot_download(
            repo_id=hf_repo_id,
            revision=hf_revision,
            cache_dir=target_dir,
            token=hf_token,
        )
        progress_callback(85)  # type: ignore[operator]
        return snapshot_dir

    log.info(
        "Downloading %d files for %s (rev %s)",
        total_files,
        hf_repo_id,
        hf_revision,
    )

    for idx, sibling in enumerate(siblings, start=1):
        hf_hub_download(
            repo_id=hf_repo_id,
            filename=sibling.rfilename,
            revision=hf_revision,
            cache_dir=target_dir,
            token=hf_token,
        )
        # 0-85% is downloading, 85-90% is scanning, 90-100% is finalising
        pct = int((idx / total_files) * 85)
        progress_callback(pct)  # type: ignore[operator]

    # Derive the snapshot directory from the HF cache structure:
    # {cache_dir}/models--{org}--{model}/snapshots/{commit_hash}/
    commit_hash = repo_info.sha
    model_cache_name = f"models--{hf_repo_id.replace('/', '--')}"
    snapshot_dir = str(Path(target_dir) / model_cache_name / "snapshots" / commit_hash)
    return snapshot_dir


@broker.task(retry_on_error=False)
async def download_model_task(
    model_id: str,
    job_id: str,
    hf_repo_id: str,
    hf_revision: str | None,
    target_dir: str,
) -> dict:
    """
    Download a model from Hugging Face Hub and register its artifacts.

    The HF token is resolved at runtime from the encrypted system settings
    (or env-var fallback) -- it is never transmitted over RabbitMQ.
    """
    from llm_port_backend.db.dao.llm_dao import DownloadJobDAO, ModelDAO  # noqa: PLC0415
    from llm_port_backend.db.models.llm import ModelStatus  # noqa: PLC0415

    _model_id = uuid.UUID(model_id)
    _job_id = uuid.UUID(job_id)

    hf_token = await _resolve_hf_token()

    app = broker.state.fastapi_app
    session = app.state.db_session_factory()

    try:
        return await _do_download(
            session=session,
            model_id=_model_id,
            job_id=_job_id,
            hf_repo_id=hf_repo_id,
            hf_revision=hf_revision,
            target_dir=target_dir,
            hf_token=hf_token,
        )
    except Exception as exc:
        log.exception("Download task crashed for %s: %s", hf_repo_id, exc)
        # Best-effort status update in a *fresh* session so we don't reuse a
        # potentially broken one.
        err_session = app.state.db_session_factory()
        try:
            err_model_dao = ModelDAO(err_session)
            err_job_dao = DownloadJobDAO(err_session)
            await err_model_dao.set_status(_model_id, ModelStatus.FAILED)
            await err_job_dao.set_failed(_job_id, str(exc))
            await err_session.commit()
        except Exception:
            log.exception("Could not update status after crash")
        finally:
            await err_session.close()
        return {"status": "failed", "error": str(exc)}
    finally:
        await session.close()


async def _do_download(
    *,
    session: object,
    model_id: uuid.UUID,
    job_id: uuid.UUID,
    hf_repo_id: str,
    hf_revision: str | None,
    target_dir: str,
    hf_token: str | None,
) -> dict:
    """Inner download logic — any exception propagates to caller."""
    from llm_port_backend.db.dao.llm_dao import ArtifactDAO, DownloadJobDAO, ModelDAO  # noqa: PLC0415
    from llm_port_backend.db.models.llm import DownloadJobStatus, ModelStatus  # noqa: PLC0415
    from llm_port_backend.services.llm.scanner import scan_model_directory  # noqa: PLC0415

    model_dao = ModelDAO(session)  # type: ignore[arg-type]
    job_dao = DownloadJobDAO(session)  # type: ignore[arg-type]
    artifact_dao = ArtifactDAO(session)  # type: ignore[arg-type]

    _last_pct = 0
    _loop = asyncio.get_running_loop()

    async def _flush_progress(pct: int) -> None:
        nonlocal _last_pct
        if pct <= _last_pct:
            return
        _last_pct = pct
        await job_dao.update_progress(job_id, pct, DownloadJobStatus.RUNNING)
        await session.commit()  # type: ignore[union-attr]

    def _sync_progress(pct: int) -> None:
        """Thread-safe bridge: schedule the async DB update on the event loop."""
        try:
            asyncio.run_coroutine_threadsafe(_flush_progress(pct), _loop).result(timeout=10)
        except Exception:
            log.warning("Progress update to %d%% failed (non-fatal)", pct)

    # Mark job as running
    await job_dao.update_progress(job_id, 0, DownloadJobStatus.RUNNING)
    await session.commit()  # type: ignore[union-attr]

    log.info("Starting HF download: %s → %s", hf_repo_id, target_dir)

    revision = hf_revision or "main"

    # Run the blocking HF download in a thread with progress callbacks.
    # target_dir is used as the HF hub cache_dir; the function returns
    # the snapshot directory containing the actual model files.
    snapshot_dir = await asyncio.to_thread(
        _run_download_sync,
        hf_repo_id,
        revision,
        target_dir,
        hf_token,
        _sync_progress,
    )

    # 90%: scanning artifacts
    await _flush_progress(90)

    # Scan downloaded files in the snapshot directory
    artifacts = await asyncio.to_thread(scan_model_directory, snapshot_dir)
    if artifacts:
        await artifact_dao.create_batch(model_id, artifacts)

    # Mark model available and job success
    await model_dao.set_status(model_id, ModelStatus.AVAILABLE)
    await job_dao.update_progress(job_id, 100, DownloadJobStatus.SUCCESS)
    await session.commit()  # type: ignore[union-attr]

    log.info("Download complete: %s (%d artifacts)", hf_repo_id, len(artifacts))
    return {"status": "success", "artifacts": len(artifacts)}
