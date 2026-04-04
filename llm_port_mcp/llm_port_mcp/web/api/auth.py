"""Shared auth dependencies for the MCP service."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from llm_port_mcp.settings import settings

# ── JWT-based auth (for admin endpoints) ──────────────────────────────


@dataclass(frozen=True)
class AuthContext:
    """Authenticated request context extracted from JWT."""

    user_id: str
    tenant_id: str
    raw_claims: dict[str, Any]


_bearer_scheme = HTTPBearer(auto_error=False)


def _verify_token(token: str) -> dict[str, Any]:
    if not settings.jwt_secret:
        raise HTTPException(status_code=500, detail="JWT secret not configured.")
    try:
        decode_opts: dict[str, Any] = {
            "algorithms": [settings.jwt_algorithm],
        }
        if settings.jwt_audience:
            decode_opts["audience"] = settings.jwt_audience
        else:
            decode_opts["options"] = {"verify_aud": False}
        if settings.jwt_issuer:
            decode_opts["issuer"] = settings.jwt_issuer
        return jwt.decode(token, settings.jwt_secret, **decode_opts)  # type: ignore[no-any-return]
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid JWT token.") from exc


async def get_auth_context(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> AuthContext:
    """FastAPI dependency — validates JWT or service bearer token.

    When the Backend admin proxy forwards a request it sends the
    shared ``service_token`` instead of a user JWT.  We accept that
    as a trusted internal call and synthesize an AuthContext.
    """
    token = credentials.credentials if credentials else None
    if not token:
        raise HTTPException(status_code=401, detail="Missing Authorization header.")

    # Allow trusted service-token from the backend proxy.
    if settings.service_token and token == settings.service_token:
        return AuthContext(
            user_id="service",
            tenant_id="default",
            raw_claims={},
        )

    claims = _verify_token(token)
    user_id = str(claims.get("sub", "")).strip()
    if not user_id:
        raise HTTPException(status_code=401, detail="JWT missing subject.")
    tenant_id = str(claims.get("tenant_id", "default")).strip() or "default"
    return AuthContext(user_id=user_id, tenant_id=tenant_id, raw_claims=claims)


# ── Service-token auth (for internal endpoints) ──────────────────────


async def verify_service_token(request: Request) -> None:
    """FastAPI dependency — validates internal service bearer token."""
    if not settings.service_token:
        return  # No service token configured => open (dev mode)
    auth_header = request.headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing service token.")
    token = auth_header[7:]
    if token != settings.service_token:
        raise HTTPException(status_code=403, detail="Invalid service token.")
