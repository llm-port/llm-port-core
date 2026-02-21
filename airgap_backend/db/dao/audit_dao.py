"""DAO for audit events."""

import uuid
from datetime import datetime

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from airgap_backend.db.dependencies import get_db_session
from airgap_backend.db.models.containers import AuditEvent, AuditResult


class AuditDAO:
    """Write-once audit event log."""

    def __init__(self, session: AsyncSession = Depends(get_db_session)) -> None:
        self.session = session

    async def log(
        self,
        action: str,
        target_type: str,
        target_id: str,
        result: AuditResult,
        actor_id: uuid.UUID | None = None,
        severity: str = "normal",
        metadata_json: str | None = None,
    ) -> AuditEvent:
        """Append an audit event."""
        event = AuditEvent(
            id=uuid.uuid4(),
            actor_id=actor_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            result=result,
            severity=severity,
            metadata_json=metadata_json,
        )
        self.session.add(event)
        return event

    async def list_events(
        self,
        actor_id: uuid.UUID | None = None,
        action: str | None = None,
        target_id: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditEvent]:
        """Query audit events with optional filters."""
        query = select(AuditEvent).order_by(AuditEvent.time.desc())
        if actor_id:
            query = query.where(AuditEvent.actor_id == actor_id)
        if action:
            query = query.where(AuditEvent.action == action)
        if target_id:
            query = query.where(AuditEvent.target_id == target_id)
        if since:
            query = query.where(AuditEvent.time >= since)
        query = query.limit(limit).offset(offset)
        result = await self.session.execute(query)
        return list(result.scalars().all())
