"""DAO for auth provider management."""

import uuid

from fastapi import Depends
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.dependencies import get_db_session
from llm_port_backend.db.models.oauth import AuthProvider


class AuthProviderDAO:
    """Manages CRUD for admin-configured SSO providers."""

    def __init__(self, session: AsyncSession = Depends(get_db_session)) -> None:
        self.session = session

    async def list_providers(self) -> list[AuthProvider]:
        """Return all auth providers ordered by name."""
        result = await self.session.execute(
            select(AuthProvider).order_by(AuthProvider.name),
        )
        return list(result.scalars().all())

    async def list_enabled_providers(self) -> list[AuthProvider]:
        """Return only enabled providers (for the login page)."""
        result = await self.session.execute(
            select(AuthProvider).where(AuthProvider.enabled.is_(True)).order_by(AuthProvider.name),
        )
        return list(result.scalars().all())

    async def get_provider_by_id(self, provider_id: uuid.UUID) -> AuthProvider | None:
        """Look up a provider by ID."""
        result = await self.session.execute(
            select(AuthProvider).where(AuthProvider.id == provider_id),
        )
        return result.scalar_one_or_none()

    async def get_provider_by_name(self, name: str) -> AuthProvider | None:
        """Look up a provider by name."""
        result = await self.session.execute(
            select(AuthProvider).where(AuthProvider.name == name),
        )
        return result.scalar_one_or_none()

    async def create_provider(
        self,
        *,
        name: str,
        provider_type: str,
        client_id: str,
        client_secret_encrypted: str,
        discovery_url: str | None = None,
        authorize_url: str | None = None,
        token_url: str | None = None,
        userinfo_url: str | None = None,
        scopes: str = "openid email profile",
        enabled: bool = True,
        auto_register: bool = True,
        default_role_ids: list | None = None,
        group_mapping: dict | None = None,
    ) -> AuthProvider:
        """Create a new auth provider."""
        provider = AuthProvider(
            id=uuid.uuid4(),
            name=name,
            provider_type=provider_type,
            client_id=client_id,
            client_secret_encrypted=client_secret_encrypted,
            discovery_url=discovery_url,
            authorize_url=authorize_url,
            token_url=token_url,
            userinfo_url=userinfo_url,
            scopes=scopes,
            enabled=enabled,
            auto_register=auto_register,
            default_role_ids=default_role_ids or [],
            group_mapping=group_mapping or {},
        )
        self.session.add(provider)
        await self.session.flush()
        return provider

    async def update_provider(
        self,
        provider_id: uuid.UUID,
        **kwargs: object,
    ) -> AuthProvider | None:
        """Update a provider. Returns None if not found."""
        provider = await self.get_provider_by_id(provider_id)
        if provider is None:
            return None

        allowed_fields = {
            "name",
            "provider_type",
            "client_id",
            "client_secret_encrypted",
            "discovery_url",
            "authorize_url",
            "token_url",
            "userinfo_url",
            "scopes",
            "enabled",
            "auto_register",
            "default_role_ids",
            "group_mapping",
        }
        updates = {k: v for k, v in kwargs.items() if k in allowed_fields and v is not None}
        if updates:
            from sqlalchemy import func as sa_func  # noqa: PLC0415

            updates["updated_at"] = sa_func.now()
            await self.session.execute(
                update(AuthProvider).where(AuthProvider.id == provider_id).values(**updates),
            )
            await self.session.flush()
            # Re-fetch to get updated values
            result = await self.session.execute(
                select(AuthProvider).where(AuthProvider.id == provider_id),
            )
            return result.scalar_one()
        return provider

    async def delete_provider(self, provider_id: uuid.UUID) -> bool:
        """Delete a provider. Returns True if deleted."""
        provider = await self.get_provider_by_id(provider_id)
        if provider is None:
            return False
        await self.session.execute(
            delete(AuthProvider).where(AuthProvider.id == provider_id),
        )
        await self.session.flush()
        return True
