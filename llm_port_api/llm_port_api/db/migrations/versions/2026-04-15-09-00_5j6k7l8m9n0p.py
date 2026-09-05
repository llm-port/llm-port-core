"""Add outbound TLS columns to llm_provider_instance.

Adds per-provider SSL settings:

* ``ssl_verify_mode`` — verification mode (default | verify | verify_custom_ca | insecure)
* ``ssl_ca_bundle_pem`` — encrypted PEM CA bundle (custom trust roots)
* ``ssl_client_cert_pem`` — encrypted PEM mTLS client certificate
* ``ssl_client_key_pem`` — encrypted PEM mTLS client private key

Revision ID: 5j6k7l8m9n0p
Revises: 4h5i6j7k8l9m
Create Date: 2026-04-15 09:00:00.000000
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "5j6k7l8m9n0p"
down_revision = "4h5i6j7k8l9m"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE llm_provider_instance "
        "ADD COLUMN IF NOT EXISTS ssl_verify_mode VARCHAR(32)"
    )
    op.execute(
        "ALTER TABLE llm_provider_instance "
        "ADD COLUMN IF NOT EXISTS ssl_ca_bundle_pem TEXT"
    )
    op.execute(
        "ALTER TABLE llm_provider_instance "
        "ADD COLUMN IF NOT EXISTS ssl_client_cert_pem TEXT"
    )
    op.execute(
        "ALTER TABLE llm_provider_instance "
        "ADD COLUMN IF NOT EXISTS ssl_client_key_pem TEXT"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE llm_provider_instance DROP COLUMN IF EXISTS ssl_client_key_pem"
    )
    op.execute(
        "ALTER TABLE llm_provider_instance DROP COLUMN IF EXISTS ssl_client_cert_pem"
    )
    op.execute(
        "ALTER TABLE llm_provider_instance DROP COLUMN IF EXISTS ssl_ca_bundle_pem"
    )
    op.execute(
        "ALTER TABLE llm_provider_instance DROP COLUMN IF EXISTS ssl_verify_mode"
    )
