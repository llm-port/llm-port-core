"""Admin auth-provider management and OAuth callback endpoints."""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from starlette import status

from llm_port_backend.db.dao.auth_provider_dao import AuthProviderDAO
from llm_port_backend.db.models.oauth import AuthProvider
from llm_port_backend.db.models.users import User, get_jwt_strategy
from llm_port_backend.web.api.admin.auth_providers.schema import (
    AuthProviderDTO,
    AuthProviderPublicDTO,
    CreateAuthProviderRequest,
    UpdateAuthProviderRequest,
)
from llm_port_backend.web.api.admin.dependencies import require_superuser

logger = logging.getLogger(__name__)

router = APIRouter()


# ─────────────────────────────────────────────────────────────────────────────
# Secret encryption helpers (Fernet-based symmetric encryption)
# ─────────────────────────────────────────────────────────────────────────────


def _get_fernet():
    """Return a Fernet instance derived from the users_secret setting."""
    import base64
    import hashlib

    from cryptography.fernet import Fernet

    from llm_port_backend.settings import settings

    # Derive a 32-byte key from users_secret (which may be arbitrary length)
    key_bytes = hashlib.sha256(settings.users_secret.encode()).digest()
    fernet_key = base64.urlsafe_b64encode(key_bytes)
    return Fernet(fernet_key)


def _encrypt_secret(plaintext: str) -> str:
    """Encrypt a client secret for storage."""
    return _get_fernet().encrypt(plaintext.encode()).decode()


def _decrypt_secret(ciphertext: str) -> str:
    """Decrypt a stored client secret."""
    return _get_fernet().decrypt(ciphertext.encode()).decode()


# ─────────────────────────────────────────────────────────────────────────────
# DTO helpers
# ─────────────────────────────────────────────────────────────────────────────


def _provider_to_dto(provider: AuthProvider) -> AuthProviderDTO:
    return AuthProviderDTO(
        id=provider.id,
        name=provider.name,
        provider_type=provider.provider_type,
        client_id=provider.client_id,
        discovery_url=provider.discovery_url,
        authorize_url=provider.authorize_url,
        token_url=provider.token_url,
        userinfo_url=provider.userinfo_url,
        scopes=provider.scopes,
        enabled=provider.enabled,
        auto_register=provider.auto_register,
        default_role_ids=[str(rid) for rid in (provider.default_role_ids or [])],
        group_mapping=provider.group_mapping or {},
        created_at=provider.created_at,
        updated_at=provider.updated_at,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Public endpoint: enabled providers (for login page)
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "/public",
    response_model=list[AuthProviderPublicDTO],
    name="list_enabled_providers",
)
async def list_enabled_providers(
    provider_dao: AuthProviderDAO = Depends(),
) -> list[AuthProviderPublicDTO]:
    """List enabled providers — no auth required (login page needs this)."""
    providers = await provider_dao.list_enabled_providers()
    return [
        AuthProviderPublicDTO(
            id=p.id,
            name=p.name,
            provider_type=p.provider_type,
        )
        for p in providers
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Admin CRUD
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/", response_model=list[AuthProviderDTO], name="list_auth_providers")
async def list_auth_providers(
    _user: Annotated[User, Depends(require_superuser)] = None,  # type: ignore[assignment]
    provider_dao: AuthProviderDAO = Depends(),
) -> list[AuthProviderDTO]:
    """List all configured auth providers."""
    providers = await provider_dao.list_providers()
    return [_provider_to_dto(p) for p in providers]


@router.get("/{provider_id}", response_model=AuthProviderDTO, name="get_auth_provider")
async def get_auth_provider(
    provider_id: uuid.UUID,
    _user: Annotated[User, Depends(require_superuser)] = None,  # type: ignore[assignment]
    provider_dao: AuthProviderDAO = Depends(),
) -> AuthProviderDTO:
    """Get a single auth provider by ID."""
    provider = await provider_dao.get_provider_by_id(provider_id)
    if provider is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Provider not found")
    return _provider_to_dto(provider)


@router.post(
    "/",
    response_model=AuthProviderDTO,
    status_code=status.HTTP_201_CREATED,
    name="create_auth_provider",
)
async def create_auth_provider(
    payload: CreateAuthProviderRequest,
    _user: Annotated[User, Depends(require_superuser)] = None,  # type: ignore[assignment]
    provider_dao: AuthProviderDAO = Depends(),
) -> AuthProviderDTO:
    """Create a new auth provider."""
    existing = await provider_dao.get_provider_by_name(payload.name)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Provider '{payload.name}' already exists",
        )
    provider = await provider_dao.create_provider(
        name=payload.name,
        provider_type=payload.provider_type,
        client_id=payload.client_id,
        client_secret_encrypted=_encrypt_secret(payload.client_secret),
        discovery_url=payload.discovery_url,
        authorize_url=payload.authorize_url,
        token_url=payload.token_url,
        userinfo_url=payload.userinfo_url,
        scopes=payload.scopes,
        enabled=payload.enabled,
        auto_register=payload.auto_register,
        default_role_ids=payload.default_role_ids,
        group_mapping=payload.group_mapping,
    )
    return _provider_to_dto(provider)


