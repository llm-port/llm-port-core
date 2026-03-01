"""DAO for group management."""

import uuid
from collections.abc import Sequence

from fastapi import Depends
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.dependencies import get_db_session
from llm_port_backend.db.models.groups import Group, GroupRole, UserGroup
from llm_port_backend.db.models.rbac import Role


class GroupDAO:
    """Manages groups, group-role assignments, and user-group memberships."""

    def __init__(self, session: AsyncSession = Depends(get_db_session)) -> None:
        self.session = session

    # ------------------------------------------------------------------
    # Group CRUD
    # ------------------------------------------------------------------

    async def list_groups(self) -> list[Group]:
        """Return all groups ordered by name."""
        result = await self.session.execute(select(Group).order_by(Group.name))
        return list(result.scalars().all())

    async def get_group_by_id(self, group_id: uuid.UUID) -> Group | None:
        """Look up a group by ID."""
        result = await self.session.execute(
            select(Group).where(Group.id == group_id),
        )
        return result.scalar_one_or_none()

    async def get_group_by_name(self, name: str) -> Group | None:
        """Look up a group by its unique name."""
        result = await self.session.execute(
            select(Group).where(Group.name == name),
        )
        return result.scalar_one_or_none()

    async def create_group(
        self,
        name: str,
        description: str | None,
        role_ids: list[uuid.UUID],
    ) -> Group:
        """Create a group with the given roles."""
        group = Group(
            id=uuid.uuid4(),
            name=name,
            description=description,
        )
        self.session.add(group)
        await self.session.flush()

        if role_ids:
            await self.session.execute(
                pg_insert(GroupRole)
                .values(
                    [{"group_id": group.id, "role_id": rid} for rid in role_ids],
                )
                .on_conflict_do_nothing(),
            )
            await self.session.flush()

        # Re-fetch to populate the selectin relationship
        result = await self.session.execute(
            select(Group).where(Group.id == group.id),
        )
        return result.scalar_one()

    async def update_group(
        self,
        group_id: uuid.UUID,
        name: str | None,
        description: str | None,
        role_ids: list[uuid.UUID] | None,
    ) -> Group | None:
        """Update a group. Returns None if not found."""
        group = await self.get_group_by_id(group_id)
        if group is None:
            return None
        if name is not None:
            group.name = name
        if description is not None:
            group.description = description
        if role_ids is not None:
            await self.session.execute(
                delete(GroupRole).where(GroupRole.group_id == group_id),
            )
            if role_ids:
                await self.session.execute(
                    pg_insert(GroupRole)
                    .values(
                        [{"group_id": group_id, "role_id": rid} for rid in role_ids],
                    )
                    .on_conflict_do_nothing(),
                )
        await self.session.flush()
        # Re-fetch to refresh relationships
        result = await self.session.execute(
            select(Group).where(Group.id == group_id),
        )
        return result.scalar_one()

    async def delete_group(self, group_id: uuid.UUID) -> bool:
        """Delete a group. Returns True if deleted."""
        group = await self.get_group_by_id(group_id)
        if group is None:
            return False
        await self.session.execute(
            delete(UserGroup).where(UserGroup.group_id == group_id),
        )
        await self.session.execute(
            delete(GroupRole).where(GroupRole.group_id == group_id),
        )
        await self.session.delete(group)
        await self.session.flush()
        return True

    # ------------------------------------------------------------------
    # Group member count
    # ------------------------------------------------------------------

    async def count_group_members(self, group_id: uuid.UUID) -> int:
        """Return the number of users in a group."""
        from sqlalchemy import func as sa_func  # noqa: PLC0415

        result = await self.session.execute(
            select(sa_func.count()).select_from(UserGroup).where(UserGroup.group_id == group_id),
        )
        return result.scalar_one()

    # ------------------------------------------------------------------
    # User-group membership
    # ------------------------------------------------------------------

    async def get_user_groups(self, user_id: uuid.UUID) -> list[Group]:
        """Return all groups a user belongs to."""
        result = await self.session.execute(
            select(Group).join(UserGroup, UserGroup.group_id == Group.id).where(UserGroup.user_id == user_id),
        )
        return list(result.scalars().all())

    async def get_group_members(self, group_id: uuid.UUID) -> Sequence[uuid.UUID]:
        """Return user IDs that belong to a group."""
        result = await self.session.execute(
            select(UserGroup.user_id).where(UserGroup.group_id == group_id),
        )
        return list(result.scalars().all())

    async def add_members(
        self,
        group_id: uuid.UUID,
        user_ids: list[uuid.UUID],
    ) -> None:
        """Add users to a group (idempotent)."""
        if not user_ids:
            return
        await self.session.execute(
            pg_insert(UserGroup)
            .values(
                [{"group_id": group_id, "user_id": uid} for uid in user_ids],
            )
            .on_conflict_do_nothing(),
        )
        await self.session.flush()

    async def remove_member(
        self,
        group_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> None:
        """Remove a user from a group."""
        await self.session.execute(
            delete(UserGroup).where(
                UserGroup.group_id == group_id,
                UserGroup.user_id == user_id,
            ),
        )
        await self.session.flush()

    async def set_group_members(
        self,
        group_id: uuid.UUID,
        user_ids: list[uuid.UUID],
    ) -> None:
        """Replace all members of a group."""
        await self.session.execute(
            delete(UserGroup).where(UserGroup.group_id == group_id),
        )
        if user_ids:
            unique_user_ids = list(dict.fromkeys(user_ids))
            await self.session.execute(
                pg_insert(UserGroup)
                .values(
                    [{"group_id": group_id, "user_id": uid} for uid in unique_user_ids],
                )
                .on_conflict_do_nothing(),
            )
        await self.session.flush()

    # ------------------------------------------------------------------
    # Group-inherited roles for a user
    # ------------------------------------------------------------------

    async def get_user_group_roles(self, user_id: uuid.UUID) -> list[Role]:
        """Return all roles inherited through groups for a user."""
        result = await self.session.execute(
            select(Role)
            .join(GroupRole, GroupRole.role_id == Role.id)
            .join(UserGroup, UserGroup.group_id == GroupRole.group_id)
            .where(UserGroup.user_id == user_id)
            .distinct(),
        )
        return list(result.scalars().all())
