"""Ray clusters the machines run that this server does not manage -- and taking them over.

After a server is rebuilt without its database, its machines go on running
the clusters and models it had started. These routes list them, read from
Ray by the machines' agents, and take one over as it runs
(``services/inference/takeover.py``). A step that cannot be taken answers 409
with the reason in words, and changes nothing.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from llm_port_backend.db.dependencies import get_db_session
from llm_port_backend.db.models.users import User
from llm_port_backend.services.inference import takeover
from llm_port_backend.web.api.rbac import require_permission

router = APIRouter()
_ENV = "inference.environments"


class TakeOverRequest(BaseModel):
    #: Any machine of the cluster; the one that described it in the list.
    node_id: uuid.UUID
    #: What to call the cluster here.
    name: str = Field(min_length=1, max_length=128)
    #: The name each model is offered under at the gateway, by app name;
    #: the model's own name, lower-cased, where none is given.
    aliases: dict[str, str] = Field(default_factory=dict)


def _gateway(request: Request) -> Any:
    """Commands to agents are polled across sessions, so the factory, not this request's session."""
    from llm_port_backend.services.inference.drivers.ray.commands import NodeCommandGateway  # noqa: PLC0415

    factory = getattr(request.app.state, "db_session_factory", None)
    if factory is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="No command channel to the machines.")
    return NodeCommandGateway(factory)


@router.get("")
async def list_found_clusters(
    request: Request,
    _user: User = Depends(require_permission(_ENV, "read")),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, list[dict[str, Any]]]:
    """Clusters the machines run that this server does not manage, and what each serves.

    Asks every machine that runs LLM.Port's Ray runtime outside a cluster
    here -- a few seconds, all at once.
    """
    return await takeover.find(session, _gateway(request))


@router.post("/take-over", status_code=status.HTTP_201_CREATED)
async def take_over(
    body: TakeOverRequest,
    request: Request,
    user: User = Depends(require_permission(_ENV, "create")),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Take over a cluster and every model it serves, as they run: nothing restarts."""
    try:
        return await takeover.take_over(
            session, _gateway(request),
            node_id=body.node_id, name=body.name, aliases=body.aliases, user_id=user.id,
        )
    except takeover.TakeoverError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
