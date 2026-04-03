"""Add infra_node_profile table and profile_id FK on infra_node.

Revision ID: n0d3pr0f1l31
Revises: st4tusm3ss4g
Create Date: 2026-03-26 10:00:00.000000
"""

import uuid

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID

revision = "n0d3pr0f1l31"
down_revision = "st4tusm3ss4g"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "infra_node_profile",
        sa.Column("id", PGUUID(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(128), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("runtime_config", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("gpu_config", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("storage_config", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("network_config", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("logging_config", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("security_config", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("update_config", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_infra_node_profile_name", "infra_node_profile", ["name"])

    op.add_column(
        "infra_node",
        sa.Column(
            "profile_id",
            PGUUID(as_uuid=True),
            sa.ForeignKey("infra_node_profile.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )

    # ── Seed predefined profiles ─────────────────────────────────
    profile_table = sa.table(
        "infra_node_profile",
        sa.column("id", PGUUID(as_uuid=True)),
        sa.column("name", sa.String),
        sa.column("description", sa.Text),
        sa.column("is_default", sa.Boolean),
        sa.column("runtime_config", JSONB),
        sa.column("gpu_config", JSONB),
        sa.column("storage_config", JSONB),
        sa.column("network_config", JSONB),
        sa.column("logging_config", JSONB),
        sa.column("security_config", JSONB),
        sa.column("update_config", JSONB),
    )

    _SEED_PROFILES = [
        {
            "id": uuid.UUID("00000000-0000-4000-a000-000000000001"),
            "name": "NVIDIA DGX Spark",
            "description": (
                "NVIDIA DGX Spark — Grace Blackwell GB10 Superchip, "
                "128 GB unified memory, MediaTek connectivity. "
                "Optimised for desktop AI workloads."
            ),
            "is_default": True,
            "runtime_config": {
                "runtime_type": "docker",
                "gpu_flag_style": "native",
            },
            "gpu_config": {
                "vendor": "nvidia",
                "driver_management": "repo",
            },
            "storage_config": {
                "model_cache_path": "/home/nvidia/models",
                "max_cache_size_gb": 100,
            },
            "network_config": {},
            "logging_config": {
                "system_log_source": "journalctl",
                "push_destination": "loki",
            },
            "security_config": {
                "image_allowlist": ["nvcr.io/*", "docker.io/library/*"],
                "image_signing": "none",
            },
            "update_config": {
                "source_type": "online",
                "reboot_policy": "prompt",
                "package_manager": "auto",
                "upgrade_command": "dist-upgrade",
                "firmware_enabled": True,
            },
        },
        {
            "id": uuid.UUID("00000000-0000-4000-a000-000000000002"),
            "name": "NVIDIA DGX Station",
            "description": (
                "NVIDIA DGX Station — multi-GPU workstation profile. "
                "Docker runtime with native GPU passthrough, NVLink topology."
            ),
            "is_default": False,
            "runtime_config": {
                "runtime_type": "docker",
                "gpu_flag_style": "native",
            },
            "gpu_config": {
                "vendor": "nvidia",
                "driver_management": "repo",
            },
            "storage_config": {
                "model_cache_path": "/raid/models",
                "max_cache_size_gb": 2000,
            },
            "network_config": {},
            "logging_config": {
                "system_log_source": "journalctl",
                "push_destination": "loki",
            },
            "security_config": {
                "image_allowlist": ["nvcr.io/*", "docker.io/library/*"],
                "image_signing": "none",
            },
            "update_config": {
                "source_type": "online",
                "reboot_policy": "prompt",
                "package_manager": "auto",
                "upgrade_command": "dist-upgrade",
                "firmware_enabled": True,
            },
        },
        {
            "id": uuid.UUID("00000000-0000-4000-a000-000000000003"),
            "name": "Apple Silicon",
            "description": (
                "Apple Silicon Mac — Metal GPU with unified memory. "
                "Docker Desktop or Podman, auto-detected GPU."
            ),
            "is_default": False,
            "runtime_config": {
                "runtime_type": "auto",
            },
            "gpu_config": {
                "vendor": "apple",
            },
            "storage_config": {},
            "network_config": {},
            "logging_config": {
                "system_log_source": "syslog",
                "push_destination": "loki",
            },
            "security_config": {},
            "update_config": {
                "source_type": "online",
                "reboot_policy": "never",
                "package_manager": "auto",
                "upgrade_command": "upgrade",
                "firmware_enabled": False,
            },
        },
        {
            "id": uuid.UUID("00000000-0000-4000-a000-000000000004"),
            "name": "Generic Linux + Podman",
            "description": (
                "Generic Linux server with Podman runtime. "
                "Rootless containers with CDI GPU device passthrough."
            ),
            "is_default": False,
            "runtime_config": {
                "runtime_type": "podman",
                "gpu_flag_style": "cdi",
            },
            "gpu_config": {
                "vendor": "auto",
            },
            "storage_config": {},
            "network_config": {},
            "logging_config": {
                "system_log_source": "journalctl",
                "push_destination": "loki",
            },
            "security_config": {},
            "update_config": {
                "source_type": "online",
                "reboot_policy": "prompt",
                "package_manager": "auto",
                "upgrade_command": "upgrade",
                "firmware_enabled": False,
            },
        },
    ]

    for profile in _SEED_PROFILES:
        op.execute(
            profile_table.insert().values(
                id=profile["id"],
                name=profile["name"],
                description=profile["description"],
                is_default=profile["is_default"],
                runtime_config=profile["runtime_config"],
                gpu_config=profile["gpu_config"],
                storage_config=profile["storage_config"],
                network_config=profile["network_config"],
                logging_config=profile["logging_config"],
                security_config=profile["security_config"],
                update_config=profile["update_config"],
            )
        )


def downgrade() -> None:
    op.drop_column("infra_node", "profile_id")
    op.drop_index("ix_infra_node_profile_name", table_name="infra_node_profile")
    op.drop_table("infra_node_profile")
