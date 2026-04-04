"""DAO for the container registry."""

import uuid

from fastapi import Depends
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.dependencies import get_db_session
from llm_port_backend.db.models.containers import (
    ContainerClass,
    ContainerPolicy,
    ContainerRegistry,
)


class ContainerRegistryDAO:
    """Manages the server-side container registry."""

    def __init__(self, session: AsyncSession = Depends(get_db_session)) -> None:
        self.session = session

    async def upsert(
        self,
        container_id: str,
        name: str,
        container_class: ContainerClass = ContainerClass.UNTRUSTED,
        owner_scope: str = "platform",
        policy: ContainerPolicy = ContainerPolicy.FREE,
        engine_id: str = "local",
        created_by: uuid.UUID | None = None,
    ) -> ContainerRegistry:
        """Insert or update a container record (atomic).

        Uses a raw ``INSERT … ON CONFLICT DO UPDATE`` to avoid the
        TOCTOU race inherent in SELECT-then-INSERT *and* the
        ``MissingGreenlet`` issue that ``pg_insert()`` triggers with
        asyncpg when enum columns are involved.
        """
        await self.session.execute(
            text(
                "INSERT INTO container_registry"
                " (container_id, name, container_class, owner_scope,"
                "  policy, engine_id, created_by)"
                " VALUES (:cid, :name, CAST(:cls AS container_class),"
                "         :scope, CAST(:pol AS container_policy), :eng, :by)"
                " ON CONFLICT (container_id) DO UPDATE SET"
                "   name = EXCLUDED.name,"
                "   container_class = EXCLUDED.container_class,"
                "   owner_scope = EXCLUDED.owner_scope,"
                "   policy = EXCLUDED.policy,"
                "   engine_id = EXCLUDED.engine_id"
            ),
            {
                "cid": container_id,
                "name": name,
                "cls": container_class.value,
                "scope": owner_scope,
                "pol": policy.value,
                "eng": engine_id,
                "by": created_by,
            },
        )
        return await self.get(container_id)  # type: ignore[return-value]

    async def get(self, container_id: str) -> ContainerRegistry | None:
        """Fetch a registry entry by container ID."""
        result = await self.session.execute(
            select(ContainerRegistry).where(ContainerRegistry.container_id == container_id)
        )
        return result.scalar_one_or_none()

    async def list_all(self) -> list[ContainerRegistry]:
        """Return all registry entries."""
        result = await self.session.execute(select(ContainerRegistry))
        return list(result.scalars().all())

    async def delete(self, container_id: str) -> None:
        """Remove a registry entry."""
        record = await self.get(container_id)
        if record:
            await self.session.delete(record)
