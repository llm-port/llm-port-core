"""Admin auth-provider management and OAuth callback endpoints.

When the external auth module is enabled (``settings.auth_enabled``),
provider CRUD calls are forwarded to the ``llm_port_auth`` micro-
service.  OAuth ``authorize`` redirects to the auth module which
handles the full IdP exchange and sends signed ``OAuthUserClaims``
back to the ``/external-callback`` endpoint here, where we
find-or-create the user and issue a JWT cookie.

When the auth module is **disabled**, the routes still exist but
return empty lists / 404 — keeping the API shape stable so the front-
end doesn't break.
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

import httpx
import jwt
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from starlette import status

from llm_port_backend.db.dao.auth_provider_dao import AuthProviderDAO
from llm_port_backend.db.models.oauth import AuthProvider, OAuthAccount
from llm_port_backend.db.models.users import User, get_jwt_strategy
from llm_port_backend.settings import settings
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
#  Proxy helpers
# ─────────────────────────────────────────────────────────────────────────────

_AUTH_API_TIMEOUT = 15.0

_auth_proxy_client: httpx.AsyncClient | None = None


def _get_auth_client() -> httpx.AsyncClient:
    global _auth_proxy_client
    if _auth_proxy_client is None:
        _auth_proxy_client = httpx.AsyncClient(timeout=_AUTH_API_TIMEOUT)
    return _auth_proxy_client


def _auth_base() -> str:
    """Return the base URL for the auth module's providers API."""
    return f"{settings.auth_service_url.rstrip('/')}/api/providers"


async def _proxy_get(path: str) -> httpx.Response:
    return await _get_auth_client().get(f"{_auth_base()}{path}")


async def _proxy_post(path: str, payload: dict) -> httpx.Response:
    return await _get_auth_client().post(f"{_auth_base()}{path}", json=payload)


async def _proxy_patch(path: str, payload: dict) -> httpx.Response:
    return await _get_auth_client().patch(f"{_auth_base()}{path}", json=payload)


async def _proxy_delete(path: str) -> httpx.Response:
    return await _get_auth_client().delete(f"{_auth_base()}{path}")


def _forward_or_raise(resp: httpx.Response):
    """Raise an HTTPException mirroring the upstream status on error."""
    if resp.status_code >= 400:  # noqa: PLR2004
        try:
            detail = resp.json().get("detail", resp.text)
        except Exception:
            detail = resp.text
        raise HTTPException(status_code=resp.status_code, detail=detail)


# ─────────────────────────────────────────────────────────────────────────────
#  Local-mode helpers (kept for fallback / CE mode)
# ─────────────────────────────────────────────────────────────────────────────


def _get_fernet():
    """Return a Fernet instance derived from the users_secret setting."""
    import base64  # noqa: PLC0415
    import hashlib  # noqa: PLC0415

    from cryptography.fernet import Fernet  # noqa: PLC0415

    key_bytes = hashlib.sha256(settings.users_secret.encode()).digest()
    fernet_key = base64.urlsafe_b64encode(key_bytes)
    return Fernet(fernet_key)


def _encrypt_secret(plaintext: str) -> str:
    return _get_fernet().encrypt(plaintext.encode()).decode()


def _decrypt_secret(ciphertext: str) -> str:
    return _get_fernet().decrypt(ciphertext.encode()).decode()


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
#  Public endpoint: enabled providers (login page)
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "/public",
    response_model=list[AuthProviderPublicDTO],
    name="list_enabled_providers",
)
async def list_enabled_providers(
    provider_dao: AuthProviderDAO = Depends(),
) -> list[AuthProviderPublicDTO]:
    """List enabled providers — unauthenticated (login page)."""
    if settings.auth_enabled:
        resp = await _proxy_get("/public")
        _forward_or_raise(resp)
        return resp.json()
    providers = await provider_dao.list_enabled_providers()
    return [
        AuthProviderPublicDTO(id=p.id, name=p.name, provider_type=p.provider_type)
        for p in providers
    ]


# ─────────────────────────────────────────────────────────────────────────────
#  Admin CRUD
# ─────────────────────────────────────────────────────────────────────────────


@router.get("/", response_model=list[AuthProviderDTO], name="list_auth_providers")
async def list_auth_providers(
    _user: Annotated[User, Depends(require_superuser)] = None,  # type: ignore[assignment]
    provider_dao: AuthProviderDAO = Depends(),
) -> list[AuthProviderDTO]:
    """List all configured auth providers."""
    if settings.auth_enabled:
        resp = await _proxy_get("/")
        _forward_or_raise(resp)
        return resp.json()
    providers = await provider_dao.list_providers()
    return [_provider_to_dto(p) for p in providers]


@router.get("/{provider_id}", response_model=AuthProviderDTO, name="get_auth_provider")
async def get_auth_provider(
    provider_id: uuid.UUID,
    _user: Annotated[User, Depends(require_superuser)] = None,  # type: ignore[assignment]
    provider_dao: AuthProviderDAO = Depends(),
) -> AuthProviderDTO:
    """Get a single auth provider by ID."""
    if settings.auth_enabled:
        resp = await _proxy_get(f"/{provider_id}")
        _forward_or_raise(resp)
        return resp.json()
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
    if settings.auth_enabled:
        resp = await _proxy_post("/", payload.model_dump())
        _forward_or_raise(resp)
        return resp.json()
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
    if settings.auth_enabled:
        resp = await _proxy_patch(
            f"/{provider_id}",
            payload.model_dump(exclude_none=True),
        )
        _forward_or_raise(resp)
        return resp.json()
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
    if settings.auth_enabled:
        resp = await _proxy_delete(f"/{provider_id}")
        _forward_or_raise(resp)
        return
    existing = await provider_dao.get_provider_by_id(provider_id)
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Provider not found")
    await provider_dao.delete_provider(provider_id)


