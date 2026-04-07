"""Admin user-management and RBAC endpoints."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from llm_port_backend.db.dao.rbac_dao import RbacDAO
from llm_port_backend.db.dependencies import get_db_session
from llm_port_backend.db.models.rbac import Permission, Role
from llm_port_backend.db.models.users import User, current_active_user
from llm_port_backend.web.api.admin.dependencies import require_superuser
from llm_port_backend.web.api.admin.users.preferences import (
    router as preferences_router,
)
from llm_port_backend.web.api.admin.users.schema import (
    AdminUserDTO,
    ApiTokenResponse,
    ChangePasswordRequest,
    CreateRoleRequest,
    CreateUserRequest,
    GenerateApiTokenRequest,
    MeAccessDTO,
    PermissionDTO,
    RoleDTO,
    UpdateRoleRequest,
    UpdateUserRolesRequest,
)

router = APIRouter()
router.include_router(preferences_router)


def _role_to_dto(role: Role, user_count: int = 0) -> RoleDTO:
    permissions = sorted(role.permissions, key=lambda p: (p.resource, p.action))
    return RoleDTO(
        id=role.id,
        name=role.name,
        description=role.description,
        is_builtin=role.is_builtin,
        created_at=role.created_at,
        permissions=[
            PermissionDTO(id=perm.id, resource=perm.resource, action=perm.action) for perm in permissions
        ],
        user_count=user_count,
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


async def _resolve_users_secret(session: AsyncSession) -> str:
    """Resolve backend JWT signing secret with DB fallback."""
    from llm_port_backend.services.system_settings.crypto import SettingsCrypto  # noqa: PLC0415
    from llm_port_backend.settings import settings as backend_settings  # noqa: PLC0415

    secret = backend_settings.users_secret.strip()
    if secret:
        return secret
    if not backend_settings.settings_master_key:
        return ""

    row = await session.execute(
        text("SELECT ciphertext FROM system_setting_secret WHERE key = :k"),
        {"k": "llm_port_backend.users_secret"},
    )
    ciphertext = row.scalar_one_or_none()
    if not ciphertext:
        return ""
    try:
        secret = SettingsCrypto(backend_settings.settings_master_key).decrypt(ciphertext).strip()
    except Exception:  # noqa: BLE001
        return ""
    if secret:
        object.__setattr__(backend_settings, "users_secret", secret)
    return secret


# ── Current user access ──────────────────────────────────────────────


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


# ── Roles CRUD ────────────────────────────────────────────────────────


@router.get("/roles", response_model=list[RoleDTO], name="list_roles")
async def list_roles(
    _user: Annotated[User, Depends(require_superuser)] = None,  # type: ignore[assignment]
    rbac_dao: RbacDAO = Depends(),
) -> list[RoleDTO]:
    """List all roles and their permissions."""
    roles = await rbac_dao.list_roles()
    result = []
    for role in roles:
        count = await rbac_dao.count_role_users(role.id)
        result.append(_role_to_dto(role, user_count=count))
    return result


@router.get("/roles/{role_id}", response_model=RoleDTO, name="get_role")
async def get_role(
    role_id: uuid.UUID,
    _user: Annotated[User, Depends(require_superuser)] = None,  # type: ignore[assignment]
    rbac_dao: RbacDAO = Depends(),
) -> RoleDTO:
    """Get a single role by ID."""
    role = await rbac_dao.get_role_by_id(role_id)
    if role is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")
    count = await rbac_dao.count_role_users(role.id)
    return _role_to_dto(role, user_count=count)


@router.post("/roles", response_model=RoleDTO, status_code=status.HTTP_201_CREATED, name="create_role")
async def create_role(
    payload: CreateRoleRequest,
    _user: Annotated[User, Depends(require_superuser)] = None,  # type: ignore[assignment]
    rbac_dao: RbacDAO = Depends(),
) -> RoleDTO:
    """Create a new custom role."""
    existing = await rbac_dao.get_role_by_name(payload.name)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Role '{payload.name}' already exists",
        )
    role = await rbac_dao.create_role(payload.name, payload.description, payload.permission_ids)
    return _role_to_dto(role)


@router.patch("/roles/{role_id}", response_model=RoleDTO, name="update_role")
async def update_role(
    role_id: uuid.UUID,
    payload: UpdateRoleRequest,
    _user: Annotated[User, Depends(require_superuser)] = None,  # type: ignore[assignment]
    rbac_dao: RbacDAO = Depends(),
) -> RoleDTO:
    """Update a custom role. Built-in roles cannot be modified."""
    existing = await rbac_dao.get_role_by_id(role_id)
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")
    if existing.is_builtin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Built-in roles cannot be modified",
        )
    role = await rbac_dao.update_role(
        role_id,
        name=payload.name,
        description=payload.description,
        permission_ids=payload.permission_ids,
    )
    count = await rbac_dao.count_role_users(role_id)
    return _role_to_dto(role, user_count=count)  # type: ignore[arg-type]


@router.delete("/roles/{role_id}", status_code=status.HTTP_204_NO_CONTENT, name="delete_role")
async def delete_role(
    role_id: uuid.UUID,
    _user: Annotated[User, Depends(require_superuser)] = None,  # type: ignore[assignment]
    rbac_dao: RbacDAO = Depends(),
) -> None:
    """Delete a custom role. Built-in roles cannot be deleted."""
    existing = await rbac_dao.get_role_by_id(role_id)
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")
    if existing.is_builtin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Built-in roles cannot be deleted",
        )
    await rbac_dao.delete_role(role_id)


# ── Permissions ───────────────────────────────────────────────────────


@router.get("/permissions", response_model=list[PermissionDTO], name="list_permissions")
async def list_permissions(
    _user: Annotated[User, Depends(require_superuser)] = None,  # type: ignore[assignment]
    rbac_dao: RbacDAO = Depends(),
) -> list[PermissionDTO]:
    """List all available permissions."""
    permissions = await rbac_dao.list_permissions()
    return _permissions_to_dto(permissions)


# ── Users ─────────────────────────────────────────────────────────────


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


@router.post("/", response_model=AdminUserDTO, status_code=status.HTTP_201_CREATED, name="create_user")
async def create_user(
    payload: CreateUserRequest,
    _user: Annotated[User, Depends(require_superuser)] = None,  # type: ignore[assignment]
    session: AsyncSession = Depends(get_db_session),
) -> AdminUserDTO:
    """Admin-initiated user creation."""
    from fastapi_users.password import PasswordHelper  # noqa: PLC0415

    # Check if email already exists
    existing = await session.execute(select(User).where(User.email == payload.email))  # type: ignore[arg-type]
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"User with email '{payload.email}' already exists",
        )

    password_helper = PasswordHelper()
    new_user = User(
        id=uuid.uuid4(),
        email=payload.email,
        hashed_password=password_helper.hash(payload.password),
        is_active=True,
        is_superuser=payload.is_superuser,
        is_verified=True,
    )
    session.add(new_user)
    await session.flush()

    rbac_dao = RbacDAO(session)
    if payload.role_ids:
        await rbac_dao.set_user_roles(new_user.id, payload.role_ids)
        await session.flush()
    elif not payload.is_superuser:
        default_role = await rbac_dao.get_role_by_name("default_user")
        if default_role is not None:
            await rbac_dao.assign_role(new_user.id, default_role.id)
            await session.flush()

    return await _build_admin_user_dto(new_user, rbac_dao)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT, name="delete_user")
async def delete_user(
    user_id: uuid.UUID,
    _user: Annotated[User, Depends(require_superuser)] = None,  # type: ignore[assignment]
    session: AsyncSession = Depends(get_db_session),
) -> None:
    """Delete a user by ID."""
    result = await session.execute(select(User).where(User.id == user_id))
    target = result.scalar_one_or_none()
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    await session.delete(target)
    await session.flush()


# ── Self-service profile endpoints ────────────────────────────────────


@router.post("/me/change-password", status_code=status.HTTP_204_NO_CONTENT, name="change_password")
async def change_password(
    payload: ChangePasswordRequest,
    user: Annotated[User, Depends(current_active_user)],
    session: AsyncSession = Depends(get_db_session),
) -> None:
    """Change the current user's password."""
    from fastapi_users.password import PasswordHelper  # noqa: PLC0415

    password_helper = PasswordHelper()
    verified, _ = password_helper.verify_and_update(payload.current_password, user.hashed_password)
    if not verified:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Current password is incorrect.",
        )
    user.hashed_password = password_helper.hash(payload.new_password)
    session.add(user)
    await session.flush()