@router.patch("/{provider_id}", response_model=AuthProviderDTO, name="update_auth_provider")
async def update_auth_provider(
    provider_id: uuid.UUID,
    payload: UpdateAuthProviderRequest,
    _user: Annotated[User, Depends(require_superuser)] = None,  # type: ignore[assignment]
    provider_dao: AuthProviderDAO = Depends(),
) -> AuthProviderDTO:
    """Update an auth provider."""
    existing = await provider_dao.get_provider_by_id(provider_id)
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Provider not found")

    update_kwargs: dict = {}
    if payload.name is not None:
        update_kwargs["name"] = payload.name
    if payload.provider_type is not None:
        update_kwargs["provider_type"] = payload.provider_type
    if payload.client_id is not None:
        update_kwargs["client_id"] = payload.client_id
    if payload.client_secret is not None:
        update_kwargs["client_secret_encrypted"] = _encrypt_secret(payload.client_secret)
    if payload.discovery_url is not None:
        update_kwargs["discovery_url"] = payload.discovery_url
    if payload.authorize_url is not None:
        update_kwargs["authorize_url"] = payload.authorize_url
    if payload.token_url is not None:
        update_kwargs["token_url"] = payload.token_url
    if payload.userinfo_url is not None:
        update_kwargs["userinfo_url"] = payload.userinfo_url
    if payload.scopes is not None:
        update_kwargs["scopes"] = payload.scopes
    if payload.enabled is not None:
        update_kwargs["enabled"] = payload.enabled
    if payload.auto_register is not None:
        update_kwargs["auto_register"] = payload.auto_register
    if payload.default_role_ids is not None:
        update_kwargs["default_role_ids"] = payload.default_role_ids
    if payload.group_mapping is not None:
        update_kwargs["group_mapping"] = payload.group_mapping

    provider = await provider_dao.update_provider(provider_id, **update_kwargs)
    return _provider_to_dto(provider)  # type: ignore[arg-type]


@router.delete(
    "/{provider_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    name="delete_auth_provider",
)
async def delete_auth_provider(
    provider_id: uuid.UUID,
    _user: Annotated[User, Depends(require_superuser)] = None,  # type: ignore[assignment]
    provider_dao: AuthProviderDAO = Depends(),
) -> None:
    """Delete an auth provider."""
    existing = await provider_dao.get_provider_by_id(provider_id)
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Provider not found")
    await provider_dao.delete_provider(provider_id)


# ─────────────────────────────────────────────────────────────────────────────
# OAuth flow endpoints (authorize redirect + callback)
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "/{provider_id}/authorize",
    name="oauth_authorize",
)
async def oauth_authorize(
    provider_id: uuid.UUID,
    request: Request,
    provider_dao: AuthProviderDAO = Depends(),
) -> RedirectResponse:
    """Redirect the user to the external OAuth/OIDC provider for login."""
    from httpx_oauth.clients.openid import OpenID  # noqa: PLC0415

    provider = await provider_dao.get_provider_by_id(provider_id)
    if provider is None or not provider.enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Provider not found")

    client_secret = _decrypt_secret(provider.client_secret_encrypted)

    if provider.provider_type == "oidc" and provider.discovery_url:
        client = OpenID(
            client_id=provider.client_id,
            client_secret=client_secret,
            openid_configuration_endpoint=provider.discovery_url,
        )
    else:
        from httpx_oauth.oauth2 import OAuth2  # noqa: PLC0415

        if not provider.authorize_url or not provider.token_url:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="OAuth2 provider requires authorize_url and token_url",
            )
        client = OAuth2(
            client_id=provider.client_id,
            client_secret=client_secret,
            authorize_endpoint=provider.authorize_url,
            access_token_endpoint=provider.token_url,
        )

    callback_url = str(request.url_for("oauth_callback", provider_id=str(provider_id)))
    scopes = [s.strip() for s in provider.scopes.split() if s.strip()]

    authorization_url = await client.get_authorization_url(
        callback_url,
        scope=scopes,
    )
    return RedirectResponse(authorization_url)