# ─────────────────────────────────────────────────────────────────────────────
#  OAuth flow endpoints
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
    """Redirect the user to the external IdP for login.

    When the auth module is enabled, redirect to its authorize endpoint
    so it handles the full OAuth exchange.
    """
    if settings.auth_enabled:
        # Redirect the browser directly to the auth module's authorize
        redirect_url = f"{_auth_base()}/{provider_id}/authorize"
        return RedirectResponse(redirect_url)

    # ── Local mode (no auth module) ───────────────────────────────────
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
    """Handle the OAuth callback (local mode only).

    When the auth module is enabled, this endpoint is never hit — the
    IdP redirects to the auth module's callback which then calls
    ``external-callback`` below.
    """
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

    # Reuse the shared _process_oauth_login helper
    return await _process_oauth_login(
        session=provider_dao.session,
        request=request,
        provider_name=provider.name,
        account_id=str(account_id),
        account_email=account_email,
        access_token=access_token,
        expires_at=token.get("expires_at"),
        refresh_token=token.get("refresh_token"),
        auto_register=provider.auto_register,
        default_role_ids=[str(rid) for rid in (provider.default_role_ids or [])],
    )


# ─────────────────────────────────────────────────────────────────────────────
#  External callback — receives signed claims from the auth module
# ─────────────────────────────────────────────────────────────────────────────


@router.get(
    "/external-callback",
    name="oauth_external_callback",
)
async def oauth_external_callback(
    token: str,
    request: Request,
    provider_dao: AuthProviderDAO = Depends(),
) -> RedirectResponse:
    """Receive signed OAuthUserClaims from the auth module.

    The auth module performs the full OAuth exchange and then redirects
    the user's browser here with a short-lived JWT.  We verify the
    signature, find-or-create the user, and issue a session cookie.
    """
    # Verify the signed token
    try:
        claims = jwt.decode(
            token,
            settings.users_secret,
            algorithms=["HS256"],
            issuer="llm-port-auth",
        )
    except jwt.ExpiredSignatureError:
        logger.warning("External callback token expired")
        return RedirectResponse("/login?error=token_expired")
    except jwt.InvalidTokenError:
        logger.warning("External callback token invalid")
        return RedirectResponse("/login?error=invalid_token")

    return await _process_oauth_login(
        session=provider_dao.session,
        request=request,
        provider_name=claims["provider_name"],
        account_id=claims.get("account_id", ""),
        account_email=claims["account_email"],
        access_token=claims.get("access_token", ""),
        expires_at=claims.get("expires_at"),
        refresh_token=claims.get("refresh_token"),
        auto_register=claims.get("auto_register", True),
        default_role_ids=claims.get("default_role_ids", []),
    )


# ─────────────────────────────────────────────────────────────────────────────
#  Shared login logic (local mode + external callback)
# ─────────────────────────────────────────────────────────────────────────────


async def _process_oauth_login(
    *,
    session,
    request: Request,
    provider_name: str,
    account_id: str,
    account_email: str,
    access_token: str,
    expires_at: int | None,
    refresh_token: str | None,
    auto_register: bool,
    default_role_ids: list[str],
) -> RedirectResponse:
    """Find-or-create a user from OAuth claims and issue a JWT cookie."""
    from sqlalchemy import select  # noqa: PLC0415
    from sqlalchemy.dialects.postgresql import insert as pg_insert  # noqa: PLC0415

    from llm_port_backend.db.dao.rbac_dao import RbacDAO  # noqa: PLC0415

    # Find or create the user
    user_result = await session.execute(
        select(User).where(User.email == account_email),  # type: ignore[arg-type]
    )
    user = user_result.scalar_one_or_none()

    if user is None:
        if not auto_register:
            return RedirectResponse("/login?error=registration_disabled")

        from fastapi_users.password import PasswordHelper  # noqa: PLC0415

        user = User(
            id=uuid.uuid4(),
            email=account_email,
            hashed_password=PasswordHelper().hash(uuid.uuid4().hex),
            is_active=True,
            is_superuser=False,
            is_verified=True,
        )
        session.add(user)
        await session.flush()

        # Assign default roles
        if default_role_ids:
            rbac_dao = RbacDAO(session)
            role_ids = [uuid.UUID(rid) for rid in default_role_ids if rid]
            if role_ids:
                await rbac_dao.set_user_roles(user.id, role_ids)
                await session.flush()
        else:
            rbac_dao = RbacDAO(session)
            default_role = await rbac_dao.get_role_by_name("default_user")
            if default_role is not None:
                await rbac_dao.assign_role(user.id, default_role.id)
                await session.flush()

    # Upsert OAuth account record
    await session.execute(
        pg_insert(OAuthAccount)
        .values(
            id=uuid.uuid4(),
            user_id=user.id,
            oauth_name=provider_name,
            access_token=access_token,
            expires_at=expires_at,
            refresh_token=refresh_token,
            account_id=str(account_id),
            account_email=account_email,
        )
        .on_conflict_do_update(
            index_elements=[OAuthAccount.id],
            set_={
                "access_token": access_token,
                "expires_at": expires_at,
                "refresh_token": refresh_token,
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
