"""Reusable TLS configuration helpers for backing-store connections.

Mirror of :mod:`llm_port_backend.services.tls.connection_ssl`. Kept in
each package because the two services have no shared Python library.
"""

from __future__ import annotations

import enum
import ssl
from typing import Any

from yarl import URL


class SSLMode(str, enum.Enum):
    """asyncpg-style SSL mode."""

    DISABLE = "disable"
    PREFER = "prefer"
    REQUIRE = "require"
    VERIFY_CA = "verify-ca"
    VERIFY_FULL = "verify-full"


def _ctx_for_mode(mode: SSLMode, ca_bundle: str | None) -> ssl.SSLContext:
    ctx = ssl.create_default_context(cafile=ca_bundle)
    if mode == SSLMode.VERIFY_FULL:
        ctx.check_hostname = True
        ctx.verify_mode = ssl.CERT_REQUIRED
    elif mode == SSLMode.VERIFY_CA:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_REQUIRED
    else:  # REQUIRE
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


def build_asyncpg_ssl(mode: str | SSLMode, ca_bundle: str | None = None) -> Any:
    """Translate an SSL mode into asyncpg's ``ssl=`` connect-arg value."""
    m = SSLMode(mode) if not isinstance(mode, SSLMode) else mode
    if m == SSLMode.DISABLE:
        return False
    if m == SSLMode.PREFER:
        return "prefer"
    return _ctx_for_mode(m, ca_bundle)


def build_httpx_verify(verify: bool, ca_bundle: str | None) -> Any:
    """Return the value for ``httpx.AsyncClient(verify=...)``."""
    if not verify:
        return False
    if ca_bundle:
        return ca_bundle
    return True


def build_redis_ssl_kwargs(enabled: bool, ca_bundle: str | None) -> dict[str, Any]:
    """Return kwargs accepted by ``redis.asyncio.ConnectionPool.from_url``."""
    if not enabled:
        return {}
    kwargs: dict[str, Any] = {"ssl": True, "ssl_cert_reqs": "required"}
    if ca_bundle:
        kwargs["ssl_ca_certs"] = ca_bundle
    return kwargs


def rewrite_amqp_url_for_tls(url: str, enabled: bool) -> str:
    """Upgrade ``amqp://`` to ``amqps://`` when ``enabled`` is True."""
    if not enabled:
        return url
    parsed = URL(url)
    if parsed.scheme == "amqps":
        return url
    if parsed.scheme == "amqp":
        return str(parsed.with_scheme("amqps"))
    return url
