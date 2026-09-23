"""vLLM the machines already run, found by their agents (Phase 8).

A step that cannot be taken answers 409 with the reason in words, and changes
nothing.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from llm_port_backend.db.dependencies import get_db_session
from llm_port_backend.db.models.inference import InferenceAdoption
from llm_port_backend.db.models.users import User
from llm_port_backend.services.inference import found as found_vllm
from llm_port_backend.web.api.rbac import require_permission

router = APIRouter()
_DEP = "inference.deployments"


class RouteRequest(BaseModel):
    node_id: uuid.UUID
    container: str
    alias: str


def _gateway_sync(request: Request) -> Any:
    return getattr(getattr(request.app.state, "llm_service", None), "gateway_sync", None)


@router.get("")
async def list_found(
    check: bool = True,
    node_id: uuid.UUID | None = None,
    _user: User = Depends(require_permission(_DEP, "read")),
    session: AsyncSession = Depends(get_db_session),
) -> list[dict[str, Any]]:
    """Every vLLM container the machines reported, with what can be done with each.

    ``check`` asks each running one what it serves (briefly, all at once).
    """
    entries = await found_vllm.found(session, check=check)
    if node_id is not None:
        entries = [e for e in entries if e["node"]["id"] == str(node_id)]
    return entries


@router.post("/route", status_code=status.HTTP_201_CREATED)
async def route(
    body: RouteRequest,
    request: Request,
    user: User = Depends(require_permission(_DEP, "create")),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Route a found container at the gateway under a name, as it is."""
    try:
        adoption = await found_vllm.route(
            session, _gateway_sync(request),
            node_id=body.node_id, container_name=body.container, alias=body.alias, user_id=user.id,
        )
    except found_vllm.FoundError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return found_vllm.describe(adoption)


@router.get("/routed")
async def list_routed(
    _user: User = Depends(require_permission(_DEP, "read")),
    session: AsyncSession = Depends(get_db_session),
) -> list[dict[str, Any]]:
    """Found containers LLM.Port routes, and ones it used to."""
    rows = await session.execute(select(InferenceAdoption).order_by(InferenceAdoption.created_at.desc()))
    return [found_vllm.describe(a) for a in rows.scalars()]


@router.post("/{adoption_id}/release")
async def release(
    adoption_id: uuid.UUID,
    request: Request,
    _user: User = Depends(require_permission(_DEP, "operate")),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Stop routing a found container. The container keeps running."""
    adoption = await session.get(InferenceAdoption, adoption_id)
    if adoption is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Nothing like that is routed.")
    try:
        await found_vllm.release(session, _gateway_sync(request), adoption)
    except found_vllm.FoundError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return found_vllm.describe(adoption)
