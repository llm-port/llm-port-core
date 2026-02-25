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
    created_at: datetime
    permissions: list[PermissionDTO] = Field(default_factory=list)


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
