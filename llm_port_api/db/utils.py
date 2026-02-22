import re

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from llm_port_api.settings import settings

_DB_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _safe_db_name(name: str) -> str:
    if not _DB_NAME_PATTERN.fullmatch(name):
        msg = "Invalid database name."
        raise ValueError(msg)
    return name


async def create_database() -> None:
    """Create the configured database if needed."""
    db_url = make_url(str(settings.db_url.with_path("/postgres")))
    engine = create_async_engine(db_url, isolation_level="AUTOCOMMIT")
    db_name = _safe_db_name(settings.db_base)

    async with engine.connect() as conn:
        database_exists = bool(
            (
                await conn.execute(
                    text("SELECT 1 FROM pg_database WHERE datname = :db_name"),
                    {"db_name": db_name},
                )
            ).scalar(),
        )
        if not database_exists:
            await conn.execute(
                text(
                    f'CREATE DATABASE "{db_name}" ENCODING "utf8" TEMPLATE template1',
                ),
            )


async def drop_database() -> None:
    """Drop the configured database."""
    db_url = make_url(str(settings.db_url.with_path("/postgres")))
    engine = create_async_engine(db_url, isolation_level="AUTOCOMMIT")
    db_name = _safe_db_name(settings.db_base)
    async with engine.connect() as conn:
        await conn.execute(
            text(
                "SELECT pg_terminate_backend(pg_stat_activity.pid) "
                "FROM pg_stat_activity "
                "WHERE pg_stat_activity.datname = :db_name "
                "AND pid <> pg_backend_pid();",
            ),
            {"db_name": db_name},
        )
        await conn.execute(text(f'DROP DATABASE "{db_name}"'))
