from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import jwt
from fastapi import Header

from llm_port_api.services.gateway.errors import GatewayError
from llm_port_api.settings import settings


@dataclass(slots=True, frozen=True)
class AuthContext:
    """Authenticated request context extracted from JWT."""

    user_id: str
    tenant_id: str
    raw_claims: dict[str, Any]


def _parse_bearer(auth_header: str | None) -> str:
    if not auth_header:
        raise GatewayError(
            status_code=401,
            message="Missing Authorization header.",
            code="missing_authorization",
        )
    if not auth_header.startswith("Bearer "):
        raise GatewayError(
            status_code=401,
            message="Authorization header must be a Bearer token.",
            code="invalid_authorization_header",
        )
    token = auth_header[len("Bearer ") :].strip()
    if not token:
        raise GatewayError(
            status_code=401,
            message="Bearer token is empty.",
            code="invalid_authorization_header",
        )
    return token


def verify_token(token: str) -> dict[str, Any]:
    """Verify JWT token and return claims."""
    if not settings.jwt_secret:
        raise GatewayError(
            status_code=500,
            message="JWT secret is not configured.",
            error_type="server_error",
            code="jwt_not_configured",
        )
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
    except jwt.PyJWTError as exc:
        raise GatewayError(
            status_code=401,
            message="Invalid JWT token.",
            code="invalid_token",
        ) from exc
    return claims


def get_auth_context_from_claims(claims: dict[str, Any]) -> AuthContext:
    """Extract user and tenant identifiers from verified claims."""
    user_id = str(claims.get("sub", "")).strip()
    if not user_id:
        raise GatewayError(
            status_code=401,
            message="JWT token does not contain subject (sub).",
            code="invalid_token_sub",
        )
    tenant_id = str(claims.get("tenant_id", "")).strip()
    if not tenant_id:
        raise GatewayError(
            status_code=403,
            message="JWT token does not include tenant_id claim.",
            code="missing_tenant_id",
        )
    return AuthContext(user_id=user_id, tenant_id=tenant_id, raw_claims=claims)


def get_auth_context(authorization: str | None = Header(default=None)) -> AuthContext:
    """FastAPI dependency that validates JWT bearer token."""
    token = _parse_bearer(authorization)
    claims = verify_token(token)
    return get_auth_context_from_claims(claims)
