"""Providers that belong to a deployment.

A model served by a Ray cluster had no row under "LLM Providers" at all, so
the one screen an operator manages providers from showed a stale local
runtime whose container no longer existed and nothing about the cluster that
was actually serving. The fix is not a second list; it is for the deployment
to own a provider row the way it already owns a gateway routing record.

Two columns and one enum value:

``source_kind`` / ``source_id``
    Who owns this row. Deliberately the same names and the same values the
    gateway already uses on ``llm_provider_instance``
    (``'inference_deployment'`` plus the deployment id), so the two layers
    describe ownership identically instead of inventing a second vocabulary.
    Null for a provider a person created by hand, which nothing may delete on
    their behalf.

``inference_cluster``
    A new target. ``local_docker`` and ``remote_endpoint`` are both wrong for
    it: it is neither a container on this host nor somebody else's API, and
    calling it either would make the details page offer the wrong controls --
    a Start button for something that is started by scaling a deployment.

Revision ID: d3r1v3dprov
Revises: j01nr3qu3st
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d3r1v3dprov"
down_revision = "j01nr3qu3st"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Postgres will not add an enum value inside a transaction block that
    # later uses it, but adding it alone is fine and this migration does not
    # write any row carrying the new value.
    op.execute("ALTER TYPE provider_target ADD VALUE IF NOT EXISTS 'inference_cluster'")

    op.add_column(
        "llm_providers",
        sa.Column("source_kind", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "llm_providers",
        sa.Column("source_id", sa.String(length=128), nullable=True),
    )
    # One provider per owning deployment. A partial index so the many
    # hand-made providers, which have no owner, are not forced to be unique
    # against each other on two nulls.
    op.create_index(
        "uq_llm_providers_source",
        "llm_providers",
        ["source_kind", "source_id"],
        unique=True,
        postgresql_where=sa.text("source_kind IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_llm_providers_source", table_name="llm_providers")
    op.drop_column("llm_providers", "source_id")
    op.drop_column("llm_providers", "source_kind")
    # The enum value is intentionally left in place: dropping a value from a
    # Postgres enum requires rewriting the type, and a leftover value that
    # nothing references is harmless.
