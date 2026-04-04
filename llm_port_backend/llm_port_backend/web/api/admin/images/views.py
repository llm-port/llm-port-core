"""Admin image management endpoints."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from starlette import status

from llm_port_backend.db.dao.audit_dao import AuditDAO
from llm_port_backend.db.models.containers import AuditResult, ContainerClass, ContainerPolicy
from llm_port_backend.db.models.users import User
from llm_port_backend.services.docker.client import DockerService
from llm_port_backend.services.docker.pull_tracker import pull_tracker
from llm_port_backend.services.policy.enforcement import Action, PolicyEnforcer
from llm_port_backend.web.api.admin.dependencies import (
    audit_action,
    get_docker,
    get_policy_enforcer,
    get_root_mode_active,
    require_superuser,
)
from llm_port_backend.web.api.rbac import require_permission
from llm_port_backend.web.api.admin.images.schema import (
    ImageCheckResponse,
    ImageSummaryDTO,
    PruneImagesRequest,
    PruneReport,
    PullImageRequest,
    PullStartedResponse,
)

router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/", response_model=list[ImageSummaryDTO], name="list_images")
async def list_images(
    docker: DockerService = Depends(get_docker),
    _user: User = Depends(require_permission("images", "read")),
) -> list[ImageSummaryDTO]:
    """List all local images."""
    raw_images = await docker.list_images()
    return [
        ImageSummaryDTO(
            id=img.get("Id", ""),
            repo_tags=img.get("RepoTags") or [],
            repo_digests=img.get("RepoDigests") or [],
            size=img.get("Size", 0),
            created=str(img.get("Created", "")),
        )
        for img in raw_images
    ]


@router.get("/check", response_model=ImageCheckResponse, name="check_image")
async def check_image(
    image: str,
    tag: str = "latest",
    docker: DockerService = Depends(get_docker),
    _user: User = Depends(require_permission("images", "read")),
) -> ImageCheckResponse:
    """Check whether an image:tag exists locally, and whether a pull is active."""
    needle = f"{image}:{tag}"
    exists = False
    raw_images = await docker.list_images()
    for img in raw_images:
        for repo_tag in img.get("RepoTags") or []:
            if repo_tag == needle:
                exists = True
                break

    active_job = pull_tracker.get_active_for(image, tag)
    return ImageCheckResponse(
        exists=exists,
        image=image,
        tag=tag,
        pulling=active_job is not None,
        pull_id=active_job.pull_id if active_job else None,
    )


@router.post("/pull", response_model=PullStartedResponse, name="pull_image")
async def pull_image(
    body: PullImageRequest,
    user: User = Depends(require_permission("images", "pull")),
    docker: DockerService = Depends(get_docker),
    enforcer: PolicyEnforcer = Depends(get_policy_enforcer),
    root_mode: bool = Depends(get_root_mode_active),
    audit_dao: AuditDAO = Depends(),
) -> PullStartedResponse:
    """Start pulling an image in the background.

    Returns immediately with a ``pull_id``. If the same image is already
    being pulled, the existing ``pull_id`` is returned (deduplication).

    Use ``GET /pull/{pull_id}/progress`` (SSE) to stream progress.
    """
    job, is_new = await pull_tracker.start_pull(docker, body.image, body.tag)

    if is_new:
        # Audit is best-effort here. Pull start should not fail just because
        # the audit DB path is temporarily degraded.
        try:
            await audit_action(
                action="image.pull",
                target_type="image",
                target_id=f"{body.image}:{body.tag}",
                result=AuditResult.ALLOW,
                actor_id=user.id,
                severity="normal",
                audit_dao=audit_dao,
                metadata_json=json.dumps({"image": body.image, "tag": body.tag, "pull_id": job.pull_id}),
            )
        except Exception:
            logger.exception(
                "Failed to write audit event for image pull start: %s:%s (pull_id=%s)",
                body.image,
                body.tag,
                job.pull_id,
            )

    return PullStartedResponse(
        pull_id=job.pull_id,
        image=body.image,
        tag=body.tag,
        already_pulling=not is_new,
    )


@router.get("/pull/{pull_id}/progress", name="pull_progress")
async def pull_progress(
    pull_id: str,
    _user: User = Depends(require_permission("images", "read")),
) -> StreamingResponse:
    """Stream pull progress as Server-Sent Events.

    Event types: ``progress``, ``complete``, ``error``.
    """
    job = pull_tracker.get_job(pull_id)
    if job is None:
        # Job not found (expired or invalid) — send a single error event
        async def _not_found() -> Any:
            yield f"event: error\ndata: {json.dumps({'error': 'Pull job not found', 'pull_id': pull_id})}\n\n"

        return StreamingResponse(_not_found(), media_type="text/event-stream")

    async def _stream() -> Any:
        try:
            async for msg in pull_tracker.subscribe(job):
                event = msg["event"]
                data = json.dumps(msg["data"])
                yield f"event: {event}\ndata: {data}\n\n"
        except asyncio.CancelledError:
            # Normal when browser closes/reconnects EventSource.
            return
        except Exception as exc:
            logger.warning("Pull progress stream crashed for pull_id=%s: %s", pull_id, exc)
            payload = json.dumps({"error": str(exc), "pull_id": pull_id})
            yield f"event: error\ndata: {payload}\n\n"

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/prune", response_model=PruneReport, name="prune_images")
async def prune_images(
    body: PruneImagesRequest,
    user: User = Depends(require_permission("images", "prune")),
    docker: DockerService = Depends(get_docker),
    enforcer: PolicyEnforcer = Depends(get_policy_enforcer),
    root_mode: bool = Depends(get_root_mode_active),
    audit_dao: AuditDAO = Depends(),
) -> PruneReport:
    """Prune dangling images. Requires Root Mode."""
    enforcer.enforce(
        Action.IMAGE_PRUNE,
        ContainerClass.SYSTEM_CORE,  # most restrictive context
        ContainerPolicy.FREE,
        root_mode,
    )

    if body.dry_run:
        candidates = await docker.prune_images_dry_run()
        return PruneReport(
            deleted=[img.get("Id", "") for img in candidates],
            space_reclaimed=sum(img.get("Size", 0) for img in candidates),
            dry_run=True,
        )

    report = await docker.prune_images()
    deleted = [item.get("Deleted", "") for item in (report.get("ImagesDeleted") or [])]

    await audit_action(
        action="image.prune",
        target_type="image",
        target_id="*",
        result=AuditResult.ALLOW,
        actor_id=user.id,
        severity="high",
        audit_dao=audit_dao,
        metadata_json=json.dumps({"deleted_count": len(deleted)}),
    )

    return PruneReport(
        deleted=deleted,
        space_reclaimed=report.get("SpaceReclaimed", 0),
        dry_run=False,
    )
