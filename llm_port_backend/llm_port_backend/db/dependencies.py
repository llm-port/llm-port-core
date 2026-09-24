from collections.abc import AsyncGenerator
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request
from taskiq import TaskiqDepends

logger = logging.getLogger(__name__)


async def get_db_session(
    request: Request = TaskiqDepends(),
) -> AsyncGenerator[AsyncSession]:
    """
    Create and get database session.

    The session is committed before the response is sent (see
    :func:`commit_before_responding`); the commit after ``yield`` only
    catches what a response wrote while it was being sent.

    :param request: current request.
    :yield: database session.
    """
    session: AsyncSession = request.app.state.db_session_factory()
    _remember(request, session)

    try:
        yield session
    except Exception:
        # On request errors, always rollback so a failed transaction does
        # not leak into dependency teardown and trigger noisy cascades.
        try:
            await session.rollback()
        except Exception:
            logger.exception("Database rollback failed during request error handling.")
        raise
    else:
        # Commit only when the request completed successfully.
        try:
            await session.commit()
        except Exception:
            # Best effort rollback after commit failure.
            try:
                await session.rollback()
            except Exception:
                logger.exception("Database rollback failed after commit failure.")
            raise
    finally:
        try:
            await session.close()
        except Exception:
            logger.exception("Database session close failed.")


def _remember(request: Any, session: AsyncSession) -> None:
    """Note the request's session, for :func:`commit_before_responding`."""
    try:
        state = request.state
    except Exception:  # noqa: BLE001 - a task's stand-in request has no state
        return
    sessions = getattr(state, "db_sessions", None)
    if sessions is None:
        state.db_sessions = [session]
    else:
        sessions.append(session)


def commit_before_responding(app: Any) -> None:
    """Commit each request's database session before its response is sent.

    FastAPI ends a ``yield`` dependency after the response has been sent,
    so :func:`get_db_session` committed once the client already had its
    answer: a 201 could stand for a write that then failed to commit, and a
    request sent right after (create, then open) could miss what the first
    one wrote. Declaring the dependency ``scope="function"`` would fix the
    order, but the login machinery (fastapi-users) holds the same session
    in a dependency FastAPI will not let depend on a function-scoped one.

    So every route's handler is wrapped: the endpoint returns its response,
    the request's sessions are committed, then the response is sent. A
    failing commit fails the request (500) instead of passing unnoticed.
    Call it once every router is included.
    """
    from fastapi.routing import APIRoute, request_response  # noqa: PLC0415

    for route in app.router.routes:
        if not isinstance(route, APIRoute) or getattr(route, "commits_before_response", False):
            continue
        handler = route.get_route_handler()

        async def committing(request: Request, _handler: Any = handler) -> Any:
            response = await _handler(request)
            for session in getattr(request.state, "db_sessions", None) or ():
                await session.commit()
            return response

        route.app = request_response(committing)
        route.commits_before_response = True
