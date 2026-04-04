"""Admin root mode (break-glass) endpoint."""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from starlette import status

from llm_port_backend.db.dao.audit_dao import AuditDAO
from llm_port_backend.db.dao.root_session_dao import RootSessionDAO
from llm_port_backend.db.models.containers import AuditResult, RootSession
from llm_port_backend.db.models.users import User
from llm_port_backend.web.api.admin.dependencies import audit_action, require_superuser
from llm_port_backend.web.api.admin.root_mode.schema import (
    RootModeStatusDTO,
    RootSessionDTO,
    StartRootModeRequest,
)

router = APIRouter()


@router.post("/start", response_model=RootSessionDTO, name="start_root_mode")
async def start_root_mode(
    body: StartRootModeRequest,
    user: Annotated[User, Depends(require_superuser)],
    root_dao: RootSessionDAO = Depends(),
    audit_dao: AuditDAO = Depends(),
) -> RootSessionDTO:
    """
    Activate a time-limited break-glass root mode session.

    Requires re-authentication (currently validated via mandatory HTTPS JWT
    bearer token that was just issued) and a mandatory reason string.
    All actions taken during this session are audited at severity=high.
    """
    existing = await root_dao.get_active(user.id)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You already have an active root session. Stop it first.",
        )

    session = await root_dao.create(
        actor_id=user.id,
        reason=body.reason,
        scope=body.scope,
        duration_seconds=body.duration_seconds,
    )

    await audit_action(
        action="root_mode.start",
        target_type="root_session",
        target_id=str(session.id),
        result=AuditResult.ALLOW,
        actor_id=user.id,
        severity="high",
        audit_dao=audit_dao,
        metadata_json=json.dumps(
            {
                "reason": body.reason,
                "scope": body.scope,
                "duration_seconds": body.duration_seconds,
            }
        ),
    )

    return _session_dto(session, active=True)


@router.post("/stop", response_model=RootSessionDTO, name="stop_root_mode")
async def stop_root_mode(
    user: Annotated[User, Depends(require_superuser)],
    root_dao: RootSessionDAO = Depends(),
    audit_dao: AuditDAO = Depends(),
) -> RootSessionDTO:
    """Terminate the current root mode session immediately."""
    existing = await root_dao.get_active(user.id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No active root session found.",
        )

    ended = await root_dao.end_session(existing.id)
    if ended is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to end session."
        )

    await audit_action(
        action="root_mode.stop",
        target_type="root_session",
        target_id=str(ended.id),
        result=AuditResult.ALLOW,
        actor_id=user.id,
        severity="high",
        audit_dao=audit_dao,
    )

    return _session_dto(ended, active=False)


@router.get("/status", response_model=RootModeStatusDTO, name="root_mode_status")
async def root_mode_status(
    user: Annotated[User, Depends(require_superuser)],
    root_dao: RootSessionDAO = Depends(),
) -> RootModeStatusDTO:
    """Check whether the current user has an active root session."""
    session = await root_dao.get_active(user.id)
    if session is None:
        return RootModeStatusDTO(active=False, session=None)
    return RootModeStatusDTO(active=True, session=_session_dto(session, active=True))


def _session_dto(session: object, *, active: bool) -> RootSessionDTO:
    s: RootSession = session  # type: ignore[assignment]
    return RootSessionDTO(
        id=s.id,
        actor_id=s.actor_id,
        start_time=s.start_time,
        end_time=s.end_time,
        reason=s.reason,
        scope=s.scope,
        duration_seconds=s.duration_seconds,
        active=active,
    )
