"""DAO for stack revisions."""

import uuid

from fastapi import Depends
from sqlalchemy import distinct, select
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.dependencies import get_db_session
from llm_port_backend.db.models.containers import StackRevision


class StackRevisionDAO:
    """Manages compose stack revisions."""

    def __init__(self, session: AsyncSession = Depends(get_db_session)) -> None:
        self.session = session

    async def next_rev(self, stack_id: str) -> int:
        """Return the next revision number for the given stack."""
        result = await self.session.execute(
            select(StackRevision.rev)
            .where(StackRevision.stack_id == stack_id)
            .order_by(StackRevision.rev.desc())
            .limit(1)
        )
        current = result.scalar_one_or_none()
        return (current or 0) + 1

    async def create(
        self,
        stack_id: str,
        compose_yaml: str,
        env_blob: str | None = None,
        image_digests: str | None = None,
        created_by: uuid.UUID | None = None,
    ) -> StackRevision:
        """Append a new revision for a stack."""
        rev = await self.next_rev(stack_id)
        record = StackRevision(
            id=uuid.uuid4(),
            stack_id=stack_id,
            rev=rev,
            compose_yaml=compose_yaml,
            env_blob=env_blob,
            image_digests=image_digests,
            created_by=created_by,
        )
        self.session.add(record)
        return record

    async def list_revisions(self, stack_id: str) -> list[StackRevision]:
        """Return all revisions for a stack ordered by rev descending."""
        result = await self.session.execute(
            select(StackRevision).where(StackRevision.stack_id == stack_id).order_by(StackRevision.rev.desc())
        )
        return list(result.scalars().all())

    async def get_revision(self, stack_id: str, rev: int) -> StackRevision | None:
        """Fetch a specific revision."""
        result = await self.session.execute(
            select(StackRevision).where(
                StackRevision.stack_id == stack_id,
                StackRevision.rev == rev,
            )
        )
        return result.scalar_one_or_none()

    async def latest(self, stack_id: str) -> StackRevision | None:
        """Fetch the latest revision for a stack."""
        result = await self.session.execute(
            select(StackRevision)
            .where(StackRevision.stack_id == stack_id)
            .order_by(StackRevision.rev.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def list_stacks(self) -> list[str]:
        """Return all distinct stack IDs."""
        result = await self.session.execute(select(distinct(StackRevision.stack_id)))
        return list(result.scalars().all())