@router.post("/me/api-token", response_model=ApiTokenResponse, name="generate_api_token")
async def generate_api_token(
    payload: GenerateApiTokenRequest,
    user: Annotated[User, Depends(current_active_user)],
    session: AsyncSession = Depends(get_db_session),
) -> ApiTokenResponse:
    """Generate a JWT token for the LLM API gateway.

    Uses ``settings.users_secret`` which is loaded from the
    ``llm_port_backend.users_secret`` system setting at startup.
    Both backend and llm_port_api share the same DB-stored secret.
    """
    import time  # noqa: PLC0415

    import jwt as pyjwt  # noqa: PLC0415

    secret = await _resolve_users_secret(session)
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="JWT secret is not configured. Set 'Backend Users JWT Secret' in System Settings.",
        )

    now = int(time.time())
    claims: dict = {
        "sub": str(user.id),
        "tenant_id": payload.tenant_id,
        "email": user.email,
        "iat": now,
        # Prevent deterministic duplicate tokens if generated in the same second.
        "jti": uuid.uuid4().hex,
    }
    if payload.expires_in is not None:
        claims["exp"] = now + payload.expires_in

    token = pyjwt.encode(claims, secret, algorithm="HS256")
    return ApiTokenResponse(token=token, expires_in=payload.expires_in)
