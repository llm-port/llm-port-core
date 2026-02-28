"""Schemas for group management endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from llm_port_backend.web.api.admin.users.schema import RoleDTO


class GroupDTO(BaseModel):
    """Group with its roles and member count."""

    id: uuid.UUID
    name: str
    description: str | None = None
    created_at: datetime
    roles: list[RoleDTO] = Field(default_factory=list)
    member_count: int = 0


class GroupMemberDTO(BaseModel):
    """Minimal user info for group membership listing."""

    id: uuid.UUID
    email: str


class CreateGroupRequest(BaseModel):
    """Create a new group."""

    name: str = Field(..., min_length=1, max_length=128)
    description: str | None = None
    role_ids: list[uuid.UUID] = Field(default_factory=list)


class UpdateGroupRequest(BaseModel):
    """Update a group."""

    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = None
    role_ids: list[uuid.UUID] | None = None


class UpdateGroupMembersRequest(BaseModel):
    """Replace or add members to a group."""

    user_ids: list[uuid.UUID] = Field(default_factory=list)
