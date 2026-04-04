"""PII scan event model — records metadata about every PII operation.

**No raw text or PII values are stored** — only aggregate counts,
entity-type breakdowns, and operational metadata.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, func
from sqlalchemy.dialects.postgresql import JSON
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from llm_port_backend.db.base import Base


class PIIScanEvent(Base):
    """One row per PII scan/redact/sanitize invocation."""

    __tablename__ = "pii_scan_events"

    id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        index=True,
    )
    # "scan" | "redact" | "sanitize"
    operation: Mapped[str] = mapped_column(String(32), index=True)
    # "redact" | "tokenize" | null (for plain scan)
    mode: Mapped[str | None] = mapped_column(String(32), nullable=True)
    language: Mapped[str] = mapped_column(String(10), default="en")
    score_threshold: Mapped[float] = mapped_column(Float, default=0.6)
    # Was any PII detected?
    pii_detected: Mapped[bool] = mapped_column(Boolean, default=False)
    # Total entity count
    entities_found: Mapped[int] = mapped_column(Integer, default=0)
    # Breakdown by entity type, e.g. {"PERSON": 3, "EMAIL_ADDRESS": 1}
    entity_type_counts: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
        default=None,
    )
    # Where the call originated: "api" (direct) or "gateway" (inference pipeline)
    source: Mapped[str] = mapped_column(String(32), default="api")
    # Optional correlation ID from the gateway
    request_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        index=True,
    )
