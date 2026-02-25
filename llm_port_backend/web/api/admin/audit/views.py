"""Admin audit log read endpoint."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from llm_port_backend.db.dao.audit_dao import AuditDAO
from llm_port_backend.db.models.users import User
from llm_port_backend.web.api.admin.audit.schema import AuditEventDTO
from llm_port_backend.web.api.admin.dependencies import require_superuser

router = APIRouter()


@router.get("/", response_model=list[AuditEventDTO], name="list_audit_events")
async def list_audit_events(
    actor_id: uuid.UUID | None = Query(default=None),
    action: str | None = Query(default=None),
    target_id: str | None = Query(default=None),
    since: datetime | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    audit_dao: AuditDAO = Depends(),
    _user: Annotated[User, Depends(require_superuser)] = None,  # type: ignore[assignment]
) -> list[AuditEventDTO]:
    """Return audit events with optional filters."""
    events = await audit_dao.list_events(
        actor_id=actor_id,
        action=action,
        target_id=target_id,
        since=since,
        limit=limit,
        offset=offset,
    )
    return [AuditEventDTO.model_validate(e) for e in events]
