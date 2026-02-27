from sqlalchemy.orm import DeclarativeBase

from llm_port_pii.db.meta import meta


class Base(DeclarativeBase):
    """Base for all models."""

    metadata = meta
