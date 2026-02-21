from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from airgap_backend.db.models.users import (
    UserCreate,  # type: ignore
    UserRead,  # type: ignore
    UserUpdate,  # type: ignore
    api_users,  # type: ignore
    auth_cookie,  # type: ignore
    auth_jwt,  # type: ignore
    get_jwt_strategy,  # type: ignore
)
from airgap_backend.settings import settings

router = APIRouter()

router.include_router(
    api_users.get_register_router(UserRead, UserCreate),
    prefix="/auth",
    tags=["auth"],
)

router.include_router(
    api_users.get_reset_password_router(),
    prefix="/auth",
    tags=["auth"],
)

router.include_router(
    api_users.get_verify_router(UserRead),
    prefix="/auth",
    tags=["auth"],
)

router.include_router(
    api_users.get_users_router(UserRead, UserUpdate),
    prefix="/users",
    tags=["users"],
)
router.include_router(api_users.get_auth_router(auth_jwt), prefix="/auth/jwt", tags=["auth"])
router.include_router(api_users.get_auth_router(auth_cookie), prefix="/auth/cookie", tags=["auth"])


@router.post("/auth/dev-login", tags=["auth"])
async def dev_login(request: Request) -> Response:
    """One-click dev login — sets a cookie token for admin@localhost.

    Only available when ENVIRONMENT=dev.
    """
    if settings.environment != "dev":
        return JSONResponse({"detail": "Not available"}, status_code=404)

    from sqlalchemy import select  # noqa: PLC0415

    from airgap_backend.db.models.users import User  # noqa: PLC0415

    async with request.app.state.db_session_factory() as session:
        result = await session.execute(
            select(User).where(User.email == "admin@localhost"),  # type: ignore[arg-type]
        )
        user = result.scalars().first()
        if user is None:
            return JSONResponse(
                {"detail": "Dev user not seeded. Restart the backend."},
                status_code=500,
            )

    strategy = get_jwt_strategy()
    token = await strategy.write_token(user)

    response = JSONResponse(
        {"token": token, "email": user.email},
    )
    # Set both cookie (for browser) and return token (for programmatic use)
    response.set_cookie(
        key="fapiauth",
        value=token,
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=86400 * 30,
        path="/",
    )
    return response
