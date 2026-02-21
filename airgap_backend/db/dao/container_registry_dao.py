"""DAO for the container registry."""

import uuid

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from airgap_backend.db.dependencies import get_db_session
from airgap_backend.db.models.containers import (
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
        """Insert or update a container record."""
        existing = await self.get(container_id)
        if existing:
            existing.name = name
            existing.container_class = container_class
            existing.owner_scope = owner_scope
            existing.policy = policy
            existing.engine_id = engine_id
            return existing
        record = ContainerRegistry(
            container_id=container_id,
            name=name,
            container_class=container_class,
            owner_scope=owner_scope,
            policy=policy,
            engine_id=engine_id,
            created_by=created_by,
        )
        self.session.add(record)
        return record

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
