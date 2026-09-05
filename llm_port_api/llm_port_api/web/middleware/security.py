"""HTTP-level security middleware (HSTS, headers, host allowlist, CORS).

The middleware here is a pluggable bundle wired into the FastAPI app
in :func:`llm_port_api.web.lifespan.lifespan_setup` between
``app.middleware_stack = None`` and ``app.build_middleware_stack()``.

Components
----------
* :class:`SecurityHeadersMiddleware` — sets HSTS,
  ``X-Content-Type-Options``, ``X-Frame-Options``, ``Referrer-Policy``,
  ``X-XSS-Protection``.
* :class:`HTTPSRedirectMiddleware` — when strict TLS mode is on and the
  request arrived over HTTP, returns a 308 to the HTTPS variant.
* Standard ``TrustedHostMiddleware`` and ``CORSMiddleware`` from
  Starlette are wired by :func:`register_security_middleware` when their
  respective settings are configured.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response
from starlette.types import ASGIApp

logger = logging.getLogger(__name__)


def _split_csv(raw: str) -> list[str]:
    return [item.strip() for item in raw.split(",") if item.strip()]


class SecurityHeadersMiddleware:
    """Apply common HTTP response security headers.

    Implemented as a raw ASGI middleware so we can mutate ``send`` —
    cheaper than subclassing :class:`BaseHTTPMiddleware`.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        hsts_max_age: int = 0,
        hsts_include_subdomains: bool = False,
        hsts_preload: bool = False,
    ) -> None:
        self.app = app
        self._hsts_max_age = hsts_max_age
        self._hsts_include_subdomains = hsts_include_subdomains
        self._hsts_preload = hsts_preload

    def _hsts_value(self) -> str | None:
        if self._hsts_max_age <= 0:
            return None
        parts = [f"max-age={self._hsts_max_age}"]
        if self._hsts_include_subdomains:
            parts.append("includeSubDomains")
        if self._hsts_preload:
            parts.append("preload")
        return "; ".join(parts)

    async def __call__(self, scope, receive, send):  # type: ignore[no-untyped-def]
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        hsts = self._hsts_value()

        async def _send(message):  # type: ignore[no-untyped-def]
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                _set_header(headers, b"x-content-type-options", b"nosniff")
                _set_header(headers, b"x-frame-options", b"DENY")
                _set_header(
                    headers,
                    b"referrer-policy",
                    b"strict-origin-when-cross-origin",
                )
                _set_header(headers, b"x-xss-protection", b"0")
                if hsts is not None and scope.get("scheme") == "https":
                    _set_header(headers, b"strict-transport-security", hsts.encode("ascii"))
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, _send)


def _set_header(headers: list, name: bytes, value: bytes) -> None:
    """Replace or append a header in a Starlette raw header list."""
    name_lower = name.lower()
    for i, (existing_name, _existing_value) in enumerate(headers):
        if existing_name.lower() == name_lower:
            headers[i] = (name, value)
            return
    headers.append((name, value))


class HTTPSRedirectMiddleware:
    """Redirect plaintext HTTP to HTTPS when strict TLS mode is enabled."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):  # type: ignore[no-untyped-def]
        if scope["type"] != "http" or scope.get("scheme") == "https":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        url = request.url.replace(scheme="https")
        response: Response = RedirectResponse(str(url), status_code=308)
        await response(scope, receive, send)


def register_security_middleware(app: FastAPI, settings) -> None:  # type: ignore[no-untyped-def]
    """Wire the security middleware bundle onto *app*.

    Must be called between ``app.middleware_stack = None`` and
    ``app.middleware_stack = app.build_middleware_stack()``.
    """
    # Always-on security headers.
    app.add_middleware(
        SecurityHeadersMiddleware,
        hsts_max_age=settings.tls_edge_hsts_max_age,
        hsts_include_subdomains=settings.tls_edge_hsts_include_subdomains,
        hsts_preload=settings.tls_edge_hsts_preload,
    )

    # Strict mode: refuse plaintext, redirect to HTTPS.
    if settings.tls_edge_strict:
        app.add_middleware(HTTPSRedirectMiddleware)

    # Trusted-Host allowlist (mitigates Host-header attacks).
    allowed = settings.tls_edge_allowed_hosts.strip()
    if allowed and allowed != "*":
        app.add_middleware(
            TrustedHostMiddleware,
            allowed_hosts=_split_csv(allowed),
        )

    # CORS.
    cors_origins = _split_csv(settings.tls_edge_cors_origins)
    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
