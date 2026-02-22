from sqlalchemy.orm import DeclarativeBase

from llm_port_api.db.meta import meta


class Base(DeclarativeBase):
    """Base for all models."""

    metadata = meta
