# type: ignore
import logging
import uuid
from urllib.parse import urlencode

from fastapi import Depends, Request
from fastapi_users import BaseUserManager, FastAPIUsers, UUIDIDMixin, schemas
from fastapi_users.authentication import (
    AuthenticationBackend,
    BearerTransport,
    CookieTransport,
    JWTStrategy,
)
from fastapi_users.db import SQLAlchemyBaseUserTableUUID, SQLAlchemyUserDatabase
from pydantic import ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.db.base import Base
from llm_port_backend.db.dependencies import get_db_session
from llm_port_backend.settings import settings

log = logging.getLogger(__name__)


class User(SQLAlchemyBaseUserTableUUID, Base):
    """Represents a user entity."""


class UserRead(schemas.BaseUser[uuid.UUID]):
    """Represents a read command for a user."""

    email: str
    model_config = ConfigDict(from_attributes=True)


class UserCreate(schemas.BaseUserCreate):
    """Represents a create command for a user."""


class UserUpdate(schemas.BaseUserUpdate):
    """Represents an update command for a user."""


class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
    """Manages a user session and its tokens."""

    reset_password_token_secret = settings.users_secret
    verification_token_secret = settings.users_secret

    async def on_after_forgot_password(
        self,
        user: User,
        token: str,
        request: Request | None = None,
    ) -> None:
        """Enqueue password-reset email delivery via notification outbox."""
        try:
            from llm_port_backend.services.notifications import NotificationService  # noqa: PLC0415
        except Exception:
            log.exception("Failed to import NotificationService in forgot-password hook.")
            return

        session = getattr(self.user_db, "session", None)
        if not isinstance(session, AsyncSession):
            log.warning("Forgot-password hook skipped: SQLAlchemy session is unavailable.")
            return

        try:
            base = settings.mailer_frontend_base_url.rstrip("/")
            query = urlencode({"token": token})
            reset_url = f"{base}/reset-password?{query}"
            request_id = str(uuid.uuid4())
            if request is not None:
                request_id = (
                    request.headers.get("x-request-id")
                    or request.headers.get("x-correlation-id")
                    or request_id
                )
            to_name = user.email.split("@", 1)[0] if user.email else None
            service = NotificationService(session)
            await service.enqueue_password_reset(
                to_email=user.email,
                to_name=to_name,
                reset_url=reset_url,
                request_id=request_id,
            )
        except Exception:
            # Keep forgot-password response enumeration-safe even if enqueue fails.
            log.exception("Failed to enqueue forgot-password notification.")


async def get_user_db(
    session: AsyncSession = Depends(get_db_session),
) -> SQLAlchemyUserDatabase:
    """
    Yield a SQLAlchemyUserDatabase instance.

    :param session: asynchronous SQLAlchemy session.
    :yields: instance of SQLAlchemyUserDatabase.
    """
    yield SQLAlchemyUserDatabase(session, User)


async def get_user_manager(
    user_db: SQLAlchemyUserDatabase = Depends(get_user_db),
) -> UserManager:
    """
    Yield a UserManager instance.

    :param user_db: SQLAlchemy user db instance
    :yields: an instance of UserManager.
    """
    yield UserManager(user_db)


def get_jwt_strategy() -> JWTStrategy:
    """
    Return a JWTStrategy in order to instantiate it dynamically.

    :returns: instance of JWTStrategy with provided settings.
    """
    return JWTStrategy(secret=settings.users_secret, lifetime_seconds=None)


bearer_transport = BearerTransport(tokenUrl="auth/jwt/login")
auth_jwt = AuthenticationBackend(
    name="jwt",
    transport=bearer_transport,
    get_strategy=get_jwt_strategy,
)
cookie_transport = CookieTransport(
    cookie_name="fapiauth",
    cookie_secure=settings.environment != "dev",
)
auth_cookie = AuthenticationBackend(name="cookie", transport=cookie_transport, get_strategy=get_jwt_strategy)

backends = [
    auth_cookie,
    auth_jwt,
]

api_users = FastAPIUsers[User, uuid.UUID](get_user_manager, backends)

current_active_user = api_users.current_user(active=True)
