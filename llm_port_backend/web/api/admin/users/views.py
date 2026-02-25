"""Admin user-management and RBAC endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from llm_port_backend.db.dao.rbac_dao import RbacDAO
from llm_port_backend.db.dependencies import get_db_session
from llm_port_backend.db.models.rbac import Permission, Role
from llm_port_backend.db.models.users import User, current_active_user
from llm_port_backend.web.api.admin.dependencies import require_superuser
from llm_port_backend.web.api.admin.users.schema import (
    AdminUserDTO,
    MeAccessDTO,
    PermissionDTO,
    RoleDTO,
    UpdateUserRolesRequest,
)

router = APIRouter()


def _role_to_dto(role: Role) -> RoleDTO:
    permissions = sorted(role.permissions, key=lambda p: (p.resource, p.action))
    return RoleDTO(
        id=role.id,
        name=role.name,
        description=role.description,
        created_at=role.created_at,
        permissions=[
            PermissionDTO(id=perm.id, resource=perm.resource, action=perm.action)
            for perm in permissions
        ],
    )


def _permissions_to_dto(permissions: list[Permission]) -> list[PermissionDTO]:
    return [
        PermissionDTO(id=perm.id, resource=perm.resource, action=perm.action)
        for perm in sorted(permissions, key=lambda p: (p.resource, p.action))
    ]


async def _build_admin_user_dto(user: User, rbac_dao: RbacDAO) -> AdminUserDTO:
    roles = await rbac_dao.get_user_roles(user.id)
    permissions = await rbac_dao.get_user_permissions(user.id)
    return AdminUserDTO(
        id=user.id,
        email=user.email,
        is_active=user.is_active,
        is_superuser=user.is_superuser,
        is_verified=user.is_verified,
        roles=[_role_to_dto(role) for role in sorted(roles, key=lambda r: r.name)],
        permissions=_permissions_to_dto(permissions),
    )


@router.get("/me/access", response_model=MeAccessDTO, name="admin_me_access")
async def me_access(
    user: Annotated[User, Depends(current_active_user)],
    rbac_dao: RbacDAO = Depends(),
) -> MeAccessDTO:
    """Return effective roles and permissions for the current user."""
    roles = await rbac_dao.get_user_roles(user.id)
    permissions = await rbac_dao.get_user_permissions(user.id)
    return MeAccessDTO(
        id=user.id,
        email=user.email,
        is_superuser=user.is_superuser,
        roles=[_role_to_dto(role) for role in sorted(roles, key=lambda r: r.name)],
        permissions=_permissions_to_dto(permissions),
    )


@router.get("/roles", response_model=list[RoleDTO], name="list_roles")
async def list_roles(
    _user: Annotated[User, Depends(require_superuser)] = None,  # type: ignore[assignment]
    rbac_dao: RbacDAO = Depends(),
) -> list[RoleDTO]:
    """List all roles and their permissions."""
    roles = await rbac_dao.list_roles()
    return [_role_to_dto(role) for role in sorted(roles, key=lambda r: r.name)]


@router.get("/", response_model=list[AdminUserDTO], name="list_users_with_roles")
async def list_users_with_roles(
    _user: Annotated[User, Depends(require_superuser)] = None,  # type: ignore[assignment]
    session: AsyncSession = Depends(get_db_session),
) -> list[AdminUserDTO]:
    """List users with assigned roles and effective permissions."""
    rbac_dao = RbacDAO(session)
    result = await session.execute(select(User).order_by(User.email.asc()))
    users = list(result.scalars().all())
    return [await _build_admin_user_dto(user, rbac_dao) for user in users]


@router.put("/{user_id}/roles", response_model=AdminUserDTO, name="set_user_roles")
async def set_user_roles(
    user_id: uuid.UUID,
    payload: UpdateUserRolesRequest,
    _user: Annotated[User, Depends(require_superuser)] = None,  # type: ignore[assignment]
    session: AsyncSession = Depends(get_db_session),
) -> AdminUserDTO:
    """Replace role assignments for a user."""
    rbac_dao = RbacDAO(session)

    user_result = await session.execute(select(User).where(User.id == user_id))
    target_user = user_result.scalar_one_or_none()
    if target_user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    existing_roles = await rbac_dao.list_roles()
    existing_role_ids = {role.id for role in existing_roles}
    for role_id in payload.role_ids:
        if role_id not in existing_role_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown role id: {role_id}",
            )

    await rbac_dao.set_user_roles(user_id, payload.role_ids)
    await session.flush()

    return await _build_admin_user_dto(target_user, rbac_dao)
