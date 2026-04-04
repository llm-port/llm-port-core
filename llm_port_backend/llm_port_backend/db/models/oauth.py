"""OAuth and auth-provider models."""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from llm_port_backend.db.base import Base


class OAuthAccount(Base):
    """Stores OAuth tokens linked to a user.

    Compatible with fastapi-users ``SQLAlchemyBaseOAuthAccountTableUUID``
    layout but defined explicitly so Alembic picks it up.
    """

    __tablename__ = "oauth_account"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    oauth_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    access_token: Mapped[str] = mapped_column(String(1024), nullable=False)
    expires_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    refresh_token: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    account_id: Mapped[str] = mapped_column(String(320), nullable=False, index=True)
    account_email: Mapped[str | None] = mapped_column(String(320), nullable=True)


class AuthProvider(Base):
    """Admin-configured SSO provider (OIDC / OAuth2)."""

    __tablename__ = "auth_providers"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    provider_type: Mapped[str] = mapped_column(String(32), nullable=False)  # "oidc" | "oauth2"
    client_id: Mapped[str] = mapped_column(String(512), nullable=False)
    client_secret_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    discovery_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    authorize_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    token_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    userinfo_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    scopes: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
        server_default="openid email profile",
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="true",
    )
    auto_register: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="true",
    )
    default_role_ids: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        server_default="[]",
    )
    group_mapping: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        server_default="{}",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
