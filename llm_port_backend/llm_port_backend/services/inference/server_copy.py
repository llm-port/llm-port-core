"""Getting a model onto the LLM.Port server as part of deploying it.

A cluster's runtime cannot reach the internet, so a model has to be on this
server before it can be copied to a machine. The onboarding guide made that a
separate step and called skipping it "the most common way a deployment
stalls": the deployment was accepted, sat on "Copying the model", and nothing
was being copied because there was nothing to copy.

The deployment knows which model it wants. So when the server does not have
it, start the download here and wait for it, instead of waiting for someone to
notice the ordering.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.models.llm import DownloadJob, DownloadJobStatus, LLMModel, ModelStatus
from llm_port_backend.settings import settings

log = logging.getLogger(__name__)

_ACTIVE = (DownloadJobStatus.QUEUED, DownloadJobStatus.RUNNING)


@dataclass(frozen=True)
class ServerCopy:
    """What the deployment should do about a model the server lacks."""

    #: Set while a download is under way: wait, and show this.
    waiting: str | None = None
    #: Set when it cannot be fetched from here: this is the reason.
    blocker: str | None = None


async def _latest_job(session: AsyncSession, model_id) -> DownloadJob | None:
    result = await session.execute(
        select(DownloadJob)
        .where(DownloadJob.model_id == model_id)
        .order_by(DownloadJob.created_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


def _waiting_message(model: LLMModel, progress: int | None) -> str:
    done = f" ({progress}%)" if progress else ""
    return (
        f"Downloading {model.hf_repo_id} to the LLM.Port server{done}. "
        f"It is copied to the cluster as soon as it is there."
    )


async def ensure_server_copy(session: AsyncSession, model: LLMModel) -> ServerCopy:
    """Start or follow the server-side download of *model*.

    Never starts a second download of the same model, and never restarts one
    that failed: a failure is usually a missing token or a wrong repository
    name, and retrying it on every reconcile pass would be the same storm of
    identical attempts the cluster reconciler used to produce. The operator
    retries from Models once it is fixed, and the deployment picks it up.
    """
    if not model.hf_repo_id:
        return ServerCopy(
            blocker=(
                f"{model.display_name} has no local copy on this server and no "
                f"repository to download it from. Import it on the server first."
            )
        )

    job = await _latest_job(session, model.id)
    if job is not None and job.status in _ACTIVE:
        return ServerCopy(waiting=_waiting_message(model, job.progress))

    if job is not None and job.status == DownloadJobStatus.FAILED:
        reason = job.error_message or "no reason recorded"
        return ServerCopy(
            blocker=(
                f"Downloading {model.hf_repo_id} to the LLM.Port server failed: "
                f"{reason}. Retry it from Models; the deployment carries on once "
                f"the model is there."
            )
        )

    # Nothing under way, and nothing failed: start it.
    from llm_port_backend.db.dao.llm_dao import DownloadJobDAO  # noqa: PLC0415
    from llm_port_backend.services.llm.tasks import download_model_task  # noqa: PLC0415

    new_job = await DownloadJobDAO(session).create(model.id)
    model.status = ModelStatus.DOWNLOADING
    await session.flush()
    try:
        await download_model_task.kiq(
            model_id=str(model.id),
            job_id=str(new_job.id),
            hf_repo_id=model.hf_repo_id,
            hf_revision=model.hf_revision,
            target_dir=settings.model_store_root,
        )
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        new_job.status = DownloadJobStatus.FAILED
        new_job.error_message = f"Could not start the download: {exc}"
        log.warning("Could not dispatch download of %s: %s", model.hf_repo_id, exc)
        return ServerCopy(blocker=f"Could not start downloading {model.hf_repo_id}: {exc}")

    log.info("Started server-side download of %s for a deployment", model.hf_repo_id)
    return ServerCopy(waiting=_waiting_message(model, 0))
