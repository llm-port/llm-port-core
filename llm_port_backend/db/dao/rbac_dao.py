"""DAO for role-based access control."""

import uuid

from fastapi import Depends
from sqlalchemy import delete, select, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.dependencies import get_db_session
from llm_port_backend.db.models.rbac import (
    Permission,
    Role,
    RolePermission,
    UserRole,
)

# Default roles and their permission scopes
_DEFAULT_ROLES: dict[str, dict[str, list[str]]] = {
    "admin": {
        # admins get everything
        "llm.providers": ["create", "read", "update", "delete"],
        "llm.models": ["create", "read", "update", "delete", "download"],
        "llm.runtimes": ["create", "read", "update", "delete", "start", "stop", "restart"],
        "llm.jobs": ["read", "cancel", "create"],
        "llm.settings": ["read", "update"],
        "llm.graph": ["read"],
        "containers": [
            "create",
            "read",
            "update",
            "delete",
            "start",
            "stop",
            "restart",
            "pause",
            "exec",
            "logs",
        ],
        "images": ["read", "pull", "prune"],
        "networks": ["create", "read", "delete"],
        "stacks": ["read", "deploy", "update", "rollback"],
        "audit": ["read"],
        "logs": ["read"],
        "root_mode": ["start", "stop", "read"],
        "system.settings": ["read", "update"],
        "system.secrets": ["read_masked", "update"],
        "system.apply": ["execute", "read"],
        "system.wizard": ["read", "execute"],
        "system.agents": ["read", "manage"],
        "system.nodes": ["read", "manage"],
        "system.node_commands": ["read", "manage"],
        "system.node_maintenance": ["read", "manage"],
        "system.node_hostops": ["read", "manage"],
        "rag.runtime": ["read", "update"],
        "rag.containers": ["create", "read", "update", "delete"],
        "rag.assets": ["create", "read", "update", "delete"],
        "rag.publish": ["execute", "read"],
        "rag.jobs": ["read"],
        "rag.search": ["read", "write"],
        "modules": ["manage", "read"],
    },
    "operator": {
        "llm.providers": ["read"],
        "llm.models": ["read"],
        "llm.runtimes": ["read", "start", "stop", "restart"],
        "llm.jobs": ["read"],
        "llm.settings": ["read"],
        "llm.graph": ["read"],
        "containers": ["read", "start", "stop", "restart", "logs"],
        "images": ["read"],
        "networks": ["read"],
        "stacks": ["read"],
        "audit": ["read"],
        "logs": ["read"],
        "root_mode": ["read"],
        "system.settings": ["read"],
        "system.secrets": ["read_masked"],
        "system.apply": ["read"],
        "system.wizard": ["read"],
        "system.agents": ["read"],
        "system.nodes": ["read"],
        "system.node_commands": ["read"],
        "system.node_maintenance": ["read"],
        "system.node_hostops": [],
        "rag.runtime": ["read"],
        "rag.containers": ["read"],
        "rag.assets": ["read"],
        "rag.publish": ["execute", "read"],
        "rag.jobs": ["read"],
        "rag.search": ["read"],
        "modules": ["read"],
    },
    "viewer": {
        "llm.providers": ["read"],
        "llm.models": ["read"],
        "llm.runtimes": ["read"],
        "llm.jobs": ["read"],
        "llm.settings": [],
        "llm.graph": ["read"],
        "containers": ["read", "logs"],
        "images": ["read"],
        "networks": ["read"],
        "stacks": ["read"],
        "audit": ["read"],
        "logs": ["read"],
        "root_mode": ["read"],
        "system.settings": [],
        "system.secrets": [],
        "system.apply": ["read"],
        "system.wizard": [],
        "system.agents": [],
        "system.nodes": ["read"],
        "system.node_commands": ["read"],
        "system.node_maintenance": [],
        "system.node_hostops": [],
        "rag.runtime": ["read"],
        "rag.containers": ["read"],
        "rag.assets": ["read"],
        "rag.publish": ["read"],
        "rag.jobs": ["read"],
        "rag.search": ["read"],
    },
    "rag_manager": {
        "rag.runtime": ["read", "update"],
        "rag.containers": ["create", "read", "update", "delete"],
        "rag.assets": ["create", "read", "update", "delete"],
        "rag.publish": ["execute", "read"],
        "rag.jobs": ["read"],
        "rag.search": ["read", "write"],
    },
    "rag_editor": {
        "rag.runtime": ["read"],
        "rag.containers": ["create", "read", "update"],
        "rag.assets": ["create", "read", "update", "delete"],
        "rag.publish": ["execute", "read"],
        "rag.jobs": ["read"],
        "rag.search": ["read", "write"],
    },
    "rag_viewer": {
        "rag.runtime": ["read"],
        "rag.containers": ["read"],
        "rag.assets": ["read"],
        "rag.publish": ["read"],
        "rag.jobs": ["read"],
        "rag.search": ["read"],
    },
}


