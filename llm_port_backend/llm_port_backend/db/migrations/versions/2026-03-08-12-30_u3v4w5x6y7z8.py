"""Add DB-level trigger for remote provider capacity guard.

Revision ID: u3v4w5x6y7z8
Revises: t2r3e4h5i6r7
Create Date: 2026-03-08 12:30:00.000000
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "u3v4w5x6y7z8"
down_revision = "t2r3e4h5i6r7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create trigger to limit remote_endpoint providers to 3."""
    op.execute(
        """
        CREATE OR REPLACE FUNCTION enforce_remote_provider_limit()
        RETURNS trigger AS $$
        DECLARE
            max_count INTEGER := 3;
            current_count INTEGER := 0;
            must_check BOOLEAN := false;
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NEW.target = 'remote_endpoint' THEN
                    must_check := true;
                END IF;
            END IF;

            IF TG_OP = 'UPDATE' THEN
                IF NEW.target = 'remote_endpoint' AND (
                    OLD.target != 'remote_endpoint'
                    OR NEW.target != OLD.target
                ) THEN
                    must_check := true;
                END IF;
            END IF;

            IF must_check THEN
                LOCK TABLE llm_providers IN EXCLUSIVE MODE;

                SELECT INTO current_count COUNT(*)
                FROM llm_providers
                WHERE target = 'remote_endpoint';

                IF current_count >= max_count THEN
                    RAISE EXCEPTION
                        'Remote provider limit reached. '
                        'Upgrade to LLM.port Enterprise for unlimited providers.';
                END IF;
            END IF;

            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_enforce_remote_provider_limit
            BEFORE INSERT OR UPDATE ON llm_providers
            FOR EACH ROW EXECUTE PROCEDURE enforce_remote_provider_limit();
        """
    )


def downgrade() -> None:
    """Drop remote provider limit trigger and function."""
    op.execute(
        "DROP TRIGGER IF EXISTS trg_enforce_remote_provider_limit ON llm_providers"
    )
    op.execute("DROP FUNCTION IF EXISTS enforce_remote_provider_limit()")
