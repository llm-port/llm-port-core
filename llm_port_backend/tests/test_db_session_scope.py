"""A request's writes are committed before its response is sent.

FastAPI ends a ``yield`` dependency after the response: the session
dependency committed once the client already had its answer. A 201 could
stand for a write that then failed to commit, and the next request --
create, then open -- could miss what the first one wrote.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi import Depends, FastAPI, HTTPException
from fastapi.routing import APIRoute

from llm_port_backend.db.dependencies import commit_before_responding, get_db_session


class _Session:
    def __init__(self, events: list[str], *, fail_commit: bool = False) -> None:
        self.events, self.fail_commit = events, fail_commit

    async def commit(self) -> None:
        if self.fail_commit:
            self.events.append("commit failed")
            raise RuntimeError("serialization failure")
        self.events.append("commit")

    async def rollback(self) -> None:
        self.events.append("rollback")

    async def close(self) -> None:
        self.events.append("close")


def _app(events: list[str], *, fail_commit: bool = False) -> FastAPI:
    app = FastAPI()
    app.state.db_session_factory = lambda: _Session(events, fail_commit=fail_commit)

    @app.middleware("http")
    async def sent(request: Any, call_next: Any) -> Any:
        response = await call_next(request)
        events.append(f"response {response.status_code}")
        return response

    @app.post("/things", status_code=201)
    async def create(session: Any = Depends(get_db_session)) -> dict[str, str]:
        return {"ok": "yes"}

    @app.post("/refused")
    async def refuse(session: Any = Depends(get_db_session)) -> None:
        raise HTTPException(status_code=409, detail="no")

    commit_before_responding(app)
    return app


async def _post(app: FastAPI, path: str) -> int:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                                 base_url="http://t") as client:
        return (await client.post(path)).status_code


@pytest.mark.anyio
async def test_the_commit_lands_before_the_response_leaves() -> None:
    events: list[str] = []
    assert await _post(_app(events), "/things") == 201
    assert events.index("commit") < events.index("response 201")


@pytest.mark.anyio
async def test_a_refused_request_is_rolled_back_not_committed() -> None:
    events: list[str] = []
    assert await _post(_app(events), "/refused") == 409
    assert "commit" not in events
    assert "rollback" in events


@pytest.mark.anyio
async def test_a_failing_commit_fails_the_request() -> None:
    """It used to fail after the client had been told it worked."""
    events: list[str] = []
    assert await _post(_app(events, fail_commit=True), "/things") == 500
    assert "response 201" not in events


def test_every_route_of_the_backend_commits_before_responding() -> None:
    from llm_port_backend.web.application import get_app

    app = get_app()
    unwrapped = [r.path for r in app.router.routes
                 if isinstance(r, APIRoute) and not getattr(r, "commits_before_response", False)]
    assert unwrapped == []