class RbacDAO:
    """Manages roles, permissions, and user-role assignments."""

    def __init__(self, session: AsyncSession = Depends(get_db_session)) -> None:
        self.session = session

    # ------------------------------------------------------------------
    # Role queries
    # ------------------------------------------------------------------

    async def get_role_by_name(self, name: str) -> Role | None:
        """Look up a role by its unique name."""
        result = await self.session.execute(
            select(Role).where(Role.name == name),
        )
        return result.scalar_one_or_none()

    async def list_roles(self) -> list[Role]:
        """Return all roles."""
        result = await self.session.execute(select(Role).order_by(Role.name))
        return list(result.scalars().all())

    async def get_role_by_id(self, role_id: uuid.UUID) -> Role | None:
        """Look up a role by ID."""
        result = await self.session.execute(select(Role).where(Role.id == role_id))
        return result.scalar_one_or_none()

    # ------------------------------------------------------------------
    # Role CRUD (custom roles only)
    # ------------------------------------------------------------------

    async def create_role(
        self,
        name: str,
        description: str | None,
        permission_ids: list[uuid.UUID],
    ) -> Role:
        """Create a custom (non-builtin) role with given permissions."""
        role = Role(
            id=uuid.uuid4(),
            name=name,
            description=description,
            is_builtin=False,
        )
        self.session.add(role)
        await self.session.flush()

        if permission_ids:
            await self.session.execute(
                pg_insert(RolePermission)
                .values([{"role_id": role.id, "permission_id": pid} for pid in permission_ids])
                .on_conflict_do_nothing(),
            )
            await self.session.flush()

        # Re-fetch to populate the selectin relationship
        result = await self.session.execute(select(Role).where(Role.id == role.id))
        return result.scalar_one()

    async def update_role(
        self,
        role_id: uuid.UUID,
        name: str | None,
        description: str | None,
        permission_ids: list[uuid.UUID] | None,
    ) -> Role | None:
        """Update a custom role. Returns None if not found."""
        role = await self.get_role_by_id(role_id)
        if role is None:
            return None
        if name is not None:
            role.name = name
        if description is not None:
            role.description = description
        if permission_ids is not None:
            await self.session.execute(
                delete(RolePermission).where(RolePermission.role_id == role_id),
            )
            if permission_ids:
                await self.session.execute(
                    pg_insert(RolePermission)
                    .values([{"role_id": role_id, "permission_id": pid} for pid in permission_ids])
                    .on_conflict_do_nothing(),
                )
        await self.session.flush()
        # Re-fetch to refresh permissions relationship
        result = await self.session.execute(select(Role).where(Role.id == role_id))
        return result.scalar_one()

    async def delete_role(self, role_id: uuid.UUID) -> bool:
        """Delete a custom role. Returns True if deleted."""
        role = await self.get_role_by_id(role_id)
        if role is None:
            return False
        # Remove all user-role and role-permission links
        await self.session.execute(delete(UserRole).where(UserRole.role_id == role_id))
        await self.session.execute(delete(RolePermission).where(RolePermission.role_id == role_id))
        await self.session.delete(role)
        await self.session.flush()
        return True

    async def count_role_users(self, role_id: uuid.UUID) -> int:
        """Return the number of users assigned to a role."""
        from sqlalchemy import func as sa_func  # noqa: PLC0415

        result = await self.session.execute(
            select(sa_func.count()).select_from(UserRole).where(UserRole.role_id == role_id),
        )
        return result.scalar_one()

    # ------------------------------------------------------------------
    # Permission queries
    # ------------------------------------------------------------------

    async def list_permissions(self) -> list[Permission]:
        """Return all known permissions, ordered by resource then action."""
        result = await self.session.execute(
            select(Permission).order_by(Permission.resource, Permission.action),
        )
        return list(result.scalars().all())

    # ------------------------------------------------------------------
    # User-role assignments
    # ------------------------------------------------------------------

    async def get_user_roles(self, user_id: uuid.UUID) -> list[Role]:
        """Return all roles assigned to a user."""
        result = await self.session.execute(
            select(Role).join(UserRole, UserRole.role_id == Role.id).where(UserRole.user_id == user_id),
        )
        return list(result.scalars().all())

    async def assign_role(self, user_id: uuid.UUID, role_id: uuid.UUID) -> None:
        """Assign a role to a user (idempotent)."""
        await self.session.execute(
            pg_insert(UserRole)
            .values(user_id=user_id, role_id=role_id)
            .on_conflict_do_nothing(
                index_elements=[UserRole.user_id, UserRole.role_id],
            ),
        )

    async def remove_role(self, user_id: uuid.UUID, role_id: uuid.UUID) -> None:
        """Remove a role from a user."""
        await self.session.execute(
            delete(UserRole).where(
                UserRole.user_id == user_id,
                UserRole.role_id == role_id,
            ),
        )

    async def set_user_roles(self, user_id: uuid.UUID, role_ids: list[uuid.UUID]) -> None:
        """Replace all role assignments for a user."""
        unique_role_ids = list(dict.fromkeys(role_ids))
        await self.session.execute(delete(UserRole).where(UserRole.user_id == user_id))
        for role_id in unique_role_ids:
            self.session.add(UserRole(user_id=user_id, role_id=role_id))

    # ------------------------------------------------------------------
    # Permission checks
    # ------------------------------------------------------------------

    async def get_user_permissions(self, user_id: uuid.UUID) -> list[Permission]:
        """Return the union of all permissions from the user's direct roles AND group-inherited roles."""
        from llm_port_backend.db.models.groups import GroupRole, UserGroup  # noqa: PLC0415

        # Direct role permissions
        direct = (
            select(Permission.id)
            .join(
                RolePermission,
                RolePermission.permission_id == Permission.id,
            )
            .join(
                UserRole,
                UserRole.role_id == RolePermission.role_id,
            )
            .where(UserRole.user_id == user_id)
        )

        # Group-inherited role permissions
        group = (
            select(Permission.id)
            .join(
                RolePermission,
                RolePermission.permission_id == Permission.id,
            )
            .join(
                GroupRole,
                GroupRole.role_id == RolePermission.role_id,
            )
            .join(
                UserGroup,
                UserGroup.group_id == GroupRole.group_id,
            )
            .where(UserGroup.user_id == user_id)
        )

        combined_ids = direct.union(group).subquery()

        result = await self.session.execute(
            select(Permission).where(Permission.id.in_(select(combined_ids.c.id))),
        )
        return list(result.scalars().all())

    async def has_permission(
        self,
        user_id: uuid.UUID,
        resource: str,
        action: str,
    ) -> bool:
        """Check whether any of the user's direct or group-inherited roles grants (resource, action)."""
        from llm_port_backend.db.models.groups import GroupRole, UserGroup  # noqa: PLC0415

        # Direct assignment check
        direct = await self.session.execute(
            select(Permission.id)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .join(UserRole, UserRole.role_id == RolePermission.role_id)
            .where(
                UserRole.user_id == user_id,
                Permission.resource == resource,
                Permission.action == action,
            )
            .limit(1),
        )
        if direct.scalar_one_or_none() is not None:
            return True

        # Group-inherited check
        group = await self.session.execute(
            select(Permission.id)
            .join(RolePermission, RolePermission.permission_id == Permission.id)
            .join(GroupRole, GroupRole.role_id == RolePermission.role_id)
            .join(UserGroup, UserGroup.group_id == GroupRole.group_id)
            .where(
                UserGroup.user_id == user_id,
                Permission.resource == resource,
                Permission.action == action,
            )
            .limit(1),
        )
        return group.scalar_one_or_none() is not None

    # ------------------------------------------------------------------
    # Seeding
    # ------------------------------------------------------------------

    async def seed_defaults(self) -> None:
        """Create built-in roles and permissions if they don't exist."""
        # 1) Upsert all known permissions.
        permission_pairs = sorted(
            {
                (resource, action)
                for perms in _DEFAULT_ROLES.values()
                for resource, actions in perms.items()
                for action in actions
            },
        )
        if permission_pairs:
            await self.session.execute(
                pg_insert(Permission)
                .values(
                    [
                        {
                            "id": uuid.uuid4(),
                            "resource": resource,
                            "action": action,
                        }
                        for resource, action in permission_pairs
                    ],
                )
                .on_conflict_do_nothing(
                    index_elements=[Permission.resource, Permission.action],
                ),
            )

        # Resolve DB IDs for all permissions after upsert.
        permission_rows = await self.session.execute(
            select(Permission).where(
                tuple_(Permission.resource, Permission.action).in_(permission_pairs),
            ),
        )
        perm_map = {(perm.resource, perm.action): perm for perm in permission_rows.scalars().all()}

        # 2) Upsert all built-in roles.
        role_names = sorted(_DEFAULT_ROLES.keys())
        await self.session.execute(
            pg_insert(Role)
            .values(
                [
                    {
                        "id": uuid.uuid4(),
                        "name": role_name,
                        "description": f"Built-in {role_name} role",
                        "is_builtin": True,
                    }
                    for role_name in role_names
                ],
            )
            .on_conflict_do_nothing(index_elements=[Role.name]),
        )

        # Ensure existing built-in roles have is_builtin set to True.
        from sqlalchemy import update  # noqa: PLC0415

        await self.session.execute(
            update(Role).where(Role.name.in_(role_names)).values(is_builtin=True),
        )

        # Resolve DB IDs for all roles after upsert.
        role_rows = await self.session.execute(
            select(Role).where(Role.name.in_(role_names)),
        )
        role_map = {role.name: role for role in role_rows.scalars().all()}

        # 3) Upsert all role->permission links.
        role_permission_rows = []
        for role_name, perms in _DEFAULT_ROLES.items():
            role = role_map.get(role_name)
            if role is None:
                continue
            for resource, actions in perms.items():
                for action in actions:
                    perm = perm_map.get((resource, action))
                    if perm is None:
                        continue
                    role_permission_rows.append(
                        {
                            "role_id": role.id,
                            "permission_id": perm.id,
                        },
                    )

        if role_permission_rows:
            await self.session.execute(
                pg_insert(RolePermission)
                .values(role_permission_rows)
                .on_conflict_do_nothing(
                    index_elements=[RolePermission.role_id, RolePermission.permission_id],
                ),
            )
