"""RAG backend access-control models."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from llm_port_backend.db.base import Base


class RagContainerGrant(Base):
    """Container-scoped permission grant for a user."""

    __tablename__ = "rag_container_grants"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
    )
    tenant_id: Mapped[str] = mapped_column(String(256), nullable=False)
    workspace_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    container_id: Mapped[str] = mapped_column(String(64), nullable=False)
    actions: Mapped[list[str]] = mapped_column(ARRAY(String(64)), nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    __table_args__ = (
        Index("ix_rag_container_grants_user", "user_id"),
        Index("ix_rag_container_grants_scope", "tenant_id", "workspace_id", "container_id"),
    )
