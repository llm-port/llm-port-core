"""Schemas for admin user and RBAC management endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class PermissionDTO(BaseModel):
    """A single resource/action permission."""

    id: uuid.UUID
    resource: str
    action: str


class RoleDTO(BaseModel):
    """Role with its attached permissions."""

    id: uuid.UUID
    name: str
    description: str | None = None
    is_builtin: bool = False
    created_at: datetime
    permissions: list[PermissionDTO] = Field(default_factory=list)
    user_count: int = 0


class CreateRoleRequest(BaseModel):
    """Create a new custom role."""

    name: str = Field(..., min_length=1, max_length=64)
    description: str | None = None
    permission_ids: list[uuid.UUID] = Field(default_factory=list)


class UpdateRoleRequest(BaseModel):
    """Update a custom role."""

    name: str | None = Field(default=None, min_length=1, max_length=64)
    description: str | None = None
    permission_ids: list[uuid.UUID] | None = None


class AdminUserDTO(BaseModel):
    """User profile including roles and effective permissions."""

    id: uuid.UUID
    email: str
    is_active: bool
    is_superuser: bool
    is_verified: bool
    roles: list[RoleDTO] = Field(default_factory=list)
    permissions: list[PermissionDTO] = Field(default_factory=list)


class MeAccessDTO(BaseModel):
    """Current-user access snapshot for UI gating."""

    id: uuid.UUID
    email: str
    is_superuser: bool
    roles: list[RoleDTO] = Field(default_factory=list)
    permissions: list[PermissionDTO] = Field(default_factory=list)


class UpdateUserRolesRequest(BaseModel):
    """Replace the set of assigned roles for a user."""

    role_ids: list[uuid.UUID] = Field(default_factory=list)


class ChangePasswordRequest(BaseModel):
    """Self-service password change."""

    current_password: str = Field(..., min_length=1)
    new_password: str = Field(..., min_length=6)


class GenerateApiTokenRequest(BaseModel):
    """Request to generate an API gateway JWT token."""

    tenant_id: str = Field(default="default", min_length=1, max_length=128)
    expires_in: int | None = Field(
        default=None,
        description="Token lifetime in seconds. None = no expiry.",
    )


class ApiTokenResponse(BaseModel):
    """Response containing the generated API token."""

    token: str
    expires_in: int | None = None


class CreateUserRequest(BaseModel):
    """Admin-initiated user creation."""

    email: str
    password: str = Field(..., min_length=6)
    is_superuser: bool = False
    role_ids: list[uuid.UUID] = Field(default_factory=list)


# ── User preferences ─────────────────────────────────────────────────


class UserPreferencesRead(BaseModel):
    """Current user's preferences blob."""

    preferences: dict = Field(default_factory=dict)


class UserPreferencesUpdate(BaseModel):
    """Partial merge-update for user preferences."""

    preferences: dict = Field(..., description="Keys to merge into existing preferences.")
