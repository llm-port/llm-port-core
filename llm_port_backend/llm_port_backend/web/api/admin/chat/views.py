"""Admin endpoints for Chat & Sessions management.

Queries the gateway database (``llm_api``) via the secondary engine
set up in ``app.state.llm_graph_trace_session_factory``, using raw SQL
to avoid coupling with the gateway's ORM models.

All read endpoints gracefully return empty data when the gateway has
not yet run its chat migrations (``chat_project`` table missing).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncGenerator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.models.users import User
from llm_port_backend.web.api.admin.dependencies import require_superuser

logger = logging.getLogger(__name__)

router = APIRouter()


def _is_missing_table(exc: ProgrammingError) -> bool:
    """Return True if the error is an UndefinedTableError."""
    return "UndefinedTableError" in str(exc) or "does not exist" in str(exc)


async def _get_gateway_session(request: Request) -> AsyncGenerator[AsyncSession, None]:
    """Yield a session against the gateway (llm_api) database."""
    factory = getattr(request.app.state, "llm_graph_trace_session_factory", None)
    if factory is None:
        raise HTTPException(status_code=503, detail="Gateway database not available")
    async with factory() as session:
        yield session


# ── Projects ──────────────────────────────────────────────────────


@router.get("/projects")
async def list_projects(
    _user: Annotated[User, Depends(require_superuser)],
    gw: AsyncSession = Depends(_get_gateway_session),
) -> dict[str, Any]:
    try:
        result = await gw.execute(
            text(
                "SELECT id, tenant_id, user_id, name, description, "
                "model_alias, created_at, updated_at "
                "FROM chat_project ORDER BY created_at DESC"
            ),
        )
    except ProgrammingError as exc:
        if _is_missing_table(exc):
            return {"data": []}
        raise
    rows = [dict(r._mapping) for r in result]
    for row in rows:
        for k in ("id",):
            row[k] = str(row[k])
        for k in ("created_at", "updated_at"):
            if row.get(k):
                row[k] = row[k].isoformat()
    return {"data": rows}


@router.delete("/projects/{project_id}", status_code=204)
async def delete_project(
    project_id: uuid.UUID,
    _user: Annotated[User, Depends(require_superuser)],
    gw: AsyncSession = Depends(_get_gateway_session),
) -> None:
    try:
        result = await gw.execute(
            text("DELETE FROM chat_project WHERE id = :pid"),
            {"pid": project_id},
        )
        await gw.commit()
    except ProgrammingError as exc:
        if _is_missing_table(exc):
            raise HTTPException(status_code=503, detail="Chat tables not yet migrated")
        raise
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Project not found")


# ── Sessions ──────────────────────────────────────────────────────


@router.get("/sessions")
async def list_sessions(
    _user: Annotated[User, Depends(require_superuser)],
    gw: AsyncSession = Depends(_get_gateway_session),
    project_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    q = (
        "SELECT id, tenant_id, user_id, project_id, title, status, "
        "created_at, updated_at FROM chat_session"
    )
    params: dict[str, Any] = {}
    if project_id:
        q += " WHERE project_id = :pid"
        params["pid"] = project_id
    q += " ORDER BY created_at DESC"

    try:
        result = await gw.execute(text(q), params)
    except ProgrammingError as exc:
        if _is_missing_table(exc):
            return {"data": []}
        raise
    rows = [dict(r._mapping) for r in result]
    for row in rows:
        for k in ("id", "project_id"):
            if row.get(k):
                row[k] = str(row[k])
        for k in ("created_at", "updated_at"):
            if row.get(k):
                row[k] = row[k].isoformat()
    return {"data": rows}


@router.delete("/sessions/{session_id}", status_code=204)
async def delete_session(
    session_id: uuid.UUID,
    _user: Annotated[User, Depends(require_superuser)],
    gw: AsyncSession = Depends(_get_gateway_session),
) -> None:
    try:
        result = await gw.execute(
            text("DELETE FROM chat_session WHERE id = :sid"),
            {"sid": session_id},
        )
        await gw.commit()
    except ProgrammingError as exc:
        if _is_missing_table(exc):
            raise HTTPException(status_code=503, detail="Chat tables not yet migrated")
        raise
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Session not found")


# ── Attachments ───────────────────────────────────────────────────


@router.get("/attachments")
async def list_attachments(
    _user: Annotated[User, Depends(require_superuser)],
    gw: AsyncSession = Depends(_get_gateway_session),
    session_id: uuid.UUID | None = None,
    project_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    q = (
        "SELECT id, tenant_id, user_id, session_id, project_id, "
        "filename, content_type, size_bytes, extraction_status, "
        "scope, page_count, truncated, created_at "
        "FROM chat_attachment"
    )
    clauses: list[str] = []
    params: dict[str, Any] = {}
    if session_id:
        clauses.append("session_id = :sid")
        params["sid"] = session_id
    if project_id:
        clauses.append("project_id = :pid")
        params["pid"] = project_id
    if clauses:
        q += " WHERE " + " AND ".join(clauses)
    q += " ORDER BY created_at DESC"

    try:
        result = await gw.execute(text(q), params)
    except ProgrammingError as exc:
        if _is_missing_table(exc):
            return {"data": []}
        raise
    rows = [dict(r._mapping) for r in result]
    for row in rows:
        for k in ("id", "session_id", "project_id", "message_id"):
            if row.get(k):
                row[k] = str(row[k])
        for k in ("created_at",):
            if row.get(k):
                row[k] = row[k].isoformat()
    return {"data": rows}


@router.delete("/attachments/{attachment_id}", status_code=204)
async def delete_attachment(
    attachment_id: uuid.UUID,
    _user: Annotated[User, Depends(require_superuser)],
    gw: AsyncSession = Depends(_get_gateway_session),
) -> None:
    try:
        result = await gw.execute(
            text("DELETE FROM chat_attachment WHERE id = :aid"),
            {"aid": attachment_id},
        )
        await gw.commit()
    except ProgrammingError as exc:
        if _is_missing_table(exc):
            raise HTTPException(status_code=503, detail="Chat tables not yet migrated")
        raise
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="Attachment not found")


# ── Stats ─────────────────────────────────────────────────────────


@router.get("/stats")
async def chat_stats(
    _user: Annotated[User, Depends(require_superuser)],
    gw: AsyncSession = Depends(_get_gateway_session),
) -> dict[str, Any]:
    empty = {
        "total_projects": 0,
        "total_sessions": 0,
        "total_attachments": 0,
        "total_attachment_bytes": 0,
    }
    try:
        projects = await gw.execute(text("SELECT count(*) FROM chat_project"))
        sessions = await gw.execute(text("SELECT count(*) FROM chat_session"))
        attachments = await gw.execute(
            text("SELECT count(*), coalesce(sum(size_bytes), 0) FROM chat_attachment"),
        )
    except ProgrammingError as exc:
        if _is_missing_table(exc):
            return empty
        raise
    att_row = attachments.one()
    return {
        "total_projects": projects.scalar() or 0,
        "total_sessions": sessions.scalar() or 0,
        "total_attachments": att_row[0] or 0,
        "total_attachment_bytes": att_row[1] or 0,
    }
