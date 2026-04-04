"""Bootstrap endpoint — one-shot first-admin creation.

Available *only* when zero users exist in the database.  After the
first admin is created the endpoints return 409 Conflict for all
subsequent calls, making them self-disabling.
"""

from __future__ import annotations

import logging
import secrets
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.dependencies import get_db_session
from llm_port_backend.db.models.users import User

log = logging.getLogger(__name__)

router = APIRouter(prefix="/bootstrap", tags=["bootstrap"])


# ── Schemas ────────────────────────────────────────────────────────


class BootstrapStatusResponse(BaseModel):
    """Whether the system needs initial admin setup."""

    needs_bootstrap: bool


class BootstrapRequest(BaseModel):
    """Payload for first-admin creation."""

    email: str = Field("admin@localhost", min_length=3, max_length=320)
    password: str | None = Field(
        default=None,
        description="Admin password.  Auto-generated if omitted.",
        min_length=6,
        max_length=128,
    )
    generate_api_token: bool = Field(
        default=True,
        description="Generate an LLM gateway API token for the new admin.",
    )
    tenant_id: str = Field(
        default="default",
        min_length=1,
        max_length=128,
    )


class BootstrapResponse(BaseModel):
    """Credentials returned exactly once after bootstrap."""

    email: str
    password: str
    api_token: str | None = None


# ── Helpers ────────────────────────────────────────────────────────


def _generate_password(length: int = 20) -> str:
    """Generate a URL-safe random password."""
    return secrets.token_urlsafe(length)


async def _user_count(session: AsyncSession) -> int:
    result = await session.execute(select(func.count()).select_from(User))
    return result.scalar_one()


# ── Endpoints ──────────────────────────────────────────────────────


@router.get(
    "/status",
    response_model=BootstrapStatusResponse,
    name="bootstrap_status",
)
async def bootstrap_status(
    session: AsyncSession = Depends(get_db_session),
) -> BootstrapStatusResponse:
    """Check whether the system needs initial admin setup."""
    count = await _user_count(session)
    return BootstrapStatusResponse(needs_bootstrap=count == 0)


@router.post(
    "",
    response_model=BootstrapResponse,
    status_code=status.HTTP_201_CREATED,
    name="bootstrap",
)
async def bootstrap(
    payload: BootstrapRequest,
    session: AsyncSession = Depends(get_db_session),
) -> BootstrapResponse:
    """Create the first admin user and optionally generate an API token.

    This endpoint is **only** available when the ``user`` table is empty.
    Once the first admin is created, all subsequent calls return
    ``409 Conflict``.
    """
    from fastapi_users.password import PasswordHelper  # noqa: PLC0415

    # ── Guard: only when zero users exist ─────────────────────
    count = await _user_count(session)
    if count > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="System is already bootstrapped. Admin users exist.",
        )

    # ── Create admin user ─────────────────────────────────────
    raw_password = payload.password or _generate_password()
    password_helper = PasswordHelper()

    admin_user = User(
        id=uuid.uuid4(),
        email=payload.email,
        hashed_password=password_helper.hash(raw_password),
        is_active=True,
        is_superuser=True,
        is_verified=True,
    )
    session.add(admin_user)
    await session.flush()

    # ── Assign the built-in admin role ────────────────────────
    from llm_port_backend.db.dao.rbac_dao import RbacDAO  # noqa: PLC0415

    rbac_dao = RbacDAO(session)
    admin_role = await rbac_dao.get_role_by_name("admin")
    if admin_role:
        await rbac_dao.assign_role(admin_user.id, admin_role.id)
        await session.flush()

    # ── Optional: generate API gateway token ──────────────────
    api_token: str | None = None
    if payload.generate_api_token:
        import time  # noqa: PLC0415

        import jwt as pyjwt  # noqa: PLC0415

        from llm_port_backend.services.system_settings.crypto import SettingsCrypto  # noqa: PLC0415
        from llm_port_backend.settings import settings  # noqa: PLC0415

        # Resolve the JWT signing secret (same logic as the token endpoint).
        # Use the secret WITHOUT stripping — JWTStrategy uses it as-is.
        secret = settings.users_secret if settings.users_secret else ""
        if not secret and settings.settings_master_key:
            row = await session.execute(
                text("SELECT ciphertext FROM system_setting_secret WHERE key = :k"),
                {"k": "llm_port_backend.users_secret"},
            )
            ciphertext = row.scalar_one_or_none()
            if ciphertext:
                try:
                    secret = SettingsCrypto(settings.settings_master_key).decrypt(ciphertext).strip()
                except Exception:  # noqa: BLE001
                    secret = ""

        if secret:
            now = int(time.time())
            claims = {
                "sub": str(admin_user.id),
                "aud": "fastapi-users:auth",
                "tenant_id": payload.tenant_id,
                "email": admin_user.email,
                "iat": now,
                "jti": uuid.uuid4().hex,
            }
            api_token = pyjwt.encode(claims, secret, algorithm="HS256")
        else:
            log.warning(
                "JWT secret not available during bootstrap — "
                "API token not generated. Generate one manually via the admin UI."
            )

    log.info("System bootstrapped: admin user '%s' created.", payload.email)

    return BootstrapResponse(
        email=payload.email,
        password=raw_password,
        api_token=api_token,
    )
