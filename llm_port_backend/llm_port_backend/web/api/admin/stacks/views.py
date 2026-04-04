"""Admin compose stack management endpoints."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from starlette import status

from llm_port_backend.db.dao.audit_dao import AuditDAO
from llm_port_backend.db.dao.stacks_dao import StackRevisionDAO
from llm_port_backend.db.models.containers import (
    AuditResult,
    ContainerClass,
    ContainerPolicy,
)
from llm_port_backend.db.models.users import User
from llm_port_backend.services.policy.enforcement import Action, PolicyEnforcer
from llm_port_backend.web.api.admin.dependencies import (
    audit_action,
    get_policy_enforcer,
    get_root_mode_active,
    require_superuser,
)
from llm_port_backend.web.api.rbac import require_permission
from llm_port_backend.web.api.admin.stacks.schema import (
    DeployStackRequest,
    RollbackStackRequest,
    StackDiffDTO,
    StackRevisionDTO,
    StackSummaryDTO,
)

router = APIRouter()


@router.get("/", response_model=list[StackSummaryDTO], name="list_stacks")
async def list_stacks(
    stacks_dao: StackRevisionDAO = Depends(),
    _user: User = Depends(require_permission("stacks", "read")),
) -> list[StackSummaryDTO]:
    """Return all known stacks with their latest revision."""
    stack_ids = await stacks_dao.list_stacks()
    result: list[StackSummaryDTO] = []
    for sid in stack_ids:
        latest = await stacks_dao.latest(sid)
        if latest:
            result.append(
                StackSummaryDTO(
                    stack_id=sid,
                    latest_rev=latest.rev,
                    created_at=latest.created_at,
                )
            )
    return result


@router.post("/deploy", response_model=StackRevisionDTO, name="deploy_stack")
async def deploy_stack(
    body: DeployStackRequest,
    user: User = Depends(require_permission("stacks", "deploy")),
    stacks_dao: StackRevisionDAO = Depends(),
    enforcer: PolicyEnforcer = Depends(get_policy_enforcer),
    root_mode: bool = Depends(get_root_mode_active),
    audit_dao: AuditDAO = Depends(),
) -> StackRevisionDTO:
    """Deploy (create or update) a compose stack, creating a new revision."""
    enforcer.enforce(Action.STACK_DEPLOY, ContainerClass.TENANT_APP, ContainerPolicy.FREE, root_mode)

    revision = await stacks_dao.create(
        stack_id=body.stack_id,
        compose_yaml=body.compose_yaml,
        env_blob=body.env_blob,
        image_digests=body.image_digests,
        created_by=user.id,
    )

    await audit_action(
        action="stack.deploy",
        target_type="stack",
        target_id=body.stack_id,
        result=AuditResult.ALLOW,
        actor_id=user.id,
        severity="normal" if not root_mode else "high",
        audit_dao=audit_dao,
        metadata_json=json.dumps({"rev": revision.rev}),
    )

    return StackRevisionDTO.model_validate(revision)


@router.post("/{stack_id}/update", response_model=StackRevisionDTO, name="update_stack")
async def update_stack(
    stack_id: str,
    body: DeployStackRequest,
    user: User = Depends(require_permission("stacks", "update")),
    stacks_dao: StackRevisionDAO = Depends(),
    enforcer: PolicyEnforcer = Depends(get_policy_enforcer),
    root_mode: bool = Depends(get_root_mode_active),
    audit_dao: AuditDAO = Depends(),
) -> StackRevisionDTO:
    """Update an existing stack, appending a new revision."""
    if not await stacks_dao.latest(stack_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Stack '{stack_id}' not found.",
        )
    enforcer.enforce(Action.STACK_UPDATE, ContainerClass.TENANT_APP, ContainerPolicy.FREE, root_mode)

    revision = await stacks_dao.create(
        stack_id=stack_id,
        compose_yaml=body.compose_yaml,
        env_blob=body.env_blob,
        image_digests=body.image_digests,
        created_by=user.id,
    )

    await audit_action(
        action="stack.update",
        target_type="stack",
        target_id=stack_id,
        result=AuditResult.ALLOW,
        actor_id=user.id,
        severity="normal" if not root_mode else "high",
        audit_dao=audit_dao,
        metadata_json=json.dumps({"rev": revision.rev}),
    )

    return StackRevisionDTO.model_validate(revision)


@router.post("/{stack_id}/rollback", response_model=StackRevisionDTO, name="rollback_stack")
async def rollback_stack(
    stack_id: str,
    body: RollbackStackRequest,
    user: User = Depends(require_permission("stacks", "rollback")),
    stacks_dao: StackRevisionDAO = Depends(),
    enforcer: PolicyEnforcer = Depends(get_policy_enforcer),
    root_mode: bool = Depends(get_root_mode_active),
    audit_dao: AuditDAO = Depends(),
) -> StackRevisionDTO:
    """Roll back a stack to a specific previous revision, creating a new revision entry."""
    enforcer.enforce(Action.STACK_ROLLBACK, ContainerClass.TENANT_APP, ContainerPolicy.FREE, root_mode)

    target = await stacks_dao.get_revision(stack_id, body.rev)
    if not target:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Revision {body.rev} for stack '{stack_id}' not found.",
        )

    new_rev = await stacks_dao.create(
        stack_id=stack_id,
        compose_yaml=target.compose_yaml,
        env_blob=target.env_blob,
        image_digests=target.image_digests,
        created_by=user.id,
    )

    await audit_action(
        action="stack.rollback",
        target_type="stack",
        target_id=stack_id,
        result=AuditResult.ALLOW,
        actor_id=user.id,
        severity="high",
        audit_dao=audit_dao,
        metadata_json=json.dumps({"from_rev": body.rev, "new_rev": new_rev.rev}),
    )

    return StackRevisionDTO.model_validate(new_rev)


@router.get("/{stack_id}/revisions", response_model=list[StackRevisionDTO], name="list_stack_revisions")
async def list_stack_revisions(
    stack_id: str,
    stacks_dao: StackRevisionDAO = Depends(),
    _user: User = Depends(require_permission("stacks", "read")),
) -> list[StackRevisionDTO]:
    """List all revisions for a stack, newest first."""
    revisions = await stacks_dao.list_revisions(stack_id)
    return [StackRevisionDTO.model_validate(r) for r in revisions]


@router.get("/{stack_id}/diff", response_model=StackDiffDTO, name="stack_diff")
async def stack_diff(
    stack_id: str,
    from_rev: int = Query(..., description="Source revision number."),
    to_rev: int = Query(..., description="Target revision number."),
    stacks_dao: StackRevisionDAO = Depends(),
    _user: User = Depends(require_permission("stacks", "read")),
) -> StackDiffDTO:
    """Return a side-by-side diff between two stack revisions."""
    rev_from = await stacks_dao.get_revision(stack_id, from_rev)
    rev_to = await stacks_dao.get_revision(stack_id, to_rev)

    if not rev_from:
        raise HTTPException(status_code=404, detail=f"Revision {from_rev} not found.")
    if not rev_to:
        raise HTTPException(status_code=404, detail=f"Revision {to_rev} not found.")

    return StackDiffDTO(
        stack_id=stack_id,
        from_rev=from_rev,
        to_rev=to_rev,
        compose_yaml_from=rev_from.compose_yaml,
        compose_yaml_to=rev_to.compose_yaml,
        env_blob_from=rev_from.env_blob,
        env_blob_to=rev_to.env_blob,
        image_digests_from=rev_from.image_digests,
        image_digests_to=rev_to.image_digests,
    )
