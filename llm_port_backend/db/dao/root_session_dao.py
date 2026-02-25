"""DAO for root (break-glass) sessions."""

import uuid
from datetime import UTC, datetime

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.dependencies import get_db_session
from llm_port_backend.db.models.containers import RootSession


class RootSessionDAO:
    """Manages break-glass root mode sessions."""

    def __init__(self, session: AsyncSession = Depends(get_db_session)) -> None:
        self.session = session

    async def create(
        self,
        actor_id: uuid.UUID,
        reason: str,
        scope: str = "all",
        duration_seconds: int = 600,
    ) -> RootSession:
        """Start a new root session."""
        session = RootSession(
            id=uuid.uuid4(),
            actor_id=actor_id,
            reason=reason,
            scope=scope,
            duration_seconds=duration_seconds,
        )
        self.session.add(session)
        return session

    async def get_active(self, actor_id: uuid.UUID) -> RootSession | None:
        """Return the active root session for the given actor (not yet expired/closed)."""
        now = datetime.now(tz=UTC)
        result = await self.session.execute(
            select(RootSession).where(
                RootSession.actor_id == actor_id,
                RootSession.end_time.is_(None),
            )
        )
        sessions = list(result.scalars().all())
        # Filter those whose time window (start_time + duration_seconds) has not passed
        for s in sessions:
            elapsed = (now - s.start_time.replace(tzinfo=UTC)).total_seconds()
            if elapsed < s.duration_seconds:
                return s
        return None

    async def end_session(self, session_id: uuid.UUID) -> RootSession | None:
        """Mark the session as ended."""
        result = await self.session.execute(select(RootSession).where(RootSession.id == session_id))
        session = result.scalar_one_or_none()
        if session:
            session.end_time = datetime.now(tz=UTC)
        return session

    async def get_by_id(self, session_id: uuid.UUID) -> RootSession | None:
        """Fetch a session by its primary key."""
        result = await self.session.execute(select(RootSession).where(RootSession.id == session_id))
        return result.scalar_one_or_none()
