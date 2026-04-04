"""Pydantic schemas for the audit log API."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from llm_port_backend.db.models.containers import AuditResult


class AuditEventDTO(BaseModel):
    """Read-only representation of an audit event."""

    id: uuid.UUID
    time: datetime
    actor_id: uuid.UUID | None = None
    action: str
    target_type: str
    target_id: str
    result: AuditResult
    severity: str
    metadata_json: str | None = None

    model_config = ConfigDict(from_attributes=True)
