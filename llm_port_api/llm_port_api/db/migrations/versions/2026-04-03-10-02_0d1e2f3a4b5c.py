"""Seed default price catalog entries.

Revision ID: 0d1e2f3a4b5c
Revises: 9c0d1e2f3a4b
Create Date: 2026-04-03 10:02:00.000000
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0d1e2f3a4b5c"
down_revision = "9c0d1e2f3a4b"
branch_labels = None
depends_on = None

# Default seed pricing (source: public provider pricing pages as of 2026-04)
_SEED_ROWS = [
    # (provider, model, input_price_per_1k, output_price_per_1k)
    ("openai", "gpt-4.1", 0.002, 0.008),
    ("openai", "gpt-4.1-mini", 0.0004, 0.0016),
    ("openai", "gpt-4.1-nano", 0.0001, 0.0004),
    ("openai", "o4-mini", 0.0011, 0.0044),
    ("anthropic", "claude-sonnet-4-20250514", 0.003, 0.015),
    ("anthropic", "claude-haiku-3-20250414", 0.0008, 0.004),
    ("google", "gemini-2.5-pro", 0.00125, 0.01),
    ("google", "gemini-2.5-flash", 0.00015, 0.0006),
]


def upgrade() -> None:
    for provider, model, input_price, output_price in _SEED_ROWS:
        op.execute(
            "INSERT INTO price_catalog "
            "(provider, model, input_price_per_1k, output_price_per_1k, source) "
            f"VALUES ('{provider}', '{model}', {input_price}, {output_price}, 'default')"
        )


def downgrade() -> None:
    op.execute("DELETE FROM price_catalog WHERE source = 'default'")
