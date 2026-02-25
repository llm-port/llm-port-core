"""Admin image management endpoints."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends
from starlette import status

from llm_port_backend.db.dao.audit_dao import AuditDAO
from llm_port_backend.db.models.containers import AuditResult, ContainerClass, ContainerPolicy
from llm_port_backend.db.models.users import User
from llm_port_backend.services.docker.client import DockerService
from llm_port_backend.services.policy.enforcement import Action, PolicyEnforcer
from llm_port_backend.web.api.admin.dependencies import (
    audit_action,
    get_docker,
    get_policy_enforcer,
    get_root_mode_active,
    require_superuser,
)
from llm_port_backend.web.api.admin.images.schema import (
    ImageSummaryDTO,
    PruneImagesRequest,
    PruneReport,
    PullImageRequest,
)

router = APIRouter()


@router.get("/", response_model=list[ImageSummaryDTO], name="list_images")
async def list_images(
    docker: DockerService = Depends(get_docker),
    _user: User = Depends(require_superuser),
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


@router.post("/pull", status_code=status.HTTP_204_NO_CONTENT, name="pull_image")
async def pull_image(
    body: PullImageRequest,
    user: User = Depends(require_superuser),
    docker: DockerService = Depends(get_docker),
    enforcer: PolicyEnforcer = Depends(get_policy_enforcer),
    root_mode: bool = Depends(get_root_mode_active),
    audit_dao: AuditDAO = Depends(),
) -> None:
    """Pull an image from a registry. Allowed for all admin users."""
    # image.pull is allowed for admin on all container classes (§5.2);
    # no container class context here, so we use a fixed check:
    # if not root_mode and a platform-specific policy were to be enforced,
    # callers could explicitly register and check. For now allow all superusers.
    await docker.pull_image(from_image=body.image, tag=body.tag)
    await audit_action(
        action="image.pull",
        target_type="image",
        target_id=f"{body.image}:{body.tag}",
        result=AuditResult.ALLOW,
        actor_id=user.id,
        severity="normal",
        audit_dao=audit_dao,
        metadata_json=json.dumps({"image": body.image, "tag": body.tag}),
    )


@router.post("/prune", response_model=PruneReport, name="prune_images")
async def prune_images(
    body: PruneImagesRequest,
    user: User = Depends(require_superuser),
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