@router.get(
    "/{provider_id}/callback",
    name="oauth_callback",
)
async def oauth_callback(
    provider_id: uuid.UUID,
    code: str,
    request: Request,
    provider_dao: AuthProviderDAO = Depends(),
) -> RedirectResponse:
    """Handle the OAuth callback, exchange code for token, create/login user."""
    import httpx  # noqa: PLC0415
    from sqlalchemy import select  # noqa: PLC0415

    from llm_port_backend.db.dao.rbac_dao import RbacDAO  # noqa: PLC0415
    from llm_port_backend.db.models.oauth import OAuthAccount  # noqa: PLC0415

    provider = await provider_dao.get_provider_by_id(provider_id)
    if provider is None or not provider.enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Provider not found")

    client_secret = _decrypt_secret(provider.client_secret_encrypted)

    # Build the OAuth client
    if provider.provider_type == "oidc" and provider.discovery_url:
        from httpx_oauth.clients.openid import OpenID  # noqa: PLC0415

        client = OpenID(
            client_id=provider.client_id,
            client_secret=client_secret,
            openid_configuration_endpoint=provider.discovery_url,
        )
    else:
        from httpx_oauth.oauth2 import OAuth2  # noqa: PLC0415

        if not provider.authorize_url or not provider.token_url:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid provider configuration",
            )
        client = OAuth2(
            client_id=provider.client_id,
            client_secret=client_secret,
            authorize_endpoint=provider.authorize_url,
            access_token_endpoint=provider.token_url,
        )

    callback_url = str(request.url_for("oauth_callback", provider_id=str(provider_id)))

    # Exchange code for tokens
    try:
        token = await client.get_access_token(code, callback_url)
    except Exception:
        logger.exception("OAuth token exchange failed for provider %s", provider.name)
        return RedirectResponse("/login?error=oauth_failed")

    access_token = token["access_token"]
    account_id = ""
    account_email = ""

    # Fetch user info
    try:
        userinfo_url = provider.userinfo_url
        if not userinfo_url and provider.provider_type == "oidc" and provider.discovery_url:
            # Try to get userinfo from OIDC discovery
            async with httpx.AsyncClient() as http:
                disc = await http.get(provider.discovery_url)
                disc_data = disc.json()
                userinfo_url = disc_data.get("userinfo_endpoint")

        if userinfo_url:
            async with httpx.AsyncClient() as http:
                resp = await http.get(
                    userinfo_url,
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                if resp.status_code == 200:
                    info = resp.json()
                    account_id = info.get("sub", info.get("id", ""))
                    account_email = info.get("email", "")
    except Exception:
        logger.exception("Failed to fetch userinfo from provider %s", provider.name)

    if not account_email:
        return RedirectResponse("/login?error=no_email")

    # Get the DB session from the DAO's session
    session = provider_dao.session

    # Find or create the user
    user_result = await session.execute(
        select(User).where(User.email == account_email),  # type: ignore[arg-type]
    )
    user = user_result.scalar_one_or_none()

    if user is None:
        if not provider.auto_register:
            return RedirectResponse("/login?error=registration_disabled")

        from fastapi_users.password import PasswordHelper  # noqa: PLC0415

        # Auto-register user
        user = User(
            id=uuid.uuid4(),
            email=account_email,
            hashed_password=PasswordHelper().hash(uuid.uuid4().hex),  # random unusable password
            is_active=True,
            is_superuser=False,
            is_verified=True,
        )
        session.add(user)
        await session.flush()

        # Assign default roles
        if provider.default_role_ids:
            rbac_dao = RbacDAO(session)
            role_ids = [uuid.UUID(rid) for rid in provider.default_role_ids if rid]
            if role_ids:
                await rbac_dao.set_user_roles(user.id, role_ids)
                await session.flush()

    # Upsert OAuth account record
    from sqlalchemy.dialects.postgresql import insert as pg_insert  # noqa: PLC0415

    await session.execute(
        pg_insert(OAuthAccount)
        .values(
            id=uuid.uuid4(),
            user_id=user.id,
            oauth_name=provider.name,
            access_token=access_token,
            expires_at=token.get("expires_at"),
            refresh_token=token.get("refresh_token"),
            account_id=str(account_id),
            account_email=account_email,
        )
        .on_conflict_do_update(
            index_elements=[OAuthAccount.id],
            set_={
                "access_token": access_token,
                "expires_at": token.get("expires_at"),
                "refresh_token": token.get("refresh_token"),
                "account_email": account_email,
            },
        ),
    )
    await session.flush()

    # Issue a JWT cookie and redirect to the admin dashboard
    strategy = get_jwt_strategy()
    jwt_token = await strategy.write_token(user)

    response = RedirectResponse("/admin/dashboard", status_code=302)
    response.set_cookie(
        key="fapiauth",
        value=jwt_token,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        max_age=86400 * 30,
        path="/",
    )
    return response
