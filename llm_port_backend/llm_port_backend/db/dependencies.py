from collections.abc import AsyncGenerator
import logging

from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request
from taskiq import TaskiqDepends

logger = logging.getLogger(__name__)


async def get_db_session(
    request: Request = TaskiqDepends(),
) -> AsyncGenerator[AsyncSession]:
    """
    Create and get database session.

    :param request: current request.
    :yield: database session.
    """
    session: AsyncSession = request.app.state.db_session_factory()

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
