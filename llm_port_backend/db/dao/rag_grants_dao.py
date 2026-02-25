"""DAO for RAG container-scoped grants."""

from __future__ import annotations

import uuid

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.dependencies import get_db_session
from llm_port_backend.db.models.rag import RagContainerGrant


class RagGrantDAO:
    """Read helpers for rag container access grants."""

    def __init__(self, session: AsyncSession = Depends(get_db_session)) -> None:
        self.session = session

    async def has_container_action(
        self,
        *,
        user_id: uuid.UUID,
        container_id: str,
        action: str,
    ) -> bool:
        """Return whether user has explicit action on container."""
        result = await self.session.execute(
            select(RagContainerGrant).where(
                RagContainerGrant.user_id == user_id,
                RagContainerGrant.container_id == container_id,
            ),
        )
        rows = result.scalars().all()
        return any(action in row.actions for row in rows)
