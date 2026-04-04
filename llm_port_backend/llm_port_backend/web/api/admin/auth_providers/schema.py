"""Schemas for auth-provider management endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class AuthProviderDTO(BaseModel):
    """Auth provider — all fields except the raw secret."""

    id: uuid.UUID
    name: str
    provider_type: str
    client_id: str
    discovery_url: str | None = None
    authorize_url: str | None = None
    token_url: str | None = None
    userinfo_url: str | None = None
    scopes: str = "openid email profile"
    enabled: bool = True
    auto_register: bool = True
    default_role_ids: list[str] = Field(default_factory=list)
    group_mapping: dict = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime


class AuthProviderPublicDTO(BaseModel):
    """Minimal info exposed on the login page (no secrets)."""

    id: uuid.UUID
    name: str
    provider_type: str


class CreateAuthProviderRequest(BaseModel):
    """Create a new auth provider."""

    name: str = Field(..., min_length=1, max_length=128)
    provider_type: str = Field(..., pattern=r"^(oidc|oauth2)$")
    client_id: str = Field(..., min_length=1, max_length=512)
    client_secret: str = Field(..., min_length=1)
    discovery_url: str | None = None
    authorize_url: str | None = None
    token_url: str | None = None
    userinfo_url: str | None = None
    scopes: str = "openid email profile"
    enabled: bool = True
    auto_register: bool = True
    default_role_ids: list[str] = Field(default_factory=list)
    group_mapping: dict = Field(default_factory=dict)


class UpdateAuthProviderRequest(BaseModel):
    """Update an auth provider. All fields optional."""

    name: str | None = Field(default=None, min_length=1, max_length=128)
    provider_type: str | None = Field(default=None, pattern=r"^(oidc|oauth2)$")
    client_id: str | None = None
    client_secret: str | None = None
    discovery_url: str | None = None
    authorize_url: str | None = None
    token_url: str | None = None
    userinfo_url: str | None = None
    scopes: str | None = None
    enabled: bool | None = None
    auto_register: bool | None = None
    default_role_ids: list[str] | None = None
    group_mapping: dict | None = None
